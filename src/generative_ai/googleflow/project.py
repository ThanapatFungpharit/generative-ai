from __future__ import annotations

import asyncio
import mimetypes
import re
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Self, TypedDict, TypeIs, override
from uuid import uuid4

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from web_page.exceptions import ActionFailed
from web_page.utils import ESCAPES, fill, is_str_list

from generative_ai.googleflow.constants import CONFIGS, SELECTORS, SITE, URLS
from generative_ai.googleflow.exceptions import (
    GenerationError,
    GenerationSettingsError,
    GoogleFlowError,
    MediaActionError,
    MediaFileReadError,
    MediaNotFoundError,
    MediaUploadError,
    ProjectClosedError,
    ProjectCloseError,
    ProjectCreationError,
    ProjectDeletionError,
    ProjectNotFoundError,
    ProjectOpenError,
    ProjectRenameError,
    SceneCreationError,
)
from generative_ai.googleflow.models import (
    GenerationJob,
    GenerationMode,
    GenerationSettings,
    MediaItem,
    VideoInput,
)
from generative_ai.googleflow.utils import (
    edit_id_from_url,
    is_not_found_url,
    is_project_url,
    normalize_media_id,
    normalize_project_magic,
    project_magic_from_url,
    scene_id_from_url,
)
from generative_ai.site import (
    SitePage,
    UnexpectedPageStateError,
    close_page_and_report_failure,
    note_cleanup_failure,
    page_required,
    text_result,
    texts_result,
)
from generative_ai.site.guards import is_list, is_str_keyed_mapping
from generative_ai.site.page import CLEANUP_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet

    from web_page import FilePayload, WebPage
    from web_page.types import ParamName

    from generative_ai.googleflow.types import (
        AspectRatio,
        FrameSlot,
        MediaAction,
        MediaId,
        MediaKind,
        MediaMenuItem,
        ModelLabel,
        OutputCount,
        OutputKind,
        ProjectMagic,
        PromptText,
        QualityLabel,
        SceneId,
        SettingField,
        SettingLabel,
        TileLabel,
        UploadToken,
        VideoDuration,
        VideoResolution,
    )
    from generative_ai.site import BrowserContext, BrowserPage
    from generative_ai.site.types import RawJson, Seconds, Url


@dataclass(frozen=True, slots=True)
class _Tile:
    """A media grid tile plus every label it exposes, for matching uploads."""

    item: MediaItem
    labels: tuple[TileLabel, ...]

    @property
    def finished_id(self) -> MediaId | None:
        """The media ID once Flow has finished processing this tile."""
        return None if self.item.pending else self.item.media_id


@dataclass(slots=True)
class _JobState:
    """What a started generation is attributed against."""

    baseline: frozenset[MediaId]
    """Media already present before submission, plus unrelated uploads."""
    found: dict[MediaId, None] = field(default_factory=dict)
    """Matched outputs retained across polling snapshots and retryable waits."""
    outputs: tuple[MediaId, ...] | None = None
    """``None`` until the job's tiles have been attributed."""


