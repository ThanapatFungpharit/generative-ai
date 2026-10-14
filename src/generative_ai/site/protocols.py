"""The browser interface site automation drives.

These are structural types extending ``web_page``'s, so Playwright's
``BrowserContext`` and ``Page`` fit as do drop-in replacements for them. Only
the calls this package makes are listed; return values it ignores are typed
``object``.
"""

from __future__ import annotations

from re import Pattern
from typing import Literal, Protocol, Self, TypedDict, override

from web_page import Locator, Page
from web_page.types import Milliseconds

from generative_ai.site.types import Pixels, RawJson


class ViewportSize(TypedDict):
    width: Pixels
    height: Pixels


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


class BrowserPage(Page, Protocol):
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


class BrowserContext(Protocol):
    async def new_page(self) -> BrowserPage: ...
