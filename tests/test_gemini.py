"""Observed Gemini DOM shapes exercised through real Playwright and manifests."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from generative_ai import GeminiChat
from generative_ai.gemini import (
    GeminiAuthenticationError,
    GeminiChatClosedError,
    GeminiChatDeletionError,
    GeminiGenerationError,
    GeminiModelError,
    GeminiOpenError,
    GeminiTemporaryChatError,
    GeminiThinkingLevelError,
    GeminiTimeoutError,
)
from generative_ai.gemini.constants import SITE
from generative_ai.site import get_site

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from playwright.async_api import Browser, BrowserContext, Page

    from generative_ai.site import BrowserContext as SiteContext


@pytest.fixture(scope="module")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install Playwright Chromium to run DOM fixture tests")
            raise
        try:
            yield browser
        finally:
            await browser.close()


@pytest.fixture
async def context(browser: Browser) -> AsyncIterator[BrowserContext]:
    context = await browser.new_context()
    try:
        yield context
    finally:
        await context.close()


_HTML = """
<chat-window style="display:block">
<main>
<button aria-label="Temporary chat" onclick="startTemporary()">Temporary</button>
<rich-textarea><div role="textbox" contenteditable="true"
 aria-label="Enter a prompt for Gemini"></div>
 <div contenteditable="true" class="ql-clipboard" tabindex="-1"></div></rich-textarea>
<div id="controls"></div>
<button aria-label="Open mode picker, currently Flash-Lite" onclick="openModels()">Flash-Lite</button>
<div id="history"></div>
</main>
</chat-window>
<script>
const fixture = FIXTURE;
const editor = document.querySelector('[role=textbox]');
const controls = document.getElementById('controls');
window.sends = 0;
window.selected = 'Flash-Lite';
window.modelClicks = 0;
window.thinkingClicks = 0;
window.thinking = {'Flash-Lite': 'Low', 'Pro': 'Medium'};
window.deleteClicks = 0;
function startTemporary() {
 if (!fixture.ignoreTemporary) setTimeout(() =>
  document.querySelector('chat-window').classList.add('is-temporary-chat'), 40);
}
function openChatActions() {
 const menu = document.createElement('div');
 menu.setAttribute('role', 'menu');
 menu.innerHTML = '<button role="menuitem" data-test-id="delete-button">Delete</button>';
 menu.querySelector('button').onclick = () => {
  menu.remove();
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-label', 'Delete chat');
  dialog.innerHTML = '<h1>Delete chat?</h1><button id="cancel-delete">Cancel</button><button id="confirm-delete">Delete</button>';
  dialog.querySelector('#cancel-delete').onclick = () => dialog.remove();
  dialog.querySelector('#confirm-delete').onclick = () => {
   window.deleteClicks++;
   dialog.remove();
   if (!fixture.neverDelete) setTimeout(async () => {
    if (window.recordDeletion) await window.recordDeletion(location.pathname);
    history.replaceState({}, '', '/app');
    document.getElementById('history').replaceChildren();
    document.querySelector('[aria-label="Open menu for conversation actions."]')?.remove();
   }, 150);
  };
  document.body.append(dialog);
  if (fixture.changeChatBeforeConfirmation) history.replaceState({}, '', '/app/abc123');
 };
 document.body.append(menu);
}
if (fixture.signedOut) document.body.insertAdjacentHTML('beforeend',
 '<a href="https://accounts.google.com/ServiceLogin">Sign in</a>');
function addTurn(id, prompt, text, complete) {
 const turn = document.createElement('div');
 turn.className = 'conversation-container';
 if (complete) turn.id = id;
 else setTimeout(() => turn.id = id, fixture.turnIdDelay || 10);
 turn.innerHTML = `<user-query><h5>You said ${prompt}</h5>
  <p class="query-text-line"></p></user-query>
  <model-response><message-content><div class="markdown" aria-busy="${!complete}"></div>
  </message-content><div class="response-footer ${complete ? 'complete' : ''}"></div></model-response>`;
 turn.querySelector('.query-text-line').textContent = prompt;
 turn.querySelector('.markdown').textContent = text;
 document.getElementById('history').append(turn);
 if (!fixture.noActions) setTimeout(() => {
  if (document.querySelector('[aria-label="Open menu for conversation actions."]')) return;
  const button = document.createElement('button');
  button.setAttribute('aria-label', 'Open menu for conversation actions.');
  button.textContent = 'Options';
  button.onclick = openChatActions;
  document.body.append(button);
 }, 80);
 return turn;
}
if (fixture.oldTurn) addTurn('a1', fixture.oldTurn, 'OLD RESPONSE', true);
if (location.pathname !== '/app' && !fixture.oldTurn)
 setTimeout(() => addTurn('a1', 'Historical prompt', 'OLD RESPONSE', true), 150);
