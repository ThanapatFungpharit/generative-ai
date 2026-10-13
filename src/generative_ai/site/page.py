"""Lifecycle plumbing shared by every site's page facade."""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import TYPE_CHECKING, Concatenate, Final

from web_page.utils import is_str_list

from generative_ai.site.exceptions import UnexpectedPageStateError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from web_page import WebPage
    from web_page.types import ResultName

    from generative_ai.site.protocols import BrowserPage
    from generative_ai.site.types import Seconds

CLEANUP_TIMEOUT_SECONDS: Final[Seconds] = 2.0


class SitePage:
    """Base for a domain facade over one browser page of a site.

    Subclasses add the site's operations, decorate public ones with
    :func:`page_required`, and implement :meth:`_closed_error`.
    """

    __slots__ = ("_operation_lock", "_page", "_web_page")

    def __init__(self, page: BrowserPage, web_page: WebPage) -> None:
        self._page: BrowserPage | None = page
        self._web_page = web_page
        self._operation_lock = asyncio.Lock()

    @property
    def is_closed(self) -> bool:
        """Whether this facade no longer has a usable browser page."""
        return self._page is None or self._page.is_closed()

    def _closed_error(self) -> Exception:
        """The error raised when an operation needs a page that is gone."""
        raise NotImplementedError

    def _require_page(self) -> BrowserPage:
        page = self._page
        if page is None or page.is_closed():
            raise self._closed_error()
        return page


def page_required[S: SitePage, **P, R](
    method: Callable[Concatenate[S, P], Awaitable[R]],
) -> Callable[Concatenate[S, P], Awaitable[R]]:
    """Serialise a public async operation and require a live page."""

    @wraps(method)
    async def guarded(page_facade: S, *args: P.args, **kwargs: P.kwargs) -> R:
        async with page_facade._operation_lock:
            page_facade._require_page()
            return await method(page_facade, *args, **kwargs)

    return guarded


async def close_page_and_report_failure(
    page: BrowserPage, timeout: Seconds = CLEANUP_TIMEOUT_SECONDS
) -> Exception | None:
    """Release a page and return, rather than hide, any cleanup failure."""
    if page.is_closed():
        return None

    try:
        async with asyncio.timeout(timeout):
            await page.close()
    except Exception as error:  # noqa: BLE001 - returned to the caller
        return error

    if not page.is_closed():
        return UnexpectedPageStateError(
            "Playwright returned from page.close(), but the page remained open."
        )
    return None


def note_cleanup_failure(
    operation_error: BaseException, cleanup_error: Exception | None
) -> None:
    """Attach a secondary cleanup failure without replacing the primary error."""
    if cleanup_error is not None:
        operation_error.add_note(
            f"Page cleanup also failed: {type(cleanup_error).__name__}: {cleanup_error}"
        )


def text_result(results: Mapping[ResultName, object], name: ResultName) -> str:
    """Return a text value saved by a manifest retrieval step."""
    value = results[name]
    if not isinstance(value, str):
        raise UnexpectedPageStateError(f"Manifest result {name!r} is not text.")
    return value


def texts_result(
    results: Mapping[ResultName, object], name: ResultName
) -> tuple[str, ...]:
    """Return the texts saved by a manifest ``get_all_texts`` step."""
    value = results[name]
    if not is_str_list(value):
        raise UnexpectedPageStateError(
            f"Manifest result {name!r} is not a list of texts."
        )
    return tuple(value)
