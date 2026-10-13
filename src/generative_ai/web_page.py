from __future__ import annotations

from asyncio import Lock
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Concatenate

from playwright.async_api import Page

from generative_ai.exceptions import WebPageClosedError

type PageMethod[**P, R] = Callable[Concatenate[WebPage, Page, P], Awaitable[R]]
type GuardedMethod[**P, R] = Callable[Concatenate[WebPage, P], Awaitable[R]]


def use_page[**P, R](method: PageMethod[P, R]) -> GuardedMethod[P, R]:
    @wraps(method)
    async def guarded(webpage: WebPage, *args: P.args, **kwargs: P.kwargs) -> R:
        async with webpage._locked_page() as page:
            return await method(webpage, page, *args, **kwargs)

    return guarded


class WebPage:
    def __init__(self) -> None:
        self._page: Page | None = None
        self._operation_lock = Lock()

    @property
    def is_closed(self) -> bool:
        return self._page is None or self._page.is_closed()

    @asynccontextmanager
    async def _locked_page(self) -> AsyncGenerator[Page]:
        """Acquire the lock and yield the live page, or raise if closed."""
        async with self._operation_lock:
            page = self._page
            if page is None or page.is_closed():
                raise WebPageClosedError()
            yield page

    @asynccontextmanager
    async def navigated(self, url: str) -> AsyncGenerator[Page]:
        """Lock, navigate to `url`, and yield the page.

        Don't call this from inside a @use_page method: the lock isn't
        reentrant and it will deadlock.
        """
        async with self._locked_page() as page:
            await page.goto(url, wait_until="domcontentloaded")
            yield page
