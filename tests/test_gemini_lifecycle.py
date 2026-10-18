"""Resource ownership for Gemini temporary-chat contexts and deletion."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast, override

import pytest
from playwright.async_api import Error as PlaywrightError

from generative_ai.gemini import GeminiChat, GeminiChatCloseError, GeminiOpenError
from generative_ai.gemini.constants import SITE

if TYPE_CHECKING:
    from web_page import WebPage

    from generative_ai.site import BrowserContext, BrowserPage


class _Page:
    url = "https://gemini.google.com/app"

    def __init__(self, *, fail_close: bool = False) -> None:
        self.closed = False
        self.close_calls = 0
        self.fail_close = fail_close

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise PlaywrightError("close failed")
        self.closed = True


class _TemporaryChat(GeminiChat):
    def __init__(
        self, page: _Page, *, activation_error: BaseException | None = None
    ) -> None:
        super().__init__(cast("BrowserPage", page), cast("WebPage", object()))
        self.activation_error = activation_error

    @override
    async def _activate_temporary(self) -> None:
        if self.activation_error is not None:
            raise self.activation_error
        self._is_temporary = True


def _factory(monkeypatch: pytest.MonkeyPatch, chat: _TemporaryChat) -> None:
    async def new(cls: type[GeminiChat], context: BrowserContext) -> GeminiChat:
        return chat

    monkeypatch.setattr(GeminiChat, "new", classmethod(new))


async def test_temporary_context_closes_its_tab_on_normal_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _Page()
    chat = _TemporaryChat(page)
    _factory(monkeypatch, chat)
    # The context has no close method: only the owned page may be closed.
    async with GeminiChat.temporary(cast("BrowserContext", object())) as temporary:
        assert temporary is chat
        assert temporary.is_temporary
        assert not temporary.is_closed
    assert chat.is_closed
    assert page.close_calls == 1


async def test_temporary_context_preserves_body_exception_and_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _Page(fail_close=True)
    chat = _TemporaryChat(page)
    _factory(monkeypatch, chat)
    primary = ValueError("body failed")
    with pytest.raises(ValueError) as caught:
        async with GeminiChat.temporary(cast("BrowserContext", object())):
            raise primary
    assert caught.value is primary
    assert any("cleanup also failed" in note for note in caught.value.__notes__)
    assert page.close_calls == 1


async def test_temporary_context_closes_after_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _Page()
    chat = _TemporaryChat(page)
    _factory(monkeypatch, chat)
    entered = asyncio.Event()

    async def work() -> None:
        async with GeminiChat.temporary(cast("BrowserContext", object())):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert page.closed
    assert chat.is_closed


@pytest.mark.parametrize("cancelled", [False, True])
async def test_temporary_activation_failure_closes_tab_without_yielding(
    monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    page = _Page()
    primary = (
        asyncio.CancelledError() if cancelled else RuntimeError("activation failed")
    )
    chat = _TemporaryChat(page, activation_error=primary)
    _factory(monkeypatch, chat)
    with pytest.raises(type(primary)) as caught:
        async with GeminiChat.temporary(cast("BrowserContext", object())):
            pytest.fail("An unverified temporary chat must not be yielded")
    assert caught.value is primary
    assert page.closed


async def test_temporary_context_allows_body_to_close_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _Page()
    chat = _TemporaryChat(page)
    _factory(monkeypatch, chat)
    async with GeminiChat.temporary(cast("BrowserContext", object())) as temporary:
        await temporary.close()
    assert page.close_calls == 1


async def test_temporary_context_reports_failed_close_on_normal_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _Page(fail_close=True)
    chat = _TemporaryChat(page)
    _factory(monkeypatch, chat)
    with pytest.raises(GeminiChatCloseError, match="Temporary chat tab"):
        async with GeminiChat.temporary(cast("BrowserContext", object())):
            pass
    assert chat._page is page
    assert not chat.is_closed


async def test_temporary_cleanup_does_not_wait_for_an_abandoned_operation_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _Page()
    chat = _TemporaryChat(page)
    _factory(monkeypatch, chat)
    async with asyncio.timeout(1):
        async with GeminiChat.temporary(cast("BrowserContext", object())):
            await chat._operation_lock.acquire()
    chat._operation_lock.release()
    assert page.closed


async def test_google_verification_is_reported_before_waiting_for_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _Page()

    class Context:
        async def new_page(self) -> BrowserPage:
            return cast("BrowserPage", page)

    class Web:
        async def run(self, action: str, params: object = None) -> dict[str, object]:
            assert action == "open_chat", (
                "A verification page has no composer to wait for"
            )
            page.url = "https://www.google.com/sorry/index"
            return {}

    async def attach(site: object, browser_page: object) -> WebPage:
        return cast("WebPage", Web())

    monkeypatch.setattr(type(SITE), "attach", attach)
    with pytest.raises(GeminiOpenError, match="browser verification"):
        await GeminiChat.new(cast("BrowserContext", Context()))
    assert page.closed