editor.addEventListener('input', () => {
 setTimeout(() => {
  if (!editor.innerText.trim()) return;
  controls.innerHTML = '<button aria-label="Send message" onclick="send()">Send</button>';
 }, 30);
});
function send() {
 window.sends++;
 window.submittedSettings = {model: window.selected, thinking: window.thinking[window.selected]};
 const prompt = editor.innerText;
 editor.innerHTML = '';
 controls.innerHTML = '<button aria-label="Stop response" onclick="stop()">Stop</button>';
 if (!document.querySelector('chat-window.is-temporary-chat'))
  history.replaceState({}, '', '/app/abcdef');
 setTimeout(() => {
  window.turn = addTurn('b' + window.sends, prompt, 'partial', false);
  if (fixture.error) {
   document.body.insertAdjacentHTML('beforeend', '<div role="alert">Service unavailable</div>');
   return;
  }
  if (!fixture.never) setTimeout(finish, fixture.delay || 100);
 }, 30);
}
function finish() {
 if (!window.turn) return;
 turn.querySelector('.markdown').textContent = 'FINISHED ' + window.sends;
 turn.querySelector('.markdown').setAttribute('aria-busy', 'false');
 turn.querySelector('.response-footer').classList.add('complete');
 controls.innerHTML = '';
}
function stop() { controls.innerHTML = ''; }
function openModels() {
 setTimeout(() => {
 const menu = document.createElement('div');
 menu.setAttribute('data-test-id', 'gem-mode-menu');
 menu.setAttribute('data-visible', 'true');
 for (const [id, name, disabled] of [
  ['flash', 'Flash-Lite', false], ['pro', 'Pro', false],
  ['disabled', 'Disabled', true], ['upsell_MODE_CATEGORY_ULTRA', 'Upgrade', false]]) {
  const item = document.createElement('div');
  item.setAttribute('role', 'menuitem');
  item.setAttribute('data-mode-id', id);
  item.setAttribute('aria-disabled', String(disabled));
  if (name === window.selected) item.className = 'selected';
  item.innerHTML = '<span class="label"></span><span>description</span>';
  item.querySelector('.label').textContent = name;
  item.onclick = () => {
   window.modelClicks++;
   if (!fixture.ignoreModel) window.selected = name;
   if (fixture.resetThinkingOnModel) window.thinking[name] = 'Low';
   menu.remove();
  };
  menu.append(item);
 }
 if (!fixture.noThinking) {
  const trigger = document.createElement('div');
  trigger.setAttribute('role', 'menuitem');
  trigger.setAttribute('value', 'thinking_level');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.innerHTML = '<span class="label">Thinking level</span>';
  const container = document.createElement('div');
  container.className = 'submenu-container';
  const submenu = document.createElement('div');
  submenu.setAttribute('role', 'menu');
  submenu.setAttribute('data-visible', 'false');
  submenu.hidden = true;
  if (fixture.offscreenThinking) submenu.style = 'position:fixed;left:2000px;top:100px';
  const labels = window.selected === 'Pro'
   ? ['Low', 'Medium', 'High', 'Deep Think', 'Disabled']
   : ['Low', 'Medium', 'High', 'Disabled'];
  for (const label of labels) {
   const item = document.createElement('div');
   item.setAttribute('role', 'menuitem');
   item.setAttribute('aria-disabled', String(label === 'Disabled'));
   item.tabIndex = -1;
   item.className = label === window.thinking[window.selected] ? 'selected' : '';
   item.innerHTML = '<span class="label"></span><span>description</span>';
   item.querySelector('.label').textContent = label;
   item.onclick = () => {
    window.thinkingClicks++;
    if (!fixture.ignoreThinking) window.thinking[window.selected] = label;
    menu.remove();
   };
   item.onkeydown = event => { if (event.key === 'Enter') item.click(); };
   submenu.append(item);
  }
  trigger.onclick = () => setTimeout(() => {
   trigger.setAttribute('aria-expanded', 'true');
   submenu.hidden = false;
   submenu.setAttribute('data-visible', 'true');
  }, 30);
  container.append(submenu);
  menu.append(trigger, container);
 }
 document.body.append(menu);
 }, 30);
}
document.addEventListener('keydown', e => {
 if (e.key === 'Escape') document.querySelector('[data-test-id="gem-mode-menu"]')?.remove();
});
</script>
"""


async def _chat(context: BrowserContext, **scenario: object) -> tuple[GeminiChat, Page]:
    html = _HTML.replace("FIXTURE", json.dumps(scenario))
    await context.route(
        "https://gemini.google.com/**",
        lambda route: route.fulfill(content_type="text/html", body=html),
    )
    chat = await GeminiChat.new(cast("SiteContext", context))
    return chat, context.pages[-1]


def test_gemini_is_registered_and_flow_still_registered() -> None:
    assert get_site("gemini") is SITE
    assert get_site("googleflow").base_url == "https://flow.google.com/"


async def test_streamed_response_and_follow_up_are_attributed_to_new_turn(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, oldTurn="same prompt")
    first = await chat.send("same prompt", timeout=5)
    second = await chat.send("second\nline", timeout=5)
    assert first.text == "FINISHED 1"
    assert first.turn_id == "b1"
    assert second.text == "FINISHED 2"
    assert second.turn_id == "b2"
    assert second.chat_url == "https://gemini.google.com/app/abcdef"
    assert await page.evaluate("window.sends") == 2
    messages = await chat.get_messages()
    assert [message.role for message in messages] == ["user", "assistant"] * 3
    assert messages[-2].text == "second\nline"


async def test_timeout_can_resume_without_duplicate_submission(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, never=True)
    with pytest.raises(GeminiTimeoutError, match="remains pending"):
        await chat.send("hello", timeout=0.3)
    with pytest.raises(GeminiGenerationError, match="pending"):
        await chat.send("another", timeout=1)
    await page.evaluate("finish()")
    assert (await chat.wait_for_response(timeout=2)).text == "FINISHED 1"
    assert await page.evaluate("window.sends") == 1


async def test_turn_id_is_assigned_after_the_stream_placeholder(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, turnIdDelay=650)
    result = await chat.send("late turn id", timeout=3)
    assert result.turn_id == "b1"
    assert result.text == "FINISHED 1"
    assert await page.evaluate("window.sends") == 1


async def test_cancelled_send_preserves_pending_turn(context: BrowserContext) -> None:
    chat, page = await _chat(context, never=True)
    task = asyncio.ensure_future(chat.send("cancel", timeout=10))
    await page.locator('button[aria-label="Stop response"]').wait_for()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await page.locator("#b1").wait_for()
    await page.evaluate("finish()")
    assert (await chat.wait_for_response(timeout=2)).turn_id == "b1"
    assert await page.evaluate("window.sends") == 1


async def test_partial_response_and_disappearing_stop_are_not_completion(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, never=True)
    with pytest.raises(GeminiTimeoutError):
        await chat.send("hello", timeout=0.3)
    await page.evaluate(
        "stop(); turn.querySelector('.markdown').setAttribute('aria-busy', 'false')"
    )
    with pytest.raises(GeminiTimeoutError):
        await chat.wait_for_response(timeout=0.1)
    await page.evaluate("finish()")
    assert (await chat.wait_for_response(timeout=2)).text == "FINISHED 1"


async def test_errors_reported_without_resending_and_stop_releases_pending(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, error=True)
    with pytest.raises(GeminiGenerationError, match="Service unavailable"):
        await chat.send("hello", timeout=3)
    assert await page.evaluate("window.sends") == 1
    await chat.stop_response()
    with pytest.raises(GeminiGenerationError, match="no pending"):
        await chat.wait_for_response()


async def test_signed_out_composer_is_rejected_and_tab_cleaned_up(
    context: BrowserContext,
) -> None:
    with pytest.raises(GeminiAuthenticationError):
        await _chat(context, signedOut=True)
    assert context.pages == []


async def test_failed_navigation_preserves_error_and_cleans_up(
    context: BrowserContext,
) -> None:
    await context.route("https://gemini.google.com/**", lambda route: route.abort())
    with pytest.raises(GeminiOpenError, match="Could not open"):
        await GeminiChat.new(cast("SiteContext", context))
    assert context.pages == []


async def test_models_are_lazy_loaded_verified_and_exclude_upgrades(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context)
    assert await chat.list_models() == ("Flash-Lite", "Pro")
    assert await chat.get_model() == "Flash-Lite"
    await chat.set_model("Pro")
    assert await chat.get_model() == "Pro"
    assert await page.locator('[data-test-id="gem-mode-menu"]').count() == 0
    await chat.set_model("Pro")
    assert await page.evaluate("window.modelClicks") == 1
    with pytest.raises(GeminiModelError, match="Available models"):
        await chat.set_model("Upgrade")
    assert await page.evaluate("window.modelClicks") == 1


async def test_model_readback_detects_ignored_click(context: BrowserContext) -> None:
    chat, _ = await _chat(context, ignoreModel=True)
    with pytest.raises(
        GeminiModelError, match="requested 'Pro', observed 'Flash-Lite'"
    ):
        await chat.set_model("Pro")


async def test_thinking_levels_are_discovered_scoped_and_verified(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context)
    assert await chat.list_thinking_levels() == ("Low", "Medium", "High")
    assert await chat.get_thinking_level() == "Low"
    await chat.set_thinking_level("High")
    assert await chat.get_thinking_level() == "High"
    assert await chat.get_model() == "Flash-Lite"
    await chat.set_thinking_level("High")
    assert await page.evaluate("window.thinkingClicks") == 1
    with pytest.raises(GeminiThinkingLevelError, match="Available levels"):
        await chat.set_thinking_level("Disabled")
    assert await page.evaluate("window.thinkingClicks") == 1
    assert await page.locator('[data-test-id="gem-mode-menu"]').count() == 0
    await chat.set_model("Pro")
    assert await chat.list_thinking_levels() == ("Low", "Medium", "High", "Deep Think")
    assert await chat.get_thinking_level() == "Medium"


async def test_thinking_readback_detects_ignored_click(context: BrowserContext) -> None:
    chat, _ = await _chat(context, ignoreThinking=True)
    with pytest.raises(
        GeminiThinkingLevelError, match="requested 'High', observed 'Low'"
    ):
        await chat.set_thinking_level("High")


async def test_offscreen_thinking_submenu_uses_native_keyboard_selection(
    context: BrowserContext,
) -> None:
    chat, _ = await _chat(context, offscreenThinking=True)
    await chat.set_thinking_level("High")
    assert await chat.get_thinking_level() == "High"


async def test_unavailable_thinking_level_is_explicit(context: BrowserContext) -> None:
    chat, page = await _chat(context, noThinking=True)
    assert await chat.list_thinking_levels() == ()
    assert await chat.get_thinking_level() is None
    with pytest.raises(GeminiThinkingLevelError, match=r"Available levels: \[\]"):
        await chat.set_thinking_level("High")
    assert await page.evaluate("window.thinkingClicks") == 0


async def test_send_applies_thinking_after_model_before_submission(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, resetThinkingOnModel=True)
    response = await chat.send(
        "settings order", model="Pro", thinking_level="High", timeout=5
    )
    assert response.text == "FINISHED 1"
    assert await page.evaluate("window.submittedSettings") == {
        "model": "Pro",
        "thinking": "High",
    }
    assert await chat.get_thinking_level() == "High"


async def test_invalid_thinking_level_does_not_submit(context: BrowserContext) -> None:
    chat, page = await _chat(context)
    with pytest.raises(GeminiThinkingLevelError):
        await chat.send("not submitted", thinking_level="Unknown", timeout=5)
    assert await page.evaluate("window.sends") == 0
    with pytest.raises(ValueError, match="level"):
        await chat.set_thinking_level(" ")


async def test_pending_turn_rejects_thinking_changes(context: BrowserContext) -> None:
    chat, page = await _chat(context, never=True)
    with pytest.raises(GeminiTimeoutError):
        await chat.send("pending settings", timeout=0.3)
    with pytest.raises(GeminiGenerationError, match="pending"):
        await chat.set_thinking_level("High")
    assert await page.evaluate("window.thinkingClicks") == 0


async def test_open_waits_for_lazy_history(context: BrowserContext) -> None:
    chat, _ = await _chat(context)
    await chat.close()
    reopened = await GeminiChat.open("abcdef", cast("SiteContext", context))
    assert (await reopened.get_messages())[-1].text == "OLD RESPONSE"
    await reopened.close()
    with pytest.raises(GeminiChatClosedError):
        await reopened.get_messages()


async def test_native_temporary_context_verifies_mode_sends_and_closes(
    context: BrowserContext,
) -> None:
    initial, _ = await _chat(context)
    await initial.close()
    async with GeminiChat.temporary(cast("SiteContext", context)) as chat:
        assert chat.is_temporary
        assert (await chat.send("temporary", timeout=5)).text == "FINISHED 1"
        assert chat.chat_url == "https://gemini.google.com/app"
        assert (await chat._snapshot())["temporary"]
    assert chat.is_closed
    assert context.pages == []


async def test_temporary_mode_cannot_silently_fall_back_to_saved_chat(
    context: BrowserContext,
) -> None:
    initial, _ = await _chat(context)
    await initial.close()
    async with GeminiChat.temporary(cast("SiteContext", context)) as chat:
        page = context.pages[-1]
        await page.evaluate(
            "document.querySelector('chat-window').classList.remove('is-temporary-chat')"
        )
        with pytest.raises(GeminiTemporaryChatError, match="left Temporary"):
            await chat.send("must not send")
        assert await page.evaluate("window.sends") == 0


async def test_delete_waits_for_remote_completion_and_keeps_other_tabs(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context)
    await chat.send("disposable", timeout=5)
    other = await context.new_page()
    deleted: list[str] = []
    await page.expose_function("recordDeletion", lambda path: deleted.append(path))
    await chat.delete(timeout=3)
    assert deleted == ["/app/abcdef"]
    assert chat.is_closed
    assert not other.is_closed()
    assert context.pages == [other]


async def test_delete_unconfirmed_timeout_can_be_retried(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, noActions=True)
    await chat.send("disposable", timeout=5)
    with pytest.raises(GeminiChatDeletionError):
        await chat.delete(timeout=0.1)
    assert not chat.is_closed
    assert await page.evaluate("window.deleteClicks") == 0


async def test_delete_refuses_a_conversation_changed_before_confirmation(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, changeChatBeforeConfirmation=True)
    await chat.send("disposable", timeout=5)
    with pytest.raises(GeminiChatDeletionError) as caught:
        await chat.delete(timeout=3)
    assert "conversation changed" in str(caught.value.__cause__)
    assert await page.evaluate("window.deleteClicks") == 0
    assert await page.locator('[role="dialog"]').count() == 0
    assert not chat.is_closed


async def test_delete_ambiguous_confirmation_invalidates_facade(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, neverDelete=True)
    await chat.send("disposable", timeout=5)
    with pytest.raises(GeminiChatDeletionError) as caught:
        await chat.delete(timeout=0.4)
    assert any("state is unknown" in note for note in caught.value.__notes__)
    assert chat.is_closed
    assert page.is_closed()


async def test_cancelled_delete_preserves_uncertainty_and_closes_tab(
    context: BrowserContext,
) -> None:
    chat, page = await _chat(context, neverDelete=True)
    await chat.send("disposable", timeout=5)
    deletion = asyncio.ensure_future(chat.delete(timeout=10))
    await page.wait_for_function("window.deleteClicks === 1")
    deletion.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await deletion
    assert any("state is unknown" in note for note in caught.value.__notes__)
    assert chat.is_closed


async def test_unsent_and_temporary_chats_cannot_be_deleted(
    context: BrowserContext,
) -> None:
    chat, _ = await _chat(context)
    with pytest.raises(GeminiChatDeletionError, match="no saved conversation"):
        await chat.delete()
    await chat.close()
    async with GeminiChat.temporary(cast("SiteContext", context)) as temporary:
        await temporary.send("temporary", timeout=5)
        with pytest.raises(GeminiChatDeletionError, match="no saved conversation"):
            await temporary.delete()


async def test_pending_turn_cannot_be_deleted(context: BrowserContext) -> None:
    chat, page = await _chat(context, never=True)
    with pytest.raises(GeminiTimeoutError):
        await chat.send("still pending", timeout=0.3)
    with pytest.raises(GeminiGenerationError, match="pending"):
        await chat.delete()
    assert await page.evaluate("window.deleteClicks") == 0


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/app/abcdef",
        "https://gemini.google.com.evil/app/abcdef",
        "https://gemini.google.com/app/abc?token=x",
        "",
        "../abc",
    ],
)
async def test_open_rejects_invalid_urls_before_creating_tabs(
    context: BrowserContext, value: str
) -> None:
    with pytest.raises(ValueError):
        await GeminiChat.open(value, cast("SiteContext", context))
    assert context.pages == []


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
async def test_invalid_timeout_does_not_submit(
    context: BrowserContext, timeout: float
) -> None:
    chat, page = await _chat(context)
    with pytest.raises(ValueError, match="timeout"):
        await chat.send("hello", timeout=timeout)
    assert await page.evaluate("window.sends") == 0
