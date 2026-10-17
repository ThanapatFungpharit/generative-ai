"""The browser interface site automation drives.

These are structural types extending ``web_page``'s, so Playwright's
``BrowserContext`` and ``Page`` fit as do drop-in replacements for them. Only
the calls this package makes are listed; return values it ignores are typed
``object``.
"""

from __future__ import annotations

from re import Pattern
from typing import TYPE_CHECKING, Literal, Protocol, Self, TypedDict, override

from web_page import Locator, Page
from web_page.types import Milliseconds

from generative_ai.site.types import Pixels, RawJson

if TYPE_CHECKING:
    from playwright.async_api import Page as ChromiumPage


class ViewportSize(TypedDict):
    width: Pixels
    height: Pixels


class BrowserElement(Protocol):
    """A specific DOM node, retained while grid indexes can change."""

    async def wait_for_element_state(
        self, state: Literal["hidden"], *, timeout: Milliseconds | None = ...
    ) -> object: ...
    async def dispose(self) -> object: ...


class BrowserLocator(Locator, Protocol):
    @property
    def last(self) -> Self: ...

    def filter(
        self,
        *,
        has_text: str | Pattern[str] | None = ...,
        visible: bool | None = ...,
    ) -> Self: ...
    async def is_disabled(self) -> bool: ...
    async def element_handle(self) -> BrowserElement | None: ...


class BrowserPage(Page, Protocol):
    @property
    def context(self) -> BrowserContext: ...

    @property
    def viewport_size(self) -> ViewportSize | None: ...

    @override
    def locator(self, selector: str) -> BrowserLocator: ...
    def is_closed(self) -> bool: ...
    async def close(self) -> object: ...
    async def evaluate(self, expression: str, arg: object = ...) -> RawJson: ...
    async def go_back(
        self, *, wait_until: Literal["commit", "domcontentloaded", "load"] = ...
    ) -> object: ...
    def set_default_timeout(self, timeout: Milliseconds) -> object: ...
    def set_default_navigation_timeout(self, timeout: Milliseconds) -> object: ...
    async def set_viewport_size(self, viewport_size: ViewportSize) -> object: ...


class BrowserSession(Protocol):
    """A page-scoped Chromium DevTools session, released when its page closes."""

    async def send(self, method: str, params: dict[str, object]) -> object: ...
    async def detach(self) -> object: ...


class BrowserContext(Protocol):
    async def new_page(self) -> BrowserPage: ...
    async def new_cdp_session(self, page: ChromiumPage) -> BrowserSession: ...
