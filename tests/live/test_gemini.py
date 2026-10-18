"""Opt-in Gemini checks. Deletion targets only chats created by its own test."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio
from cloakbrowser import launch_persistent_context_async

from generative_ai.gemini import GeminiChat

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from playwright.async_api import BrowserContext

    from generative_ai.site import BrowserContext as SiteContext

pytestmark = pytest.mark.live


@pytest_asyncio.fixture(scope="module")
async def gemini_context(pytestconfig: pytest.Config) -> AsyncIterator[BrowserContext]:
    # Separate from the Flow context fixture: Gemini checks must never depend
    # on the account having Flow enabled, or launch the same profile twice.
    profile = cast("Path", pytestconfig.getoption("gemini_profile"))
    if not profile.is_dir():
        pytest.fail(f"Gemini profile {profile} does not exist. Sign in first.")
    context = cast(
        "BrowserContext",
        await launch_persistent_context_async(
            profile, headless=pytestconfig.getoption("headless"), humanize=True
        ),
    )
    try:
        yield context
    finally:
        await context.close()


async def test_models_and_multi_turn_chat(gemini_context: BrowserContext) -> None:
    chat = await GeminiChat.new(cast("SiteContext", gemini_context))
    original = await chat.get_model()
    try:
        models = await chat.list_models()
        assert original in models
        # Choose an actually offered option, then read it back. No fixed model
        # names: labels and account entitlements change independently of code.
        await chat.set_model(models[0])
        assert await chat.get_model() == models[0]
        first = await chat.send("Reply with exactly GEMINI_LIVE_FIRST_OK.")
        assert first.text.strip() == "GEMINI_LIVE_FIRST_OK"
        second = await chat.send("Reply with exactly GEMINI_LIVE_SECOND_OK.")
        assert second.text.strip() == "GEMINI_LIVE_SECOND_OK"
        assert first.turn_id != second.turn_id
        assert second.chat_url.startswith("https://gemini.google.com/app/")
        assert (await chat.get_messages())[-1].text == second.text
        reopened = await GeminiChat.open(
            second.chat_url, cast("SiteContext", gemini_context)
        )
        try:
            assert (await reopened.get_messages())[-1].text == second.text
        finally:
            await reopened.close()
    finally:
        try:
            await chat.stop_response()
            await chat.set_model(original)
        finally:
            await chat.close()


async def test_thinking_levels_across_available_models(
    gemini_context: BrowserContext,
) -> None:
    chat = await GeminiChat.new(cast("SiteContext", gemini_context))
    original_model = await chat.get_model()
    try:
        for model in await chat.list_models():
            await chat.set_model(model)
            original_level = await chat.get_thinking_level()
            levels = await chat.list_thinking_levels()
            if original_level is None:
                assert levels == ()
                continue
            assert original_level in levels
            try:
                for level in levels:
                    await chat.set_thinking_level(level)
                    assert await chat.get_thinking_level() == level
                    assert await chat.get_model() == model
            finally:
                await chat.set_thinking_level(original_level)
    finally:
        try:
            await chat.set_model(original_model)
        finally:
            await chat.close()


async def test_native_temporary_chat_context(gemini_context: BrowserContext) -> None:
    existing_pages = set(gemini_context.pages)
    async with GeminiChat.temporary(cast("SiteContext", gemini_context)) as chat:
        assert chat.is_temporary
        assert (await chat._snapshot())["temporary"]
        response = await chat.send("Reply with exactly GEMINI_NATIVE_TEMP_OK.")
        assert response.text.strip() == "GEMINI_NATIVE_TEMP_OK"
        assert (await chat._snapshot())["temporary"]
        assert response.chat_url == "https://gemini.google.com/app"
    assert chat.is_closed
    assert set(gemini_context.pages) == existing_pages


async def test_delete_only_its_new_disposable_chat(
    gemini_context: BrowserContext,
) -> None:
    # Never open an existing chat for this test: its deletion target must be
    # the conversation created by this exact invocation.
    chat = await GeminiChat.new(cast("SiteContext", gemini_context))
    try:
        response = await chat.send(
            "This is a disposable browser automation deletion test. Reply with exactly GEMINI_DELETE_TEST_OK."
        )
        assert response.text.strip() == "GEMINI_DELETE_TEST_OK"
        assert response.chat_url.startswith("https://gemini.google.com/app/")
        await chat.delete(timeout=60)
        assert chat.is_closed
    finally:
        if not chat.is_closed:
            await chat.close()
