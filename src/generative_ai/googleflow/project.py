from __future__ import annotations

import asyncio
import mimetypes
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self, TypedDict, TypeIs, override
from urllib.parse import urlparse
from uuid import uuid4

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from web_page.exceptions import ActionFailed
from web_page.utils import is_str_list

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
    GenerationMode,
    GenerationSettings,
    MediaItem,
    VideoInput,
)
from generative_ai.googleflow.utils import (
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


class GoogleFlowProject(SitePage):
    """A domain facade for one Google Flow project browser page.

    Use :meth:`open` or :meth:`new` to construct an instance. Linear UI flows
    are declared as ``web_page`` manifests under ``manifests/``; this class
    drives them and handles what manifests cannot express (project- and
    media-scoped locators, file choosers, branching, and polling).
    """

    __slots__ = ("_project_magic", "_project_url")

    def __init__(
        self, project_magic: str, page: BrowserPage, web_page: WebPage
    ) -> None:
        super().__init__(page, web_page)
        self._project_magic = normalize_project_magic(project_magic)
        self._project_url = f"{URLS.project}{self._project_magic}"

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
                cleanup_error = await close_page_and_report_failure(page)
                note_cleanup_failure(failure, cleanup_error)
            raise failure from error
        except PlaywrightError as error:
            failure = ProjectCreationError("Google Flow could not create a project.")
            if page is not None:
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
        finally:
            self._page = None

        if not page.is_closed():
            raise ProjectCloseError(self.project_magic)

    @page_required
    async def rename(self, new_name: str) -> None:
        """Rename this project and return its page to the project workspace.

        Leading and trailing whitespace is removed from ``new_name``. Success
        means the projects page displayed the new name and the original project
        workspace was loaded again.

        Raises:
            ValueError: ``new_name`` is empty or contains only whitespace.
            ProjectRenameError: Google Flow does not complete the rename.
        """
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("new_name must not be empty or whitespace")

        try:
            await self._web_page.run(
                "rename_project",
                {
                    "base_url": URLS.base,
                    **self._project_card_params(),
                    "new_name": new_name,
                },
            )
            await self._return_to_workspace()
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
        card_params = self._project_card_params()
        try:
            await self._web_page.run(
                "open_delete_project_dialog", {"base_url": URLS.base, **card_params}
            )
            confirmation_attempted = True
            await self._web_page.run("confirm_delete_project", card_params)
            await self._verify_deletion(page)
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

        operation_error: BaseException | None = None
        try:
            await self._ensure_on_workspace()
            await self._web_page.run("wait_for_workspace")
            known_ids = await self._known_media_ids()
            await self._web_page.run("upload_media", {"files": payloads})
            media_id_by_token = await self._wait_for_uploaded_media(
                kind_by_token, known_ids
            )
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

        Raises:
            GenerationSettingsError: Flow does not offer a requested value for
                the current mode or account, or the popover cannot be driven.
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

        Raises:
            GenerationSettingsError: The Agent chip cannot be toggled.
        """
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
            GenerationSettingsError: A setting or reference cannot be applied.
            MediaNotFoundError: A reference is not in the project's asset picker.
            GenerationError: Flow does not start or finish the generation in
                time, or finishes without producing media.
        """
        settings = GenerationSettings(
            mode=GenerationMode.IMAGE,
            aspect_ratio=aspect_ratio,
            model=model,
            outputs=outputs,
        )
        return await self._generate(
            prompt,
            settings,
            expected_kind="image",
            ingredients=_normalize_media_ids(references),
            timeout=(
                CONFIGS.IMAGE_GENERATION_TIMEOUT_SECONDS if timeout is None else timeout
            ),
        )

    @page_required
    async def generate_video(
        self,
        prompt: PromptText,
        *,
        model: ModelLabel | None = None,
        aspect_ratio: AspectRatio | None = None,
        outputs: OutputCount | None = None,
        start_frame: MediaId | None = None,
        end_frame: MediaId | None = None,
        ingredients: Iterable[MediaId] = (),
        timeout: Seconds | None = None,
    ) -> tuple[MediaId, ...]:
        """Generate videos and return the new media IDs, newest first.

        Passing ``start_frame`` and/or ``end_frame`` (image media IDs) uses
        Flow's Frames mode; otherwise Ingredients mode is used, with
        ``ingredients`` as optional reference media IDs. Both frame slots must
        be empty beforehand. Other arguments behave as in
        :meth:`generate_image`. Video generation spends account credits.

        Raises:
            ValueError: ``prompt`` is empty, a media ID is malformed, or frames
                are combined with ingredients.
            GenerationSettingsError: A setting, frame or ingredient cannot be
                applied.
            MediaNotFoundError: A frame or ingredient is not in the project's
                asset picker.
            GenerationError: Flow does not start or finish the generation in
                time, or finishes without producing media.
        """
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

        settings = GenerationSettings(
            mode=GenerationMode.VIDEO,
            video_input=VideoInput.FRAMES if frames else VideoInput.INGREDIENTS,
            aspect_ratio=aspect_ratio,
            model=model,
            outputs=outputs,
        )
        return await self._generate(
            prompt,
            settings,
            expected_kind="video",
            ingredients=ingredient_ids,
            frames=frames,
            timeout=(
                CONFIGS.VIDEO_GENERATION_TIMEOUT_SECONDS if timeout is None else timeout
            ),
        )

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
            normalize_media_id(media_id), "Favourite", "favourite"
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

        Raises:
            MediaNotFoundError: No grid tile has ``media_id``.
            MediaActionError: Flow does not remove the tile from the grid.
        """
        media_id = normalize_media_id(media_id)
        await self._run_media_menu_action(media_id, "Move to bin", "move to bin")
        try:
            await self._web_page.run("wait_for_media_removed", {"media_id": media_id})
        except PlaywrightError as error:
            raise MediaActionError(media_id, "move to bin") from error

    @page_required
    async def restore_from_bin(self, media_id: MediaId) -> None:
        """Restore a binned media item and return to the project workspace.

        Raises:
            MediaNotFoundError: The bin has no tile with ``media_id``.
            MediaActionError: Flow does not restore the item.
        """
        media_id = normalize_media_id(media_id)
        try:
            await self._web_page.run(
                "restore_from_bin",
                {"trash_url": f"{self.project_url}/trash", "media_id": media_id},
            )
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

        ``quality`` is the leading label of a Download submenu entry, such as
        ``"1K"`` or ``"2K"`` for images; ``None`` picks the first enabled one.
        If ``destination`` is an existing directory, Flow's suggested file
        name is used inside it.

        Raises:
            MediaNotFoundError: No grid tile has ``media_id``.
            MediaActionError: The quality is unavailable or the download fails.
        """
        media_id = normalize_media_id(media_id)
        try:
            await self._ensure_on_workspace()
            results = await self._web_page.run(
                "download_media",
                {
                    "media_id": media_id,
                    "quality": quality or "",
                    "to": str(destination),
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

    def _project_card_params(self) -> Mapping[ParamName, str]:
        """Params for the selectors that find this project's card by its link."""
        return {
            "project_path": urlparse(self.project_url).path,
            "project_url": self.project_url,
        }

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
        deadline = loop.time() + CONFIGS.UPLOAD_TIMEOUT_SECONDS
        settled_at: float | None = None
        while True:
            tiles = await self._snapshot_media()
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

    async def _generate(
        self,
        prompt: PromptText,
        settings: GenerationSettings,
        *,
        expected_kind: OutputKind,
        ingredients: Iterable[MediaId] = (),
        frames: Mapping[FrameSlot, MediaId] | None = None,
        timeout: Seconds,
    ) -> tuple[MediaId, ...]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt must not be empty or whitespace")

        try:
            await self._ensure_on_workspace()
            await self._set_agent_mode(False)
            await self._apply_settings(settings)
            await self._clear_prompt()
            for slot, media_id in (frames or {}).items():
                await self._fill_frame(slot, media_id)
            for media_id in ingredients:
                await self._add_ingredient(media_id)
            await self._fill_prompt(prompt)
            return await self._submit_generation(timeout, expected_kind=expected_kind)
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = GenerationError("Google Flow could not run the generation.")
            await self._note_flow_message(failure)
            await self._close_overlays_noting_failure(failure)
            raise failure from error
        except GoogleFlowError as error:
            await self._close_overlays_noting_failure(error)
            raise

    async def _set_agent_mode(self, enabled: bool) -> None:
        await self._web_page.run(
            "set_agent_mode", {"pressed": "true" if enabled else "false"}
        )

    async def _read_settings(self) -> GenerationSettings:
        await self._web_page.run("open_generation_settings")
        results = await self._web_page.run("read_generation_settings")
        model = text_result(results, "model")
        await self._close_overlays()

        mode: GenerationMode | None = None
        video_input: VideoInput | None = None
        aspect_ratio: AspectRatio | None = None
        outputs: OutputCount | None = None
        for label in texts_result(results, "checked"):
            if label in GenerationMode:
                mode = GenerationMode(label)
            elif label in VideoInput:
                video_input = VideoInput(label)
            elif match := re.fullmatch(r"x([1-4])", label):
                outputs = int(match[1])
            else:
                aspect_ratio = label
        return GenerationSettings(
            mode=mode,
            video_input=video_input,
            aspect_ratio=aspect_ratio,
            model=model or None,
            outputs=outputs,
        )

    async def _apply_settings(self, settings: GenerationSettings) -> None:
        if settings == GenerationSettings():
            return

        await self._web_page.run("open_generation_settings")
        # Mode first: it decides which of the other options are rendered.
        if settings.mode is not None:
            await self._select_setting("mode", settings.mode)
        if settings.video_input is not None:
            await self._select_setting("video input", settings.video_input)
        if settings.aspect_ratio is not None:
            await self._select_setting("aspect ratio", settings.aspect_ratio)
        if settings.model is not None:
            await self._select_model(settings.model)
        if settings.outputs is not None:
            await self._select_setting("output count", f"x{settings.outputs}")
        await self._close_overlays()

    async def _select_setting(self, field: SettingField, label: SettingLabel) -> None:
        """Check the settings toggle ``label``, or report the options Flow offers."""
        try:
            await self._web_page.run("select_setting", {"label": label})
        except ActionFailed as error:
            available = texts_result(error.results, "available")
            raise GenerationSettingsError(
                f"Google Flow does not offer {field} {label!r} here; "
                f"available options: {', '.join(available)}."
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

    async def _fill_frame(self, slot: FrameSlot, media_id: MediaId) -> None:
        """Fill the empty frame slot labelled ``slot`` (Start or End)."""
        try:
            await self._web_page.run("open_frame_slot", {"slot": slot})
        except ActionFailed as error:
            raise GenerationSettingsError(error.message) from error
        await self._pick_asset(media_id)
        await self._web_page.run("wait_for_frame_filled", {"slot": slot})

    async def _add_ingredient(self, media_id: MediaId) -> None:
        chips = self._require_page().locator(SELECTORS["ingredient_chip"])
        existing_chips = await chips.count()
        try:
            await self._web_page.run("open_ingredient_picker")
        except ActionFailed as error:
            raise GenerationSettingsError(error.message) from error
        await self._pick_asset(media_id)
        await self._web_page.run("wait_for_ingredient", {"index": str(existing_chips)})
        await self._close_overlays()

    async def _pick_asset(self, media_id: MediaId) -> None:
        """Choose ``media_id`` in the open asset picker."""
        try:
            await self._web_page.run("pick_asset", {"media_id": media_id})
        except ActionFailed as error:
            raise MediaNotFoundError(media_id) from error

    async def _fill_prompt(self, prompt: PromptText) -> None:
        results = await self._web_page.run("fill_prompt", {"prompt": prompt})
        if text_result(results, "text").split() != prompt.split():
            raise GenerationError("Google Flow did not accept the prompt text.")

    async def _submit_generation(
        self, timeout: Seconds, *, expected_kind: OutputKind
    ) -> tuple[MediaId, ...]:
        """Start generation and wait for the media tiles it produces.

        Only finished tiles of ``expected_kind`` whose media ID was not in
        the grid before submitting count as output, so uploads, scenes and
        tiles still rendering are never reported.
        """
        page = self._require_page()
        generate_button = page.locator(SELECTORS["generate_button"])
        if await generate_button.is_disabled():
            raise GenerationError(
                "Google Flow did not enable generation for this prompt and inputs."
            )

        trigger_text = await page.locator(SELECTORS["settings_trigger"]).inner_text()
        outputs_match = re.search(r"\bx([1-4])\b", trigger_text)
        expected_outputs = int(outputs_match[1]) if outputs_match else 1

        before = await self._snapshot_media()
        known_ids = {
            tile.item.media_id for tile in before if tile.item.media_id is not None
        }
        pending_before = sum(tile.item.pending for tile in before)
        await generate_button.click()

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        started = False
        settled_at: float | None = None
        while True:
            await asyncio.sleep(CONFIGS.POLL_INTERVAL_SECONDS)
            tiles = await self._snapshot_media()
            new_ids = list(
                dict.fromkeys(
                    tile.finished_id
                    for tile in tiles
                    if tile.finished_id is not None
                    and tile.finished_id not in known_ids
                    and tile.item.kind == expected_kind
                )
            )
            pending = sum(tile.item.pending for tile in tiles)
            started = started or pending > pending_before or bool(new_ids)
            now = loop.time()

            if len(new_ids) >= expected_outputs:
                return tuple(new_ids)
            if started and pending <= pending_before:
                # Flow may drop outputs (e.g. safety filtering); once nothing of
                # ours is pending, return what was produced.
                if new_ids:
                    return tuple(new_ids)
                if settled_at is None:
                    settled_at = now
                if now - settled_at >= CONFIGS.GENERATION_FAILURE_GRACE_SECONDS:
                    failure = GenerationError(
                        "Google Flow finished the generation without producing media."
                    )
                    await self._note_flow_message(failure)
                    raise failure
            else:
                settled_at = None

            if (
                not started
                and now - started_at >= CONFIGS.GENERATION_START_TIMEOUT_SECONDS
            ):
                failure = GenerationError("Google Flow did not start the generation.")
                await self._note_flow_message(failure)
                raise failure
            if now - started_at >= timeout:
                failure = GenerationError(
                    f"Google Flow did not finish the generation within {timeout:g}s."
                )
                if new_ids:
                    failure.add_note(f"Media finished so far: {', '.join(new_ids)}.")
                await self._note_flow_message(failure)
                raise failure

    async def _run_media_menu_action(
        self, media_id: MediaId, item_label: MediaMenuItem, action: MediaAction
    ) -> None:
        """Choose ``item_label`` in a grid tile's context menu."""
        try:
            await self._ensure_on_workspace()
            await self._web_page.run(
                "run_media_menu_item", {"media_id": media_id, "item": item_label}
            )
        except (PlaywrightError, UnexpectedPageStateError) as error:
            failure = MediaActionError(media_id, action)
            await self._note_flow_message(failure)
            await self._close_overlays_noting_failure(failure)
            raise failure from error
        except ActionFailed as error:
            failure = MediaNotFoundError(media_id)
            await self._close_overlays_noting_failure(failure)
            raise failure from error

    async def _note_flow_message(self, error: BaseException) -> None:
        """Attach the text of any visible Flow snackbar to ``error``."""
        try:
            results = await self._web_page.run("read_flow_messages")
        except PlaywrightError:
            return
        for message in texts_result(results, "messages"):
            error.add_note(f"Google Flow reported: {message}")

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


def _ensure_opened(url: Url, project_magic: ProjectMagic) -> None:
    """Raise unless ``url`` is the workspace of the project being opened."""
    if is_not_found_url(url):
        raise ProjectNotFoundError(project_magic)
    if not is_project_url(url, project_magic):
        raise ProjectOpenError(project_magic)


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
