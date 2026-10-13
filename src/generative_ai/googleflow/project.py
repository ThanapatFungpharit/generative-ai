from __future__ import annotations

import asyncio
import mimetypes
import re
from collections.abc import Awaitable, Callable, Iterable
from functools import wraps
from pathlib import Path
from typing import Concatenate, Self
from urllib.parse import urlparse
from uuid import uuid4

from playwright.async_api import BrowserContext, FilePayload, Locator, Page, Response
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from generative_ai.googleflow.constants import CONFIGS, SELECTORS, URLS
from generative_ai.googleflow.exceptions import (
    MediaFileReadError,
    MediaUploadError,
    ProjectClosedError,
    ProjectCloseError,
    ProjectCreationError,
    ProjectDeletionError,
    ProjectNotFoundError,
    ProjectOpenError,
    ProjectRenameError,
)


class _UnexpectedProjectStateError(RuntimeError):
    """An expected Flow UI postcondition was not reached."""


def _page_required[**P, R](
    method: Callable[Concatenate[GoogleFlowProject, P], Awaitable[R]],
) -> Callable[Concatenate[GoogleFlowProject, P], Awaitable[R]]:
    """Enforce the live-page invariant for public async operations."""

    @wraps(method)
    async def guarded(
        project: GoogleFlowProject,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        async with project._operation_lock:
            project._require_page()
            return await method(project, *args, **kwargs)

    return guarded


class GoogleFlowProject:
    """A domain facade for one Google Flow project browser page.

    Use :meth:`open` or :meth:`new` to construct an instance. Playwright page
    handles and UI-level operations are intentionally not part of this class's
    supported public API.
    """

    __slots__ = ("_operation_lock", "_page", "_project_magic", "_project_url")

    def __init__(self, project_magic: str, page: Page) -> None:
        self._project_magic = _normalize_project_magic(project_magic)
        self._project_url = f"{URLS.project}{self._project_magic}"
        self._page: Page | None = page
        self._operation_lock = asyncio.Lock()

    @property
    def project_magic(self) -> str:
        """The stable identifier contained in this project's Flow URL."""
        return self._project_magic

    @property
    def project_url(self) -> str:
        """The canonical browser URL for this project."""
        return self._project_url

    @property
    def is_closed(self) -> bool:
        """Whether this facade no longer has a usable browser page."""
        return self._page is None or self._page.is_closed()

    @classmethod
    async def open(cls, project_magic: str, context: BrowserContext) -> Self:
        """Open an existing project by its Flow identifier.

        Raises:
            ProjectNotFoundError: Flow redirects the project URL to its 404 page.
            ProjectOpenError: Navigation fails, reaches an unexpected URL, or
                does not produce a usable project workspace.
        """
        project_magic = _normalize_project_magic(project_magic)
        page: Page | None = None

        try:
            page = await context.new_page()
            project = cls(project_magic, page)
            await page.goto(
                project.project_url,
                wait_until="domcontentloaded",
                timeout=CONFIGS.NAVIGATION_TIMEOUT_MS,
            )
            if _is_not_found_url(page.url):
                raise ProjectNotFoundError(project.project_magic)
            if not _is_project_url(page.url, project.project_magic):
                raise ProjectOpenError(project.project_magic)

            await page.locator(SELECTORS.open_add_menu_button).wait_for(
                state="visible", timeout=CONFIGS.NAVIGATION_TIMEOUT_MS
            )
            if _is_not_found_url(page.url):
                raise ProjectNotFoundError(project.project_magic)
            if not _is_project_url(page.url, project.project_magic):
                raise ProjectOpenError(project.project_magic)
        except PlaywrightError as error:
            failure = ProjectOpenError(project_magic)
            if page is not None:
                was_not_found = _is_not_found_url(page.url)
                cleanup_error = await _close_page_and_report_failure(page)
                if was_not_found:
                    failure = ProjectNotFoundError(project_magic)
                _note_cleanup_failure(failure, cleanup_error)
            raise failure from error
        except BaseException as error:
            if page is not None:
                cleanup_error = await _close_page_and_report_failure(page)
                _note_cleanup_failure(error, cleanup_error)
            raise
        return project

    @classmethod
    async def new(cls, context: BrowserContext) -> Self:
        """Create a new Google Flow project in ``context``.

        Raises:
            ProjectCreationError: The creation controls or resulting project
                URL cannot be resolved.
        """
        page: Page | None = None

        try:
            page = await context.new_page()
            await page.goto(
                URLS.base,
                wait_until="domcontentloaded",
                timeout=CONFIGS.NAVIGATION_TIMEOUT_MS,
            )
            new_project_button = page.locator(SELECTORS.new_project_button)
            await _wait_until_interactable(new_project_button)
            await new_project_button.click()
            await page.wait_for_url(
                f"{URLS.project}*", timeout=CONFIGS.NAVIGATION_TIMEOUT_MS
            )
            await page.locator(SELECTORS.open_add_menu_button).wait_for(
                state="visible", timeout=CONFIGS.NAVIGATION_TIMEOUT_MS
            )
            project_magic = _project_magic_from_url(page.url)
            return cls(project_magic, page)
        except PlaywrightTimeoutError as error:
            failure = ProjectCreationError(
                "Timed out while waiting for Google Flow to create a project."
            )
            if page is not None:
                cleanup_error = await _close_page_and_report_failure(page)
                _note_cleanup_failure(failure, cleanup_error)
            raise failure from error
        except PlaywrightError as error:
            failure = ProjectCreationError("Google Flow could not create a project.")
            if page is not None:
                cleanup_error = await _close_page_and_report_failure(page)
                _note_cleanup_failure(failure, cleanup_error)
            raise failure from error
        except BaseException as error:
            if page is not None:
                cleanup_error = await _close_page_and_report_failure(page)
                _note_cleanup_failure(error, cleanup_error)
            raise

    @_page_required
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

    @_page_required
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

        page = self._require_page()
        try:
            await page.goto(
                URLS.base,
                wait_until="domcontentloaded",
                timeout=CONFIGS.NAVIGATION_TIMEOUT_MS,
            )
            project_card = self._project_card()
            await project_card.wait_for(state="visible")
            edit_title_button = project_card.locator(SELECTORS.edit_title_button)
            await _wait_until_interactable(edit_title_button)
            await edit_title_button.click()

            title_input = project_card.locator(SELECTORS.title_input)
            await _wait_until_interactable(title_input)
            await title_input.fill(new_name)
            await title_input.press("Enter")
            await title_input.wait_for(state="hidden")
            await (
                project_card.locator(SELECTORS.project_title)
                .get_by_text(new_name, exact=True)
                .wait_for(state="visible")
            )
            await page.goto(
                self.project_url,
                wait_until="domcontentloaded",
                timeout=CONFIGS.NAVIGATION_TIMEOUT_MS,
            )
            await page.locator(SELECTORS.open_add_menu_button).wait_for(
                state="visible", timeout=CONFIGS.NAVIGATION_TIMEOUT_MS
            )
            if not _is_project_url(page.url, self.project_magic):
                raise _UnexpectedProjectStateError(
                    "Flow left the expected project after renaming it."
                )
        except (PlaywrightError, _UnexpectedProjectStateError) as error:
            failure = ProjectRenameError(self.project_magic)
            await self._restore_project_page_after_failure(failure)
            raise failure from error

    @_page_required
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
            await page.goto(
                URLS.base,
                wait_until="domcontentloaded",
                timeout=CONFIGS.NAVIGATION_TIMEOUT_MS,
            )
            project_card = self._project_card()
            await project_card.wait_for(state="visible")
            delete_project_button = project_card.locator(
                SELECTORS.delete_project_button
            )
            await _wait_until_interactable(delete_project_button)
            await delete_project_button.click()

            confirm_delete_button = page.locator(
                SELECTORS.confirm_delete_project_button
            )
            await _wait_until_interactable(confirm_delete_button)
            confirmation_attempted = True
            await confirm_delete_button.click()
            await project_card.wait_for(state="detached")

            await self._verify_deletion(page)
        except (PlaywrightError, _UnexpectedProjectStateError) as error:
            failure = ProjectDeletionError(self.project_magic)
            if confirmation_attempted:
                failure.add_note(
                    "Deletion confirmation was submitted, but the remote "
                    "deletion could not be verified; the project state is unknown."
                )
                self._page = None
                cleanup_error = await _close_page_and_report_failure(page)
                _note_cleanup_failure(failure, cleanup_error)
            else:
                await self._restore_project_page_after_failure(failure)
            raise failure from error

        # The remote deletion has been verified. The facade must not be reused.
        self._page = None
        cleanup_error = await _close_page_and_report_failure(page)
        if cleanup_error is not None:
            failure = ProjectCloseError(self.project_magic)
            failure.add_note(
                "The remote project deletion was verified before page cleanup failed."
            )
            raise failure from cleanup_error

    @_page_required
    async def upload_media(self, files: Iterable[Path]) -> dict[Path, str]:
        """Upload media and return a mapping from each path to its media ID.

        File contents are read concurrently in worker threads. Duplicate paths
        are rejected because a mapping cannot represent them without data loss.

        Raises:
            ValueError: ``files`` contains the same path more than once.
            MediaFileReadError: A file cannot be read.
            MediaUploadError: Flow does not confirm every uploaded preview in
                the allotted time, or the workspace cannot be restored to a
                known state after the upload.
        """
        media_files = tuple(files)
        if len(set(media_files)) != len(media_files):
            raise ValueError("files must not contain duplicate paths")
        if not media_files:
            return {}

        built_payloads = await asyncio.gather(
            *(_build_file_payload(file) for file in media_files)
        )
        file_to_media_id: dict[Path, str] = {}
        payloads: list[FilePayload] = []
        for file, payload in built_payloads:
            file_to_media_id[file] = payload["name"]
            payloads.append(payload)

        uploads_complete = asyncio.Event()
        pending_payloads = len(payloads)
        seen_previews: set[str] = set()
        preview_tasks: set[asyncio.Task[None]] = set()
        preview_errors: list[PlaywrightError] = []
        page = self._require_page()

        async def confirm_preview_response(response: Response) -> None:
            nonlocal pending_payloads
            try:
                await response.finished()
            except PlaywrightError as error:
                preview_errors.append(error)
                return

            if pending_payloads == 0 or response.url in seen_previews:
                return
            seen_previews.add(response.url)
            pending_payloads -= 1
            if pending_payloads == 0:
                uploads_complete.set()

        def observe_preview_response(response: Response) -> None:
            if not response.ok or not response.url.startswith(URLS.media_preview):
                return
            preview_tasks.add(asyncio.create_task(confirm_preview_response(response)))

        listener_registered = False
        operation_error: BaseException | None = None
        try:
            await self._open_add_menu()
            upload_media_button = page.locator(SELECTORS.upload_media_button)
            await _wait_until_interactable(upload_media_button)
            async with page.expect_file_chooser() as chooser_info:
                await upload_media_button.click()

            chooser = await chooser_info.value
            # Register as late as possible so previews already on the page do
            # not get mistaken for uploads from this operation.
            page.on("response", observe_preview_response)
            listener_registered = True
            await chooser.set_files(payloads)

            try:
                consent_button = page.get_by_role("button", name="I agree")
                await _wait_until_interactable(
                    consent_button,
                    timeout=CONFIGS.OPTIONAL_CONSENT_TIMEOUT_MS,
                )
                await consent_button.click(timeout=CONFIGS.OPTIONAL_CONSENT_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                # This consent dialog is legitimately absent for some accounts.
                pass

            try:
                await asyncio.wait_for(
                    uploads_complete.wait(), timeout=CONFIGS.UPLOAD_TIMEOUT_SECONDS
                )
            except TimeoutError as error:
                failure = MediaUploadError(
                    "Google Flow did not confirm "
                    f"{pending_payloads} uploaded media item(s)."
                )
                for preview_error in preview_errors:
                    failure.add_note(
                        "A matching preview response failed before completion: "
                        f"{preview_error}"
                    )
                raise failure from error
        except PlaywrightError as error:
            failure = MediaUploadError("Google Flow could not upload the media.")
            operation_error = failure
            raise failure from error
        except BaseException as error:
            operation_error = error
            raise
        finally:
            cleanup_errors: list[Exception] = []
            if listener_registered:
                try:
                    page.remove_listener("response", observe_preview_response)
                except Exception as error:  # noqa: BLE001 - reported below
                    cleanup_errors.append(error)

            for task in preview_tasks:
                if not task.done():
                    task.cancel()
            task_results = await asyncio.gather(*preview_tasks, return_exceptions=True)
            for result in task_results:
                if isinstance(result, Exception):
                    cleanup_errors.append(result)

            try:
                async with asyncio.timeout(CONFIGS.CLEANUP_TIMEOUT_SECONDS):
                    await self._close_add_menu()
            except Exception as error:  # noqa: BLE001 - reported below
                cleanup_errors.append(error)

            if cleanup_errors:
                if operation_error is not None:
                    for cleanup_error in cleanup_errors:
                        operation_error.add_note(
                            "Upload cleanup also failed: "
                            f"{type(cleanup_error).__name__}: {cleanup_error}"
                        )
                else:
                    failure = MediaUploadError(
                        "The media upload completed, but the workspace could not "
                        "be restored to a known state."
                    )
                    for cleanup_error in cleanup_errors[1:]:
                        failure.add_note(
                            "Additional cleanup failure: "
                            f"{type(cleanup_error).__name__}: {cleanup_error}"
                        )
                    raise failure from cleanup_errors[0]

        return file_to_media_id

    async def send_prompt(self, prompt: str) -> None:
        pass

    def _require_page(self) -> Page:
        page = self._page
        if page is None or page.is_closed():
            raise ProjectClosedError(self.project_magic)
        return page

    def _add_menu_panel(self) -> Locator:
        return self._require_page().locator(SELECTORS.add_menu_popover)

    def _project_card(self) -> Locator:
        page = self._require_page()
        project_path = urlparse(self.project_url).path
        project_link = page.locator(
            f'a[href="{project_path}"], a[href="{self.project_url}"]'
        )
        return page.locator(SELECTORS.project_card).filter(has=project_link)

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
            await page.goto(
                self.project_url,
                wait_until="domcontentloaded",
                timeout=CONFIGS.RECOVERY_TIMEOUT_MS,
            )
            await page.locator(SELECTORS.open_add_menu_button).wait_for(
                state="visible", timeout=CONFIGS.RECOVERY_TIMEOUT_MS
            )
            if not _is_project_url(page.url, self.project_magic):
                raise _UnexpectedProjectStateError(
                    "Flow did not return to the expected project workspace."
                )
        except Exception as recovery_error:  # noqa: BLE001 - attached below
            operation_error.add_note(
                "Workspace recovery also failed: "
                f"{type(recovery_error).__name__}: {recovery_error}"
            )
            self._page = None
            cleanup_error = await _close_page_and_report_failure(page)
            _note_cleanup_failure(operation_error, cleanup_error)

    async def _verify_deletion(self, page: Page) -> None:
        """Verify that the project URL resolves to Flow's not-found route."""
        # Card removal can be an optimistic UI update, so independently load
        # the former project URL and verify its remote postcondition.
        try:
            await page.goto(
                self.project_url,
                wait_until="domcontentloaded",
                timeout=CONFIGS.NAVIGATION_TIMEOUT_MS,
            )
        except PlaywrightError:
            # Client-side redirects can abort the original navigation. That is
            # acceptable only when the resulting route is already the 404 page.
            if not _is_not_found_url(page.url):
                raise

        if not _is_not_found_url(page.url):
            try:
                await page.wait_for_url(
                    "**/404*", timeout=CONFIGS.NAVIGATION_TIMEOUT_MS
                )
            except PlaywrightError:
                if not _is_not_found_url(page.url):
                    raise

        if not _is_not_found_url(page.url):
            raise _UnexpectedProjectStateError(
                "Flow did not redirect the deleted project to its 404 route."
            )

    async def _open_add_menu(self) -> None:
        panel = self._add_menu_panel()
        if await panel.is_visible():
            return

        page = self._require_page()
        trigger = page.locator(SELECTORS.open_add_menu_button)
        await _wait_until_interactable(trigger)
        await trigger.click()
        await panel.wait_for(state="visible")

    async def _close_add_menu(self) -> None:
        panel = self._add_menu_panel()
        page = self._require_page()

        for attempt in range(CONFIGS.MENU_CLOSE_MAX_ATTEMPTS):
            if not await panel.is_visible():
                return

            await page.keyboard.press("Escape")
            try:
                await panel.wait_for(
                    state="hidden",
                    timeout=CONFIGS.MENU_CLOSE_RETRY_TIMEOUT_MS,
                )
                return
            except PlaywrightTimeoutError:
                if attempt == CONFIGS.MENU_CLOSE_MAX_ATTEMPTS - 1:
                    raise


# Helpers


async def _wait_until_interactable(
    locator: Locator, *, timeout: float | None = None
) -> None:
    """Wait until a locator is visible before its first interaction."""
    await locator.wait_for(state="visible", timeout=timeout)


async def _build_file_payload(file: Path) -> tuple[Path, FilePayload]:
    """Read one media file without blocking the asyncio event loop."""
    try:
        contents = await asyncio.to_thread(file.read_bytes)
    except OSError as error:
        raise MediaFileReadError(file) from error

    mime_type, _ = mimetypes.guess_file_type(file)
    media_id = f"{uuid4()}{file.suffix}"
    return file, {
        "name": media_id,
        "mimeType": mime_type or "application/octet-stream",
        "buffer": contents,
    }


async def _close_page_and_report_failure(page: Page) -> Exception | None:
    """Release a page and return, rather than hide, any cleanup failure."""
    if page.is_closed():
        return None

    try:
        async with asyncio.timeout(CONFIGS.CLEANUP_TIMEOUT_SECONDS):
            await page.close()
    except Exception as error:  # noqa: BLE001 - returned to the caller
        return error

    if not page.is_closed():
        return _UnexpectedProjectStateError(
            "Playwright returned from page.close(), but the page remained open."
        )
    return None


def _note_cleanup_failure(
    operation_error: BaseException, cleanup_error: Exception | None
) -> None:
    """Attach a secondary cleanup failure without replacing the primary error."""
    if cleanup_error is not None:
        operation_error.add_note(
            f"Page cleanup also failed: {type(cleanup_error).__name__}: {cleanup_error}"
        )


def _project_magic_from_url(url: str) -> str:
    project_path = urlparse(URLS.project).path
    current_path = urlparse(url).path
    if not current_path.startswith(project_path):
        raise ProjectCreationError(
            f"Google Flow returned an unexpected project URL: {url}"
        )

    project_magic = current_path.removeprefix(project_path).removesuffix("/")
    if not project_magic or "/" in project_magic:
        raise ProjectCreationError(
            f"Google Flow returned an unexpected project URL: {url}"
        )

    try:
        return _normalize_project_magic(project_magic)
    except ValueError as error:
        raise ProjectCreationError(
            f"Google Flow returned an unexpected project URL: {url}"
        ) from error


def _normalize_project_magic(project_magic: str) -> str:
    normalized = project_magic.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        raise ValueError(
            "project_magic must contain only ASCII letters, digits, hyphens, "
            "and underscores"
        )
    return normalized


def _is_project_url(url: str, project_magic: str) -> bool:
    """Return whether ``url`` identifies the expected Flow project."""
    parsed_url = urlparse(url)
    parsed_base = urlparse(URLS.project)
    return (
        parsed_url.scheme == parsed_base.scheme
        and parsed_url.netloc == parsed_base.netloc
        and parsed_url.path.rstrip("/")
        == f"{parsed_base.path}{project_magic}".rstrip("/")
    )


def _is_not_found_url(url: str) -> bool:
    """Return whether Flow redirected to its not-found route."""
    return urlparse(url).path.rstrip("/") == "/404"