class GoogleFlowProject(SitePage):
    """A domain facade for one Google Flow project browser page.

    Use :meth:`open` or :meth:`new` to construct an instance. Linear UI flows
    are declared as ``web_page`` manifests under ``manifests/``; this class
    drives them and handles what manifests cannot express (project- and
    media-scoped locators, file choosers, branching, and polling).
    """

    __slots__ = (
        "_flow_messages",
        "_jobs",
        "_media_editor_ids",
        "_project_magic",
        "_project_url",
    )

    def __init__(
        self, project_magic: str, page: BrowserPage, web_page: WebPage
    ) -> None:
        super().__init__(page, web_page)
        self._project_magic = normalize_project_magic(project_magic)
        self._project_url = f"{URLS.project}{self._project_magic}"
        self._jobs: dict[GenerationJob, _JobState] = {}
        # Video content IDs and editor IDs differ. Thumbnails lose the content
        # ID after a reload, but opening a tile still reveals its editor ID.
        self._media_editor_ids: dict[MediaId, MediaId] = {}
        # Snackbars seen while polling. They vanish after a few seconds, long
        # before a wait times out; failures report them, then clear them.
        self._flow_messages: dict[str, None] = {}

    @property
    def project_magic(self) -> ProjectMagic:
        """The stable identifier contained in this project's Flow URL."""
        return self._project_magic

    @property
    def project_url(self) -> Url:
        """The canonical browser URL for this project."""
        return self._project_url

    @classmethod
    async def open(cls, project_magic: str, context: BrowserContext) -> Self:
        """Open an existing project by its Flow identifier.

        Raises:
            ProjectNotFoundError: Flow redirects the project URL to its 404 page.
            ProjectOpenError: Navigation fails, reaches an unexpected URL, or
                does not produce a usable project workspace.
        """
        project_magic = normalize_project_magic(project_magic)
        page: BrowserPage | None = None

        try:
            page = await context.new_page()
            project = cls(project_magic, page, await SITE.attach(page))
            await project._web_page.run(
                "open_project", {"project_url": project.project_url}
            )
            _ensure_opened(page.url, project.project_magic)
            await project._web_page.run("wait_for_workspace")
            _ensure_opened(page.url, project.project_magic)
        except PlaywrightError as error:
            failure = ProjectOpenError(project_magic)
            if page is not None:
                was_not_found = is_not_found_url(page.url)
                cleanup_error = await close_page_and_report_failure(page)
                if was_not_found:
                    failure = ProjectNotFoundError(project_magic)
                note_cleanup_failure(failure, cleanup_error)
            raise failure from error
        except BaseException as error:
            if page is not None:
                cleanup_error = await close_page_and_report_failure(page)
                note_cleanup_failure(error, cleanup_error)
            raise
        return project

    @classmethod
    async def new(cls, context: BrowserContext) -> Self:
        """Create a new Google Flow project in ``context``.

        Raises:
            ProjectCreationError: The creation controls or resulting project
                URL cannot be resolved.
        """
        page: BrowserPage | None = None

        try:
            page = await context.new_page()
            web_page = await SITE.attach(page)
            await web_page.run("new_project", {"base_url": URLS.base})
            return cls(project_magic_from_url(page.url), page, web_page)
        except PlaywrightTimeoutError as error:
            failure = ProjectCreationError(
                "Timed out while waiting for Google Flow to create a project."
            )
            if page is not None:
                _note_created_project(failure, page.url)
                cleanup_error = await close_page_and_report_failure(page)
                note_cleanup_failure(failure, cleanup_error)
            raise failure from error
        except PlaywrightError as error:
            failure = ProjectCreationError("Google Flow could not create a project.")
            if page is not None:
                _note_created_project(failure, page.url)
                cleanup_error = await close_page_and_report_failure(page)
                note_cleanup_failure(failure, cleanup_error)
            raise failure from error
        except BaseException as error:
            if page is not None:
                cleanup_error = await close_page_and_report_failure(page)
                note_cleanup_failure(error, cleanup_error)
            raise

    @page_required
    async def close(self) -> None:
        """Close the project page and make this facade unusable.

        Raises:
            ProjectCloseError: Playwright cannot verify that the page closed.
        """
        page = self._require_page()
        try:
            await page.close()
        except PlaywrightError as error:
            raise ProjectCloseError(self.project_magic) from error
        if not page.is_closed():
            raise ProjectCloseError(self.project_magic)
        self._page = None

    @page_required
    async def rename(self, new_name: str) -> None:
        """Rename this project from its workspace header.

        Leading and trailing whitespace is removed from ``new_name``. Success
        means the header shows the new name as the saved project name.

        Raises:
            ValueError: ``new_name`` is empty or contains only whitespace.
            ProjectRenameError: Google Flow does not complete the rename.
        """
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("new_name must not be empty or whitespace")

        try:
            await self._ensure_on_workspace()
            await self._web_page.run("rename_project", {"new_name": new_name})
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = ProjectRenameError(self.project_magic)
            await self._restore_project_page_after_failure(failure)
            raise failure from error

    @page_required
    async def delete(self) -> None:
        """Permanently delete this project and close its browser page.

        On success this facade is closed and cannot be reused.

        Raises:
            ProjectDeletionError: Google Flow does not complete the deletion.
                If confirmation was submitted but the 404 postcondition cannot
                be verified, the exception notes that remote state is unknown.
            ProjectCloseError: Deletion was verified, but its page could not be
                closed afterward.
        """
        page = self._require_page()
        confirmation_attempted = False
        try:
            await self._ensure_on_workspace()
            await self._web_page.run("open_delete_project_dialog")
            confirmation_attempted = True
            await self._web_page.run("confirm_delete_project")
            await self._verify_deletion(page)
        except asyncio.CancelledError as error:
            if confirmation_attempted:
                error.add_note(
                    "Deletion confirmation was attempted; the remote project "
                    "state is unknown. The project facade has been closed."
                )
                self._page = None
                cleanup_error = await close_page_and_report_failure(page)
                note_cleanup_failure(error, cleanup_error)
            else:
                await self._restore_project_page_after_failure(error)
            raise
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = ProjectDeletionError(self.project_magic)
            if confirmation_attempted:
                failure.add_note(
                    "Deletion confirmation was submitted, but the remote "
                    "deletion could not be verified; the project state is unknown."
                )
                self._page = None
                cleanup_error = await close_page_and_report_failure(page)
                note_cleanup_failure(failure, cleanup_error)
            else:
                await self._restore_project_page_after_failure(failure)
            raise failure from error

        # The remote deletion has been verified. The facade must not be reused.
        self._page = None
        cleanup_error = await close_page_and_report_failure(page)
        if cleanup_error is not None:
            failure = ProjectCloseError(self.project_magic)
            failure.add_note(
                "The remote project deletion was verified before page cleanup failed."
            )
            raise failure from cleanup_error

    @page_required
    async def upload_media(self, files: Iterable[Path]) -> Mapping[Path, MediaId]:
        """Upload media and return a mapping from each path to its Flow media ID.

        Each file is uploaded under a unique generated name. Flow labels the
        finished tile with that name and assigns its own media ID, which is
        what the other media operations accept. If Flow drops the name, a
        new tile is still matched when it is the only unidentified upload of
        its kind (image or video). File contents are read
        concurrently in worker threads. Duplicate paths are rejected because a
        mapping cannot represent them without data loss.

        Raises:
            ValueError: ``files`` contains the same path more than once.
            MediaFileReadError: A file cannot be read.
            MediaUploadError: Flow does not finish every upload in the allotted
                time, or the workspace cannot be restored to a known state
                after the upload.
        """
        media_files = tuple(files)
        if len(set(media_files)) != len(media_files):
            raise ValueError("files must not contain duplicate paths")
        if not media_files:
            no_uploads: dict[Path, MediaId] = {}
            return no_uploads

        built_payloads = await asyncio.gather(
            *(_build_file_payload(file) for file in media_files)
        )
        file_by_token: dict[UploadToken, Path] = {}
        kind_by_token: dict[UploadToken, MediaKind] = {}
        payloads: list[str | FilePayload] = []
        for file, token, payload in built_payloads:
            file_by_token[token] = file
            kind_by_token[token] = payload["mimeType"].partition("/")[0]
            payloads.append(payload)

        total_mib = (
            sum(len(payload["buffer"]) for _, _, payload in built_payloads) / 2**20
        )
        timeout = (
            CONFIGS.UPLOAD_TIMEOUT_SECONDS + total_mib * CONFIGS.UPLOAD_SECONDS_PER_MIB
        )
        operation_error: BaseException | None = None
        self._flow_messages.clear()
        try:
            await self._ensure_on_workspace()
            await self._web_page.run("wait_for_workspace")
            known_ids = await self._known_media_ids()
            await self._web_page.run("upload_media", {"files": payloads})
            media_id_by_token = await self._wait_for_uploaded_media(
                kind_by_token, known_ids, timeout
            )
            video_ids = {
                media_id_by_token[token]
                for token, kind in kind_by_token.items()
                if kind == "video"
            }
            if video_ids:
                for index, tile in enumerate(await self._snapshot_media()):
                    if tile.finished_id in video_ids:
                        editor_id, _ = await self._open_tile(index)
                        self._media_editor_ids[tile.finished_id] = editor_id
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = MediaUploadError("Google Flow could not upload the media.")
            operation_error = failure
            raise failure from error
        except BaseException as error:
            operation_error = error
            raise
        finally:
            cleanup_error = await self._close_overlays_and_report_failure()
            if cleanup_error is not None:
                if operation_error is not None:
                    operation_error.add_note(
                        "Upload cleanup also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                else:
                    raise MediaUploadError(
                        "The media upload completed, but the workspace could not "
                        "be restored to a known state."
                    ) from cleanup_error

        # Uploads may complete while generation jobs are outstanding. They must
        # never be considered generation results, even if they are newest.
        for state in self._jobs.values():
            state.baseline |= frozenset(media_id_by_token.values())
        return {
            file_by_token[token]: media_id_by_token[token] for token in file_by_token
        }

    @page_required
    async def list_media(self) -> tuple[MediaItem, ...]:
        """Return the tiles currently rendered in the project's media grid.

        The grid is virtualised, so very large projects may only report the
        tiles near the top (newest first by default).
        """
        try:
            await self._ensure_on_workspace()
            await self._web_page.run("wait_for_workspace")
            return tuple(tile.item for tile in await self._snapshot_media())
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = GoogleFlowError("Google Flow media could not be listed.")
            await self._restore_project_page_after_failure(failure)
            raise failure from error

    @page_required
    async def get_generation_settings(self) -> GenerationSettings:
        """Read the prompt box's current mode, model, aspect ratio and outputs.

        Raises:
            GenerationSettingsError: The settings popover cannot be read.
        """
        try:
            await self._ensure_on_workspace()
            return await self._read_settings()
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = GenerationSettingsError(
                "Google Flow generation settings could not be read."
            )
            await self._close_overlays_noting_failure(failure)
            raise failure from error

    @page_required
    async def configure(self, settings: GenerationSettings) -> None:
        """Apply ``settings`` to the prompt box; ``None`` fields are left as is.

        Flow remembers these settings for later generations in the project.
        They are read back afterwards, and applied once more if Flow reset
        any (e.g. a model without the chosen aspect ratio).

        Raises:
            GenerationSettingsError: Flow does not offer a requested value for
                the current mode or account, the popover cannot be driven, or
                Flow still shows other values after the settings are re-applied.
        """
        try:
            await self._ensure_on_workspace()
            await self._apply_settings(settings)
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = GenerationSettingsError(
                "Google Flow generation settings could not be applied."
            )
            await self._close_overlays_noting_failure(failure)
            raise failure from error
        except GoogleFlowError as error:
            await self._close_overlays_noting_failure(error)
            raise

    @page_required
    async def set_agent_mode(self, enabled: bool) -> None:
        """Turn the prompt box's Agent mode on or off.

        Warning:
            Enabling Agent mode makes Flow reset the generation settings (model,
            aspect ratio, outputs) and the configuration carries over to other
            projects, leaving them in an unexpected state. Turn it off again
            and re-apply your settings (``configure`` or the ``generate_*``
            arguments) before relying on them. Enabling emits a
            ``UserWarning``.

        Raises:
            GenerationSettingsError: The Agent chip cannot be toggled.
        """
        if enabled:
            warnings.warn(
                "Enabling Google Flow Agent mode removes the generation "
                "settings and the change carries over to other projects, "
                "leaving them in an unexpected state. Turn it off and "
                "re-apply your settings afterwards.",
                UserWarning,
                stacklevel=2,
            )
        try:
            await self._ensure_on_workspace()
            await self._set_agent_mode(enabled)
        except (PlaywrightError, UnexpectedPageStateError) as error:
            raise GenerationSettingsError(
                "Google Flow Agent mode could not be changed."
            ) from error

    @page_required
    async def generate_image(
        self,
        prompt: PromptText,
        *,
        model: ModelLabel | None = None,
        aspect_ratio: AspectRatio | None = None,
        outputs: OutputCount | None = None,
        references: Iterable[MediaId] = (),
        timeout: Seconds | None = None,
    ) -> tuple[MediaId, ...]:
        """Generate images and return the new media IDs, newest first.

        ``model``, ``aspect_ratio`` and ``outputs`` (1-4) use Flow's own labels,
        e.g. ``"Nano Banana Pro"`` and ``"3:4"``; ``None`` keeps the current
        value. ``references`` are media IDs added to the prompt as ingredients.
        Agent mode is turned off so the prompt goes straight to generation.

        Raises:
            ValueError: ``prompt`` is empty or a media ID is malformed.
            GenerationSettingsError: A setting cannot be applied.
            MediaNotFoundError: A reference is not in the project's media grid.
            GenerationError: Flow does not start or finish the generation in
                time, or finishes without producing media.
        """
        job = await self._start_image(
            prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            outputs=outputs,
            references=references,
        )
        return await self._finish(job, timeout)

    @page_required
    async def generate_video(
        self,
        prompt: PromptText,
        *,
        model: ModelLabel | None = None,
        aspect_ratio: AspectRatio | None = None,
        outputs: OutputCount | None = None,
        resolution: VideoResolution | None = None,
        duration: VideoDuration | None = None,
        start_frame: MediaId | None = None,
        end_frame: MediaId | None = None,
        ingredients: Iterable[MediaId] = (),
        timeout: Seconds | None = None,
    ) -> tuple[MediaId, ...]:
        """Generate videos and return the new media IDs, newest first.

        Passing ``start_frame`` and/or ``end_frame`` (image media IDs) uses
        Flow's Frames mode; otherwise Ingredients mode is used, with
        ``ingredients`` as optional reference media IDs. ``resolution`` is
        Flow's label, e.g. ``"720p"``, and ``duration`` the video length in
        seconds, e.g. ``8``; ``None`` keeps the current value. Other arguments behave as in :meth:`generate_image`. Video generation spends account credits.

        Raises:
            ValueError: ``prompt`` is empty, a media ID is malformed, frames
                are combined with ingredients, or ``end_frame`` is given
                without ``start_frame``.
            GenerationSettingsError: A setting cannot be applied.
            MediaNotFoundError: A frame or ingredient is not in the project's
                media grid.
            GenerationError: Flow does not start or finish the generation in
                time, or finishes without producing media.
        """
        job = await self._start_video(
            prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            outputs=outputs,
            resolution=resolution,
            duration=duration,
            start_frame=start_frame,
            end_frame=end_frame,
            ingredients=ingredients,
        )
        return await self._finish(job, timeout)

    @page_required
    async def start_image(
        self,
        prompt: PromptText,
        *,
        model: ModelLabel | None = None,
        aspect_ratio: AspectRatio | None = None,
        outputs: OutputCount | None = None,
        references: Iterable[MediaId] = (),
    ) -> GenerationJob:
        """Submit an image generation and return as soon as Flow starts it.

        Arguments are as in :meth:`generate_image`. Several jobs can run at
        once in one project; collect them with :meth:`wait_for_generations`.

        Raises:
            ValueError: ``prompt`` is empty or a media ID is malformed.
            GenerationSettingsError: A setting cannot be applied.
            MediaNotFoundError: A reference is not in the project's media grid.
            GenerationError: Flow does not start the generation.
        """
        return await self._start_image(
            prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            outputs=outputs,
            references=references,
        )

    @page_required
    async def start_video(
        self,
        prompt: PromptText,
        *,
        model: ModelLabel | None = None,
        aspect_ratio: AspectRatio | None = None,
        outputs: OutputCount | None = None,
        resolution: VideoResolution | None = None,
        duration: VideoDuration | None = None,
        start_frame: MediaId | None = None,
        end_frame: MediaId | None = None,
        ingredients: Iterable[MediaId] = (),
    ) -> GenerationJob:
        """Submit a video generation and return as soon as Flow starts it.

        Arguments are as in :meth:`generate_video`; Flow empties the frame
        slots on submission, so the next job can use them. Collect jobs with
        :meth:`wait_for_generations`. Video generation spends account credits.

        Raises:
            ValueError: ``prompt`` is empty, a media ID is malformed, frames
                are combined with ingredients, or ``end_frame`` is given
                without ``start_frame``.
            GenerationSettingsError: A setting cannot be applied.
            MediaNotFoundError: A frame or ingredient is not in the project's
                media grid.
            GenerationError: Flow does not start the generation.
        """
        return await self._start_video(
            prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            outputs=outputs,
            resolution=resolution,
            duration=duration,
            start_frame=start_frame,
            end_frame=end_frame,
            ingredients=ingredients,
        )

    @page_required
    async def wait_for_generations(
        self, jobs: Iterable[GenerationJob], *, timeout: Seconds | None = None
    ) -> Mapping[GenerationJob, tuple[MediaId, ...]]:
        """Wait for started jobs and map each to its media IDs, newest first.

        Flow does not say which pending tile belongs to which job, so this
        waits for every started job of the same kinds, then attributes each
        new tile by the prompt Flow shows for it. Jobs not in ``jobs`` stay
        collected for a later call. A job whose output Flow dropped (e.g. by
        safety filtering) maps to an empty tuple. ``timeout`` defaults to the
        generation timeout of the slowest kind among ``jobs``.

        Raises:
            ValueError: A job was not started by this project or was already
                returned by an earlier call.
            GenerationError: Flow does not finish in time, or the new tiles
                cannot be read.

        Repeated jobs in this call are collected once. Jobs with the same
        prompt and kind must be submitted and collected sequentially.
        """
        jobs = tuple(jobs)
        if timeout is None:
            timeout = max(
                (_default_timeout(job.kind) for job in jobs),
                default=CONFIGS.IMAGE_GENERATION_TIMEOUT_SECONDS,
            )
        try:
            return await self._wait_for_jobs(jobs, timeout)
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = GenerationError("Google Flow generations could not be collected.")
            await self._note_flow_message(failure)
            await self._restore_project_page_after_failure(failure)
            raise failure from error

    @page_required
    async def reuse_prompt(self, media_id: MediaId) -> PromptText:
        """Load a media item's prompt and settings into the prompt box.

        Returns the prompt text Flow placed in the editor.

        Raises:
            MediaNotFoundError: No grid tile has ``media_id``.
            MediaActionError: Flow does not load the prompt.
        """
        media_id = normalize_media_id(media_id)
        editor = self._require_page().locator(SELECTORS["prompt_editor"])
        try:
            await self._ensure_on_workspace()
            # Start empty so the wait below observes Flow's text, not leftovers.
            await self._clear_prompt()
        except (PlaywrightError, UnexpectedPageStateError) as error:
            raise MediaActionError(media_id, "reuse the prompt of") from error
        await self._run_media_menu_action(
            media_id, "Reuse prompt", "reuse the prompt of"
        )
        try:
            await editor.filter(has_text=re.compile(r"\S")).wait_for(state="visible")
            return " ".join((await editor.inner_text()).split())
        except PlaywrightError as error:
            raise MediaActionError(media_id, "reuse the prompt of") from error

    @page_required
    async def toggle_favourite(self, media_id: MediaId) -> None:
        """Toggle the favourite flag of a media item.

        Raises:
            MediaNotFoundError: No grid tile has ``media_id``.
            MediaActionError: Flow does not accept the action.
        """
        await self._run_media_menu_action(
            normalize_media_id(media_id), "Favorite", "favourite"
        )

    @page_required
    async def set_project_cover(self, media_id: MediaId) -> None:
        """Use a media item as the project's cover on the projects page.

        Raises:
            MediaNotFoundError: No grid tile has ``media_id``.
            MediaActionError: Flow does not accept the action.
        """
        await self._run_media_menu_action(
            normalize_media_id(media_id), "Set project cover", "set as cover"
        )

    @page_required
    async def move_to_bin(self, media_id: MediaId) -> None:
        """Move a media item to the project's bin, where it can be restored.

        Flow removes the tile at once but lists the item in the bin only
        after a delay, typically 10-30 seconds.

        Raises:
            MediaNotFoundError: No grid tile has ``media_id``.
            MediaActionError: Flow does not remove the tile from the grid.
        """
        media_id = normalize_media_id(media_id)
        await self._run_media_menu_action(media_id, "Move to trash", "move to bin")
        try:
            await self._web_page.run("wait_for_media_removed", {"media_id": media_id})
        except PlaywrightError as error:
            raise MediaActionError(media_id, "move to bin") from error

    @page_required
    async def restore_from_bin(self, media_id: MediaId) -> None:
        """Restore a binned media item and return to the project workspace.

        An item binned moments ago is not listed in the bin yet, so the bin
        is reloaded until it is, for up to a minute.
        The restored item likewise reappears in the grid after a delay.

        Raises:
            MediaNotFoundError: The bin has no tile with ``media_id``.
            MediaActionError: Flow does not restore the item.
        """
        media_id = normalize_media_id(media_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CONFIGS.BIN_LISTING_TIMEOUT_SECONDS
        try:
            while True:
                try:
                    await self._web_page.run(
                        "restore_from_bin",
                        {
                            "trash_url": f"{self.project_url}/trash",
                            "media_id": media_id,
                        },
                    )
                    break
                except ActionFailed:
                    index = await self._find_media_tile(media_id)
                    if index is not None:
                        tile = self._require_page().locator(
                            fill(SELECTORS["media_tile_at"], {"index": str(index)})
                        )
                        element = await tile.element_handle()
                        if element is None:
                            raise UnexpectedPageStateError("The bin tile disappeared.")
                        try:
                            await self._web_page.run(
                                "restore_media_tile_at", {"index": str(index)}
                            )
                            await element.wait_for_element_state("hidden")
                        finally:
                            await element.dispose()
                        break
                    if loop.time() >= deadline:
                        raise
            await self._return_to_workspace()
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = MediaActionError(media_id, "restore")
            await self._restore_project_page_after_failure(failure)
            raise failure from error
        except ActionFailed as error:
            failure = MediaNotFoundError(media_id)
            await self._restore_project_page_after_failure(failure)
            raise failure from error

    @page_required
    async def download_media(
        self,
        media_id: MediaId,
        destination: Path,
        *,
        quality: QualityLabel | None = None,
    ) -> Path:
        """Download a media item and return the saved file path.

        ``quality`` is the leading label of a Download menu entry, such as
        ``"1K"`` or ``"2K"`` for images or ``"720p"`` for videos; some
        upscales spend credits, which Flow notes in the label. ``None`` picks
        the original size, or else the first enabled entry. Uploads have no
        such menu, so they ignore ``quality``. Video tiles expose no media
        ID, so a media ID not found in the grid is downloaded from its editor
        page. If ``destination`` is an existing directory, Flow's suggested
        file name is used inside it.

        Raises:
            MediaNotFoundError: Neither the grid nor the editor has ``media_id``.
            MediaActionError: The quality is unavailable, Flow fails to
                upscale to it, or the download fails.
        """
        media_id = normalize_media_id(media_id)
        params: dict[ParamName, str] = {
            "media_id": media_id,
            "quality": quality or "",
            "to": str(destination),
        }
        try:
            await self._ensure_on_workspace()
            try:
                results = await self._web_page.run("download_media", params)
            except ActionFailed as error:
                # Only a missing tile falls back; these mean the tile was found.
                if error.results.keys() & {"available", "flow_messages"}:
                    raise
                results = await self._web_page.run(
                    "download_from_editor",
                    {
                        "edit_url": f"{self.project_url}/edit/{media_id}",
                        "quality": params["quality"],
                        "to": params["to"],
                    },
                )
            target = Path(text_result(results, "path"))
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = MediaActionError(media_id, "download")
            await self._close_overlays_noting_failure(failure)
            raise failure from error
        except ActionFailed as error:
            if "available" in error.results:
                failure = MediaActionError(media_id, f"download {quality!r} of")
                available = texts_result(error.results, "available")
                failure.add_note(f"Available downloads: {', '.join(available)}.")
            elif "flow_messages" in error.results:
                failure = MediaActionError(media_id, f"download {quality!r} of")
                for message in texts_result(error.results, "flow_messages"):
                    failure.add_note(f"Google Flow reported: {message}")
            else:
                failure = MediaNotFoundError(media_id)
            await self._close_overlays_noting_failure(failure)
            raise failure from error

        await self._close_overlays()
        return target

    @page_required
    async def create_scene(self) -> SceneId:
        """Create an empty scene, return to the workspace, and return its ID.

        Raises:
            SceneCreationError: Flow does not open a new scene.
        """
        try:
            await self._ensure_on_workspace()
            results = await self._web_page.run(
                "new_scene", {"project_url": self.project_url}
            )
            scene_id = scene_id_from_url(
                text_result(results, "url"), self.project_magic
            )
            if scene_id is None:
                raise UnexpectedPageStateError("Flow left the new scene page.")
            await self._return_to_workspace()
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = SceneCreationError("Google Flow could not create a scene.")
            await self._restore_project_page_after_failure(failure)
            raise failure from error
        return scene_id

    @override
    def _closed_error(self) -> Exception:
        return ProjectClosedError(self.project_magic)

    async def _return_to_workspace(self) -> None:
        """Load this project's workspace and verify Flow stayed on it."""
        await self._web_page.run("open_project", {"project_url": self.project_url})
        await self._web_page.run("wait_for_workspace")
        if not is_project_url(self._require_page().url, self.project_magic):
            raise UnexpectedPageStateError(
                "Flow did not return to the expected project workspace."
            )

    async def _restore_project_page_after_failure(
        self, operation_error: BaseException
    ) -> None:
        page = self._page
        if page is None or page.is_closed():
            self._page = None
            operation_error.add_note(
                "The project page was unavailable, so its workspace could not "
                "be restored after the failure."
            )
            return

        try:
            async with asyncio.timeout(CONFIGS.RECOVERY_TIMEOUT_SECONDS):
                await self._return_to_workspace()
        except Exception as recovery_error:  # noqa: BLE001 - attached below
            operation_error.add_note(
                "Workspace recovery also failed: "
                f"{type(recovery_error).__name__}: {recovery_error}"
            )
            self._page = None
            cleanup_error = await close_page_and_report_failure(page)
            note_cleanup_failure(operation_error, cleanup_error)

    async def _verify_deletion(self, page: BrowserPage) -> None:
        """Verify that the project URL resolves to Flow's not-found route."""
        # Card removal can be an optimistic UI update, so independently load
        # the former project URL and verify its remote postcondition.
        try:
            await self._web_page.run("open_project", {"project_url": self.project_url})
        except PlaywrightError:
            # Client-side redirects can abort the original navigation. That is
            # acceptable only when the resulting route is already the 404 page.
            if not is_not_found_url(page.url):
                raise

        if not is_not_found_url(page.url):
            try:
                await self._web_page.run("wait_for_not_found")
            except PlaywrightError:
                if not is_not_found_url(page.url):
                    raise

        if not is_not_found_url(page.url):
            raise UnexpectedPageStateError(
                "Flow did not redirect the deleted project to its 404 route."
            )

    async def _ensure_on_workspace(self) -> None:
        """Return to the workspace if an editor, scene or bin route is open."""
        if not is_project_url(self._require_page().url, self.project_magic):
            await self._return_to_workspace()

    async def _snapshot_media(self) -> tuple[_Tile, ...]:
        raw_tiles = await self._require_page().evaluate(
            _SNAPSHOT_MEDIA_JS,
            [SELECTORS["media_tile"], SELECTORS["pending_tile"], URLS.media_content],
        )
        if not is_list(raw_tiles):
            raise UnexpectedPageStateError("The media grid snapshot is not a list.")
        return tuple(_tile_from_raw(raw_tile) for raw_tile in raw_tiles)

    async def _known_media_ids(self) -> frozenset[MediaId]:
        """IDs of every tile currently in the grid, finished or not."""
        return frozenset(
            tile.item.media_id
            for tile in await self._snapshot_media()
            if tile.item.media_id is not None
        )

    async def _wait_for_uploaded_media(
        self,
        uploads: Mapping[UploadToken, MediaKind],
        known_ids: AbstractSet[MediaId],
        timeout: Seconds,
    ) -> Mapping[UploadToken, MediaId]:
        """Wait for a finished tile per upload; map each upload token to its ID.

        ``uploads`` maps each upload token to the kind of tile it should
        become (``"image"`` or ``"video"``). Tiles are matched by the token
        appearing in any of their labels. A finished tile that is new since
        ``known_ids`` but carries no token is matched by elimination once
        nothing is pending, when it is the only candidate of its kind.
        """
        missing = dict(uploads)
        found: dict[UploadToken, MediaId] = {}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        settled_at: float | None = None
        while True:
            tiles = await self._poll_media()
            claimed = set(found.values())
            for tile in tiles:
                media_id = tile.finished_id
                if media_id is None or media_id in claimed:
                    continue
                if (token := _upload_token_in(tile.labels, missing)) is not None:
                    found[token] = media_id
                    claimed.add(media_id)
                    del missing[token]
            if not missing:
                return found

            now = loop.time()
            unnamed = [
                tile
                for tile in tiles
                if tile.finished_id is not None
                and tile.finished_id not in known_ids
                and tile.finished_id not in claimed
            ]
            if unnamed and not any(tile.item.pending for tile in tiles):
                settled_at = now if settled_at is None else settled_at
                if now - settled_at >= CONFIGS.UPLOAD_SETTLE_SECONDS:
                    for token, media_id in _match_by_kind(missing, unnamed).items():
                        found[token] = media_id
                        del missing[token]
                    if not missing:
                        return found
            else:
                settled_at = None

            if now >= deadline:
                failure = MediaUploadError(
                    f"Google Flow did not finish {len(missing)} uploaded media item(s)."
                )
                if unnamed:
                    failure.add_note(
                        "Unidentified new media in the grid: "
                        f"{', '.join(str(tile.finished_id) for tile in unnamed)}."
                    )
                await self._note_flow_message(failure)
                raise failure
            await asyncio.sleep(CONFIGS.POLL_INTERVAL_SECONDS)

    async def _start_image(
        self,
        prompt: PromptText,
        *,
        model: ModelLabel | None,
        aspect_ratio: AspectRatio | None,
        outputs: OutputCount | None,
        references: Iterable[MediaId],
    ) -> GenerationJob:
        settings = GenerationSettings(
            mode=GenerationMode.IMAGE,
            aspect_ratio=aspect_ratio,
            model=model,
            outputs=outputs,
        )
        return await self._start_generation(
            prompt,
            settings,
            expected_kind="image",
            ingredients=_normalize_media_ids(references),
        )

    async def _start_video(
        self,
        prompt: PromptText,
        *,
        model: ModelLabel | None,
        aspect_ratio: AspectRatio | None,
        outputs: OutputCount | None,
        resolution: VideoResolution | None,
        duration: VideoDuration | None,
        start_frame: MediaId | None,
        end_frame: MediaId | None,
        ingredients: Iterable[MediaId],
    ) -> GenerationJob:
        ingredient_ids = _normalize_media_ids(ingredients)
        frame_inputs: tuple[tuple[FrameSlot, MediaId | None], ...] = (
            ("Start", start_frame),
            ("End", end_frame),
        )
        frames: dict[FrameSlot, MediaId] = {
            slot: normalize_media_id(media_id)
            for slot, media_id in frame_inputs
            if media_id is not None
        }
        if frames and ingredient_ids:
            raise ValueError("frames and ingredients cannot be combined")
        if "End" in frames and "Start" not in frames:
            raise ValueError("Google Flow needs a start_frame with an end_frame")

        settings = GenerationSettings(
            mode=GenerationMode.VIDEO,
            video_input=VideoInput.FRAMES if frames else VideoInput.INGREDIENTS,
            aspect_ratio=aspect_ratio,
            model=model,
            resolution=resolution,
            duration=duration,
            outputs=outputs,
        )
        return await self._start_generation(
            prompt,
            settings,
            expected_kind="video",
            ingredients=ingredient_ids,
            frames=frames,
        )

    async def _start_generation(
        self,
        prompt: PromptText,
        settings: GenerationSettings,
        *,
        expected_kind: OutputKind,
        ingredients: Iterable[MediaId] = (),
        frames: Mapping[FrameSlot, MediaId] | None = None,
    ) -> GenerationJob:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt must not be empty or whitespace")
        if any(
            job.kind == expected_kind
            and state.outputs is None
            and " ".join(job.prompt.split()) == " ".join(prompt.split())
            for job, state in self._jobs.items()
        ):
            raise GenerationError(
                "A generation with the same prompt is still uncollected. "
                "Collect it before submitting another: Flow cannot reliably "
                "distinguish concurrent jobs with identical prompts."
            )

        self._flow_messages.clear()
        try:
            await self._ensure_on_workspace()
            baseline = await self._generation_baseline(expected_kind)
            await self._set_agent_mode(False)
            await self._apply_settings(settings)
            # This also empties the ingredients and frames left from before.
            await self._clear_prompt()
            await self._fill_frames(frames or {})
            for media_id in ingredients:
                await self._add_ingredient(media_id)
            await self._fill_prompt(prompt)
            return await self._submit_generation(
                prompt, expected_kind=expected_kind, baseline=baseline
            )
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = GenerationError("Google Flow could not start the generation.")
            await self._note_flow_message(failure)
            await self._close_overlays_noting_failure(failure)
            raise failure from error
        except GoogleFlowError as error:
            await self._close_overlays_noting_failure(error)
            raise

    async def _finish(
        self, job: GenerationJob, timeout: Seconds | None
    ) -> tuple[MediaId, ...]:
        """Wait for one job; raise if Flow produced nothing for it."""
        try:
            outputs = (
                await self._wait_for_jobs(
                    (job,), _default_timeout(job.kind) if timeout is None else timeout
                )
            )[job]
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = GenerationError("Google Flow could not run the generation.")
            await self._note_flow_message(failure)
            await self._restore_project_page_after_failure(failure)
            raise failure from error
        if not outputs:
            failure = GenerationError(
                "Google Flow finished the generation without producing media."
            )
            await self._note_flow_message(failure)
            raise failure
        return outputs

    async def _set_agent_mode(self, enabled: bool) -> None:
        await self._web_page.run(
            "set_agent_mode", {"pressed": "true" if enabled else "false"}
        )

    async def _read_settings(self) -> GenerationSettings:
        settings, _ = await self._read_settings_and_summary()
        return settings

    async def _read_settings_and_summary(
        self,
    ) -> tuple[GenerationSettings, tuple[SettingLabel, ...]]:
        """The checked settings, plus the trigger summary's labels.

        The summary also names fixed values whose group the panel hides.
        """
        results = await self._web_page.run("read_generation_settings")
        # Some labels carry a badge glyph, e.g. "🍌 Nano Banana Pro".
        model = re.sub(r"^\W+", "", text_result(results, "model"))
        summary_labels = _summary_labels(text_result(results, "summary"))
        await self._close_overlays()

        mode = _checked_label(results, "mode")
        video_input = _checked_label(results, "video_input")
        duration = _checked_label(results, "duration")
        duration_match = None if duration is None else re.fullmatch(r"(\d+)s", duration)
        outputs = _checked_label(results, "outputs")
        outputs_match = None if outputs is None else re.fullmatch(r"x([1-4])", outputs)
        settings = GenerationSettings(
            mode=GenerationMode(mode) if mode in tuple(GenerationMode) else None,
            video_input=(
                VideoInput(video_input) if video_input in tuple(VideoInput) else None
            ),
            aspect_ratio=_checked_label(results, "aspect_ratio"),
            model=model or None,
            # Models with one resolution hide the group; the summary names it.
            resolution=_checked_label(results, "resolution")
            or next(
                (label for label in summary_labels if re.fullmatch(r"\d+p", label)),
                None,
            ),
            duration=int(duration_match[1]) if duration_match else None,
            outputs=int(outputs_match[1]) if outputs_match else None,
        )
        return settings, summary_labels

    async def _apply_settings(self, settings: GenerationSettings) -> None:
        """Apply ``settings``, then read them back until Flow shows them all.

        A later choice can reset an earlier one (a model may not offer the
        chosen aspect ratio or resolution), so a mismatch is applied again
        before it is reported.

        Raises:
            GenerationSettingsError: Flow does not offer a value, or still
                shows other values after the last attempt.
        """
        if settings == GenerationSettings():
            return

        for attempt in range(1, CONFIGS.SETTINGS_APPLY_ATTEMPTS + 1):
            await self._select_settings(settings)
            actual, summary_labels = await self._read_settings_and_summary()
            mismatches = _settings_mismatches(settings, actual, summary_labels)
            if not mismatches:
                return
            if attempt == CONFIGS.SETTINGS_APPLY_ATTEMPTS:
                raise GenerationSettingsError(
                    "Google Flow did not keep the requested settings: "
                    f"{'; '.join(mismatches)}."
                )

    async def _select_settings(self, settings: GenerationSettings) -> None:
        """Choose every non-``None`` field of ``settings`` in the popover."""
        await self._web_page.run("open_generation_settings")
        # Mode first: it decides which of the other options are rendered.
        if settings.mode is not None:
            await self._select_setting("Mode", settings.mode)
        if settings.video_input is not None:
            await self._select_setting("Video type", settings.video_input)
        if settings.model is not None:
            await self._select_model(settings.model)
        if settings.aspect_ratio is not None:
            await self._select_setting("Aspect ratio", settings.aspect_ratio)
        # After the model: it decides which resolutions and durations exist.
        if settings.resolution is not None:
            await self._select_setting("Video resolution", settings.resolution)
        if settings.duration is not None:
            await self._select_setting("Video duration", f"{settings.duration}s")
        if settings.outputs is not None:
            await self._select_setting("Output count", f"x{settings.outputs}")
        await self._close_overlays()

    async def _select_setting(self, field: SettingField, label: SettingLabel) -> None:
        """Check the toggle ``label`` in group ``field``, or report its options."""
        try:
            await self._web_page.run("select_setting", {"group": field, "label": label})
        except ActionFailed as error:
            available = texts_result(error.results, "available")
            # A hidden group's fixed value, e.g. Veo's 720p, is already set.
            if not available and label in _summary_labels(
                text_result(error.results, "summary")
            ):
                return
            raise GenerationSettingsError(
                f"Google Flow does not offer {field.lower()} {str(label)!r} here; "
                f"available options: {', '.join(available) or 'none'}."
            ) from error

    async def _select_model(self, model: ModelLabel) -> None:
        try:
            await self._web_page.run("select_model", {"model": model})
        except ActionFailed as error:
            available = texts_result(error.results, "available")
            raise GenerationSettingsError(
                f"Google Flow does not offer model {model!r} here; "
                f"available models: {', '.join(available)}."
            ) from error

    async def _clear_prompt(self) -> None:
        await self._web_page.run("clear_prompt")

    async def _fill_frames(self, frames: Mapping[FrameSlot, MediaId]) -> None:
        """Fill the emptied frame slots with ``frames``.

        Flow puts media added from a tile into the first empty slot, so the
        Start frame, which an End frame requires, goes first.
        """
        for position, slot in enumerate(("Start", "End")):
            if (media_id := frames.get(slot)) is not None:
                await self._add_to_prompt(media_id)
                await self._web_page.run(
                    "wait_for_frame", {"media_id": media_id, "position": str(position)}
                )

    async def _add_ingredient(self, media_id: MediaId) -> None:
        await self._add_to_prompt(media_id)
        await self._web_page.run("wait_for_ingredient", {"media_id": media_id})

    async def _add_to_prompt(self, media_id: MediaId) -> None:
        """Add media to the prompt box from its grid tile's menu.

        The asset picker's thumbnails carry no media ID; the tile does.
        """
        try:
            await self._run_media_menu_item(media_id, "Add to prompt")
        except ActionFailed as error:
            raise MediaNotFoundError(media_id) from error

    async def _fill_prompt(self, prompt: PromptText) -> None:
        results = await self._web_page.run("fill_prompt", {"prompt": prompt})
        if text_result(results, "text").split() != prompt.split():
            raise GenerationError("Google Flow did not accept the prompt text.")

    async def _generation_baseline(self, kind: OutputKind) -> frozenset[MediaId]:
        """Resolve existing rendered media before preparing the new prompt.

        After a reload videos may expose their ID only through the editor.
        Resolve those too, before navigation could disturb prepared inputs.
        """
        known: set[MediaId] = set()
        for index, tile in _finished_tiles(await self._snapshot_media(), kind):
            media_id = tile.item.media_id
            if media_id is not None:
                known.add(media_id)
            editor_id = self._media_editor_ids.get(media_id) if bool(media_id) else None
            if media_id is None or (kind == "video" and editor_id is None):
                editor_id, _ = await self._open_tile(index)
                if media_id is not None:
                    self._media_editor_ids[media_id] = editor_id
            if editor_id is not None:
                known.add(editor_id)
        return frozenset(known)

    async def _submit_generation(
        self,
        prompt: PromptText,
        *,
        expected_kind: OutputKind,
        baseline: frozenset[MediaId],
    ) -> GenerationJob:
        """Click Generate and return once Flow shows a new tile for it."""
        page = self._require_page()
        generate_button = page.locator(SELECTORS["generate_button"])
        if await generate_button.is_disabled():
            failure = GenerationError(
                "Google Flow did not enable generation for this prompt and inputs."
            )
            # The button's tooltip says why, e.g. a missing start frame.
            reason = await page.locator(SELECTORS["generate_button_host"]).evaluate(
                _DESCRIPTION_JS
            )
            if isinstance(reason, str) and reason.strip():
                failure.add_note(f"Google Flow reported: {reason.strip()}")
            raise failure

        trigger_text = await page.locator(SELECTORS["settings_trigger"]).inner_text()
        outputs_match = re.search(r"\bx([1-4])\b", trigger_text)
        job = GenerationJob(
            prompt=prompt,
            kind=expected_kind,
            outputs=int(outputs_match[1]) if outputs_match else 1,
        )

        before = await self._snapshot_media()
        pending_before = sum(tile.item.pending for tile in before)
        await generate_button.click()

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        while True:
            await asyncio.sleep(CONFIGS.POLL_INTERVAL_SECONDS)
            tiles = await self._poll_media()
            # A submission adds a tile; finishing one only changes its state.
            if (
                len(tiles) > len(before)
                or (sum(tile.item.pending for tile in tiles) > pending_before)
                or any(
                    tile.finished_id is not None
                    and tile.finished_id not in baseline
                    and tile.item.kind == expected_kind
                    for tile in tiles
                )
            ):
                self._jobs[job] = _JobState(baseline=baseline)
                return job
            if loop.time() - started_at >= CONFIGS.GENERATION_START_TIMEOUT_SECONDS:
                failure = GenerationError("Google Flow did not start the generation.")
                await self._note_flow_message(failure)
                raise failure

    async def _wait_for_jobs(
        self, jobs: tuple[GenerationJob, ...], timeout: Seconds
    ) -> dict[GenerationJob, tuple[MediaId, ...]]:
        jobs = tuple(dict.fromkeys(jobs))
        for job in jobs:
            if job not in self._jobs:
                raise ValueError(
                    "job was not started by this project or was already collected"
                )
        await self._ensure_on_workspace()
        deadline = asyncio.get_running_loop().time() + timeout
        for kind in dict.fromkeys(job.kind for job in jobs):
            uncollected = {
                job: state
                for job, state in self._jobs.items()
                if job.kind == kind and state.outputs is None
            }
            if uncollected:
                await self._collect(kind, uncollected, deadline)
        return {job: self._jobs.pop(job).outputs or () for job in jobs}

    async def _collect(
        self,
        kind: OutputKind,
        jobs: Mapping[GenerationJob, _JobState],
        deadline: float,
    ) -> None:
        """Wait for every uncollected ``kind`` job and record their outputs.

        Compare identities instead of grid counts, and verify every output's
        prompt. Retain matches across snapshots, including virtualized grids.
        """
        loop = asyncio.get_running_loop()
        settled_at: float | None = None
        examined: set[MediaId] = set()
        outputs = {job: list(state.found) for job, state in jobs.items()}
        while True:
            tiles = await self._poll_media()
            visible_ids: list[MediaId] = []
            for index, tile in _finished_tiles(tiles, kind):
                tile_id = tile.item.media_id
                if tile_id is not None:
                    visible_ids.append(tile_id)
                if tile_id is not None and (
                    tile_id in examined
                    or all(tile_id in state.baseline for state in jobs.values())
                ):
                    continue
                media_id, shown_prompt = await self._open_tile(index)
                if tile_id is not None:
                    self._media_editor_ids[tile_id] = media_id
                if tile_id is None:
                    visible_ids.append(media_id)
                if media_id in examined:
                    continue
                examined.add(media_id)
                if tile_id is not None:
                    examined.add(tile_id)
                job = _job_for_prompt(shown_prompt, outputs)
                if job is not None:
                    state = jobs[job]
                    if media_id in state.baseline or tile_id in state.baseline:
                        continue
                    output_id = tile_id or media_id
                    state.found[output_id] = None
                    outputs[job] = list(state.found)
            for job, state in jobs.items():
                # Preserve the grid's newest-first order while retaining
                # outputs that have scrolled out of its virtualized window.
                state.found = dict.fromkeys(
                    [media_id for media_id in visible_ids if media_id in state.found]
                    + list(state.found)
                )
                outputs[job] = list(state.found)
            now = loop.time()
            if all(len(outputs[job]) >= job.outputs for job in jobs):
                break
            if any(tile.item.pending for tile in tiles):
                settled_at = None
            else:
                # Flow may drop outputs (e.g. safety filtering); once nothing
                # is pending, attribute what was produced.
                settled_at = now if settled_at is None else settled_at
                if now - settled_at >= CONFIGS.GENERATION_FAILURE_GRACE_SECONDS:
                    break
            if now >= deadline:
                failure = GenerationError(
                    f"Google Flow did not finish {len(jobs)} {kind} generation(s) "
                    "in time."
                )
                await self._note_flow_message(failure)
                raise failure
            await asyncio.sleep(CONFIGS.POLL_INTERVAL_SECONDS)

        for job, state in jobs.items():
            state.outputs = tuple(outputs[job][: job.outputs])

    async def _open_tile(self, index: int) -> tuple[MediaId, PromptText]:
        """Open the grid tile at ``index`` in the editor; return its ID and prompt."""
        from_bin = self._require_page().url.rstrip("/") == f"{self.project_url}/trash"
        results = await self._web_page.run(
            "open_media_tile", {"index": str(index), "project_url": self.project_url}
        )
        media_id = edit_id_from_url(text_result(results, "url"), self.project_magic)
        if media_id is None:
            raise UnexpectedPageStateError("Flow did not open the media editor.")
        prompt = text_result({"prompt": results.get("prompt", "")}, "prompt")
        await self._back_to_workspace(from_bin=from_bin)
        return media_id, prompt

    async def _back_to_workspace(self, *, from_bin: bool = False) -> None:
        """Go back from a page opened from the workspace, without reloading it.

        A reload can reorder the grid (the live grid keeps submission order),
        so grid indices taken before stay valid only this way.
        """
        page = self._require_page()
        await page.go_back(wait_until="commit")
        if from_bin:
            if page.url.rstrip("/") != f"{self.project_url}/trash":
                raise UnexpectedPageStateError("Going back did not return to the bin.")
            return
        if not is_project_url(page.url, self.project_magic):
            raise UnexpectedPageStateError(
                "Going back did not return to the project workspace."
            )
        await self._web_page.run("wait_for_workspace")

    async def _find_media_tile(self, media_id: MediaId) -> int | None:
        """Resolve an opaque video tile using its editor identity, in either grid."""
        editor_id = self._media_editor_ids.get(media_id, media_id)
        for index, tile in enumerate(await self._snapshot_media()):
            if tile.item.media_id in {media_id, editor_id}:
                return index
            if tile.item.kind == "video" and not tile.item.pending:
                candidate_id, _ = await self._open_tile(index)
                if candidate_id == editor_id:
                    return index
        return None

    async def _run_media_menu_item(
        self, media_id: MediaId, item: MediaMenuItem
    ) -> None:
        params = {"media_id": media_id, "item": _menu_item_pattern(item)}
        try:
            await self._web_page.run("run_media_menu_item", params)
        except ActionFailed:
            index = await self._find_media_tile(media_id)
            if index is None:
                raise
            tile = self._require_page().locator(
                fill(SELECTORS["media_tile_at"], {"index": str(index)})
            )
            element = await tile.element_handle()
            if element is None:
                raise UnexpectedPageStateError("The media tile disappeared.")
            try:
                await self._web_page.run(
                    "run_media_menu_item_at",
                    {"index": str(index), "item": params["item"]},
                )
                if item == "Move to trash":
                    await element.wait_for_element_state("hidden")
            finally:
                await element.dispose()

    async def _run_media_menu_action(
        self, media_id: MediaId, item_label: MediaMenuItem, action: MediaAction
    ) -> None:
        """Choose ``item_label`` in a grid tile's context menu."""
        try:
            await self._ensure_on_workspace()
            await self._run_media_menu_item(media_id, item_label)
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = MediaActionError(media_id, action)
            await self._note_flow_message(failure)
            await self._close_overlays_noting_failure(failure)
            raise failure from error
        except ActionFailed as error:
            failure = MediaNotFoundError(media_id)
            await self._close_overlays_noting_failure(failure)
            raise failure from error

    async def _poll_media(self) -> tuple[_Tile, ...]:
        """Snapshot the grid for a polling wait, riding out brief page errors.

        A wait can last many minutes, so one failed ``evaluate`` (e.g. while
        Flow re-renders) should not abort it. It also records visible Flow
        snackbars for :meth:`_note_flow_message`.
        """
        attempt = 1
        while True:
            try:
                tiles = await self._snapshot_media()
                break
            except PlaywrightError:
                # A closed page will not recover; this raises ProjectClosedError.
                self._require_page()
                if attempt >= CONFIGS.POLL_SNAPSHOT_ATTEMPTS:
                    raise
                attempt += 1
                await asyncio.sleep(CONFIGS.POLL_INTERVAL_SECONDS)
        self._flow_messages.update(dict.fromkeys(await self._read_flow_messages()))
        return tiles

    async def _read_flow_messages(self) -> tuple[str, ...]:
        """The text of every visible Flow snackbar, or none if unreadable."""
        try:
            results = await self._web_page.run("read_flow_messages")
            return texts_result(results, "messages")
        except PlaywrightError, UnexpectedPageStateError:
            return ()

    async def _note_flow_message(self, error: BaseException) -> None:
        """Attach Flow's snackbars, seen while polling or visible now, to ``error``."""
        self._flow_messages.update(dict.fromkeys(await self._read_flow_messages()))
        for message in self._flow_messages:
            error.add_note(f"Google Flow reported: {message}")
        self._flow_messages.clear()

    async def _close_overlays(self) -> None:
        """Dismiss Flow menus and popovers left open by an operation."""
        page = self._require_page()
        open_overlays = page.locator(
            ", ".join(
                SELECTORS[key]
                for key in ("overlay_menu", "settings_panel", "add_menu_popover")
            )
        ).filter(visible=True)

        for attempt in range(CONFIGS.MENU_CLOSE_MAX_ATTEMPTS):
            if await open_overlays.count() == 0:
                return

            await page.keyboard.press("Escape")
            try:
                await open_overlays.last.wait_for(
                    state="hidden",
                    timeout=CONFIGS.MENU_CLOSE_RETRY_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError:
                if attempt == CONFIGS.MENU_CLOSE_MAX_ATTEMPTS - 1:
                    raise

        if await open_overlays.count() != 0:
            raise UnexpectedPageStateError("Flow overlays could not be closed.")

    async def _close_overlays_and_report_failure(self) -> Exception | None:
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
                await self._close_overlays()
        except Exception as error:  # noqa: BLE001 - returned to the caller
            return error
        return None

    async def _close_overlays_noting_failure(
        self, operation_error: BaseException
    ) -> None:
        if self.is_closed:
            return
        cleanup_error = await self._close_overlays_and_report_failure()
        if cleanup_error is not None:
            operation_error.add_note(
                "Closing Flow menus also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )


# Helpers

# The text of the element's tooltip, which Material links by aria-describedby.
_DESCRIPTION_JS: Final = """
(element) => {
    const id = element.getAttribute("aria-describedby");
    return id ? document.getElementById(id)?.textContent ?? null : null;
}
"""

_SNAPSHOT_MEDIA_JS: Final = """
([tileSelector, pendingSelector, contentUrl]) => {
    const contentPath = new RegExp("^(?:image|video)/([A-Za-z0-9_-]+)");
    const mediaIdFromUrl = (url) =>
        url.startsWith(contentUrl)
            ? contentPath.exec(url.slice(contentUrl.length))?.[1] ?? null
            : null;
    return [...document.querySelectorAll(tileSelector)].map((tile) => {
        // A local blob: preview is not Flow-hosted media yet.
        const tagged = tile.querySelector('[data-media-id]:not([src^="blob:"])');
        let mediaId = tagged?.getAttribute("data-media-id") || null;
        let mediaElement = tagged;
        if (!mediaId) {
            for (const element of tile.querySelectorAll("img, video, source, a")) {
                mediaId = ["src", "poster", "href"]
                    .map((name) => mediaIdFromUrl(element.getAttribute(name) ?? ""))
                    .find(Boolean) ?? null;
                if (mediaId) {
                    mediaElement = element;
                    break;
                }
            }
        }
        const body = [...tile.querySelectorAll("*")].find(
            (element) =>
                /^FLOW-[A-Z-]+-TILE$/.test(element.tagName) &&
                !element.matches(pendingSelector),
        );
        let kind = body
            ? body.tagName.toLowerCase().replace(/^flow-/, "").replace(/-tile$/, "")
            : null;
        if (!kind && mediaElement) {
            kind = mediaElement.closest("video") || tile.querySelector("video")
                ? "video"
                : "image";
        }
        // Flow has shown the upload name in different places over time, so
        // every label-like attribute is kept for matching.
        const labels = [tile, ...tile.querySelectorAll("[aria-label], [title], img[alt]")]
            .flatMap((element) => [
                element.getAttribute("aria-label"),
                element.getAttribute("title"),
                element.getAttribute("alt"),
            ])
            .concat(tile.innerText)
            .filter((label) => label && label.trim());
        return {
            title: tile.getAttribute("aria-label") ?? "",
            labels: [...new Set(labels)],
            kind: kind ?? "unknown",
            mediaId,
            pending: tile.querySelector(pendingSelector) !== null,
        };
    });
}
"""


class _RawTile(TypedDict):
    """One entry of the ``_SNAPSHOT_MEDIA_JS`` result."""

    title: str
    labels: list[TileLabel]
    kind: MediaKind
    mediaId: MediaId | None
    pending: bool


def _is_raw_tile(value: RawJson) -> TypeIs[_RawTile]:
    if not is_str_keyed_mapping(value):
        return False
    media_id = value.get("mediaId")
    return (
        isinstance(value.get("title"), str)
        and is_str_list(value.get("labels"))
        and isinstance(value.get("kind"), str)
        and (media_id is None or isinstance(media_id, str))
        and isinstance(value.get("pending"), bool)
    )


def _tile_from_raw(raw_tile: RawJson) -> _Tile:
    if not _is_raw_tile(raw_tile):
        raise UnexpectedPageStateError(f"Malformed media grid tile: {raw_tile!r}")
    return _Tile(
        item=MediaItem(
            title=raw_tile["title"],
            kind=raw_tile["kind"],
            media_id=raw_tile["mediaId"],
            pending=raw_tile["pending"],
        ),
        labels=tuple(raw_tile["labels"]),
    )


def _note_created_project(failure: BaseException, url: Url) -> None:
    """Name the project Flow had already created when ``new`` failed."""
    try:
        project_magic = project_magic_from_url(url)
    except ProjectCreationError:
        return
    failure.add_note(
        f"Google Flow had already created project {project_magic!r}; "
        "it was left in place."
    )


def _ensure_opened(url: Url, project_magic: ProjectMagic) -> None:
    """Raise unless ``url`` is the workspace of the project being opened."""
    if is_not_found_url(url):
        raise ProjectNotFoundError(project_magic)
    if not is_project_url(url, project_magic):
        raise ProjectOpenError(project_magic)


def _finished_tiles(
    tiles: Iterable[_Tile], kind: OutputKind
) -> list[tuple[int, _Tile]]:
    """Finished tiles of ``kind`` with their grid index, newest first."""
    return [
        (index, tile)
        for index, tile in enumerate(tiles)
        if tile.item.kind == kind and not tile.item.pending
    ]


def _job_for_prompt(
    shown_prompt: PromptText, outputs: Mapping[GenerationJob, list[MediaId]]
) -> GenerationJob | None:
    """The job still missing outputs whose prompt Flow shows for a tile."""
    shown = " ".join(shown_prompt.split())
    waiting = [job for job, found in outputs.items() if len(found) < job.outputs]
    matches = [job for job in waiting if " ".join(job.prompt.split()) == shown]
    if len(matches) > 1:
        raise GenerationError("Flow returned an ambiguous prompt for concurrent jobs.")
    return matches[0] if matches else None


# Other labels of media menu entries. Flow follows the account and browser
# language, so en-GB spellings may show, and Favorite reads "Remove favorite"
# on a favourite.
_MENU_ITEM_VARIANTS: Final[Mapping[MediaMenuItem, tuple[str, ...]]] = MappingProxyType(
    {
        "Favorite": ("Favourite", "Remove favorite", "Remove favourite"),
        "Move to trash": ("Move to bin",),
    }
)


def _menu_item_pattern(item: MediaMenuItem) -> str:
    """The ``media_menu_item`` regex matching any label of ``item``."""
    labels = (item, *_MENU_ITEM_VARIANTS.get(item, ()))
    return "|".join(ESCAPES["regex"](label) for label in labels)


def _checked_label(results: Mapping[str, object], group: str) -> str | None:
    """The label a settings group has checked, or ``None`` if Flow hides it."""
    labels = texts_result(results, group)
    return labels[0] if labels else None


def _summary_labels(summary: str) -> tuple[SettingLabel, ...]:
    """The labels in the settings trigger's summary, e.g. "Video · 720p · 4s x1"."""
    return tuple(summary.replace("·", " ").split())


def _settings_mismatches(
    requested: GenerationSettings,
    actual: GenerationSettings,
    summary_labels: Iterable[SettingLabel],
) -> list[str]:
    """Describe each requested setting Flow does not show, e.g. after a reset.

    A group the panel hides reads as ``None``; its value then counts as kept
    if the trigger summary names it, as Veo's fixed 720p.
    """

    def label(value: object, template: str) -> str | None:
        return None if value is None else template.format(value)

    summary = set(summary_labels)
    fields = (
        ("mode", requested.mode, actual.mode, "{}"),
        ("video type", requested.video_input, actual.video_input, "{}"),
        ("aspect ratio", requested.aspect_ratio, actual.aspect_ratio, "{}"),
        ("resolution", requested.resolution, actual.resolution, "{}"),
        ("duration", requested.duration, actual.duration, "{}s"),
        ("output count", requested.outputs, actual.outputs, "x{}"),
    )
    mismatches: list[str] = []
    for name, wanted, shown, template in fields:
        wanted_label = label(wanted, template)
        shown_label = label(shown, template)
        if wanted_label is None or wanted_label == shown_label:
            continue
        if shown_label is None and wanted_label in summary:
            continue
        mismatches.append(f"{name} {wanted_label!r} (Flow shows {shown_label!r})")

    # Like the model menu's own matching: the button may add a family name
    # or badge around the requested label.
    if requested.model is not None and (
        actual.model is None
        or " ".join(requested.model.split()).casefold()
        not in " ".join(actual.model.split()).casefold()
    ):
        mismatches.append(f"model {requested.model!r} (Flow shows {actual.model!r})")
    return mismatches


def _default_timeout(kind: OutputKind) -> Seconds:
    if kind == "video":
        return CONFIGS.VIDEO_GENERATION_TIMEOUT_SECONDS
    return CONFIGS.IMAGE_GENERATION_TIMEOUT_SECONDS


def _normalize_media_ids(media_ids: Iterable[str]) -> tuple[MediaId, ...]:
    if isinstance(media_ids, str):
        raise TypeError("expected an iterable of media IDs, not a single string")
    return tuple(normalize_media_id(media_id) for media_id in media_ids)


async def _build_file_payload(file: Path) -> tuple[Path, UploadToken, FilePayload]:
    """Read one media file without blocking the asyncio event loop.

    Returns the file, the unique token its upload name is built from, and
    the payload.
    """
    try:
        contents = await asyncio.to_thread(file.read_bytes)
    except OSError as error:
        raise MediaFileReadError(file) from error

    mime_type, _ = mimetypes.guess_file_type(file)
    # Hex only, so the token survives Flow stripping the extension or
    # punctuation from the displayed name.
    token = uuid4().hex
    return (
        file,
        token,
        {
            "name": f"{token}{file.suffix.lower()}",
            "mimeType": mime_type or "application/octet-stream",
            "buffer": contents,
        },
    )


# Labels may be truncated or re-punctuated; a 16-hex-digit prefix (64 random
# bits) still identifies an upload unambiguously.
_UPLOAD_TOKEN_MATCH_LENGTH: Final = 16


def _upload_token_in(
    labels: Iterable[TileLabel], tokens: Iterable[UploadToken]
) -> UploadToken | None:
    """Return the first upload token found in any of a tile's labels."""
    compact = " ".join(re.sub(r"[^0-9a-z]", "", label.lower()) for label in labels)
    return next(
        (token for token in tokens if token[:_UPLOAD_TOKEN_MATCH_LENGTH] in compact),
        None,
    )


def _match_by_kind(
    missing: Mapping[UploadToken, MediaKind], tiles: Iterable[_Tile]
) -> dict[UploadToken, MediaId]:
    """Pair unnamed uploads with unnamed tiles where the kind is unambiguous."""
    tokens_by_kind: dict[MediaKind, list[UploadToken]] = {}
    for token, kind in missing.items():
        tokens_by_kind.setdefault(kind, []).append(token)
    ids_by_kind: dict[MediaKind, list[MediaId]] = {}
    for tile in tiles:
        if tile.finished_id is not None:
            ids_by_kind.setdefault(tile.item.kind, []).append(tile.finished_id)
    return {
        tokens[0]: ids_by_kind[kind][0]
        for kind, tokens in tokens_by_kind.items()
        if len(tokens) == 1 and len(ids_by_kind.get(kind, ())) == 1
    }
