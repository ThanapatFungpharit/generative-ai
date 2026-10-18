from __future__ import annotations

import asyncio
import math
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, TypedDict, override
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from web_page.exceptions import ActionFailed
from web_page.utils import is_str_list

from generative_ai.gemini.constants import (
    BASE_URL,
    POLL_INTERVAL_SECONDS,
    RESPONSE_TIMEOUT_SECONDS,
    SELECTORS,
    SITE,
)
from generative_ai.gemini.exceptions import (
    GeminiAuthenticationError,
    GeminiChatClosedError,
    GeminiChatCloseError,
    GeminiChatDeletionError,
    GeminiGenerationError,
    GeminiModelError,
    GeminiOpenError,
    GeminiTemporaryChatError,
    GeminiThinkingLevelError,
    GeminiTimeoutError,
)
from generative_ai.gemini.models import GeminiMessage, GeminiResponse
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
    from collections.abc import AsyncGenerator

    from web_page import WebPage

    from generative_ai.site import BrowserContext, BrowserPage


class _Turn(TypedDict):
    id: str
    prompt: str
    text: str
    complete: bool
    busy: bool


class _Snapshot(TypedDict):
    turns: list[_Turn]
    errors: list[str]
    stopping: bool
    signed_out: bool
    temporary: bool


@dataclass(slots=True)
class _PendingTurn:
    prompt: str
    baseline: frozenset[str]
    errors: frozenset[str]
    turn_id: str | None = None


# Only DOM-backed state: no private Angular services, backend RPCs or account
# tokens. Each turn is read atomically so re-rendering cannot mix two responses.
_SNAPSHOT_JS = """s => {
    const visible = el => !!el.getClientRects().length;
    return {
        turns: Array.from(document.querySelectorAll(s.turns)).map(el => ({
            id: el.id,
            prompt: Array.from(el.querySelectorAll(s.query_lines))
                .map(line => line.textContent.trim()).join('\\n'),
            text: Array.from(el.querySelectorAll(s.response_text))
                .map(text => text.innerText || text.textContent).join('\\n').trim(),
            complete: !!el.querySelector(s.response_complete),
            busy: !!el.querySelector(s.busy)
        })),
        errors: Array.from(document.querySelectorAll(s.errors))
            .filter(visible).map(el => el.innerText.trim()).filter(Boolean),
        stopping: Array.from(document.querySelectorAll(s.stop_button)).some(visible),
        signed_out: Array.from(document.querySelectorAll(s.sign_in)).some(visible),
        temporary: Array.from(document.querySelectorAll(s.temporary_window)).some(visible)
    };
}"""


class GeminiChat(SitePage):
    """One Google Gemini chat tab in a caller-owned Chromium context.

    Construct with ``new`` or ``open``. Public operations are serialized like
    other SitePage facades. A timed-out/cancelled send remains pending; use
    ``wait_for_response`` or ``stop_response`` before sending another prompt.
    """

    __slots__ = ("_chat_url", "_is_temporary", "_pending")

    def __init__(self, page: BrowserPage, web_page: WebPage) -> None:
        super().__init__(page, web_page)
        self._chat_url = page.url
        self._is_temporary = False
        self._pending: _PendingTurn | None = None

    @property
    def chat_url(self) -> str:
        """The last observed chat URL; new chats gain an ID after submission."""
        return self._chat_url

    @property
    def is_temporary(self) -> bool:
        """Whether this facade was created in verified native Temporary chat mode."""
        return self._is_temporary

    @classmethod
    async def new(cls, context: BrowserContext) -> Self:
        """Open a fresh composer. No conversation is saved until a prompt is sent."""
        return await cls._open_url(BASE_URL, context)

    @classmethod
    @asynccontextmanager
    async def temporary(cls, context: BrowserContext) -> AsyncGenerator[Self]:
        """Yield a native Temporary chat and close its owned tab on every exit.

        The caller's browser context is retained. Body exceptions and
        cancellation propagate, with cleanup failures attached as notes.
        """
        chat = await cls.new(context)
        operation_error: BaseException | None = None
        try:
            await chat._activate_temporary()
            yield chat
        except BaseException as error:
            operation_error = error
            raise
        finally:
            # Bound page cleanup directly: an abandoned background send may
            # still hold the operation lock. This context owns its entire tab.
            page = chat._page
            if page is not None:
                cleanup_error = await close_page_and_report_failure(page)
                if cleanup_error is None:
                    chat._page = None
                elif operation_error is not None:
                    note_cleanup_failure(operation_error, cleanup_error)
                else:
                    raise GeminiChatCloseError(
                        "Could not close the Temporary chat tab."
                    ) from cleanup_error

    async def _activate_temporary(self) -> None:
        self._ensure_idle(await self._snapshot())
        try:
            await self._web_page.run("start_temporary_chat")
            if not (await self._snapshot())["temporary"]:
                raise UnexpectedPageStateError(
                    "Gemini did not retain Temporary chat mode."
                )
        except (PlaywrightError, UnexpectedPageStateError, ActionFailed) as error:
            raise GeminiTemporaryChatError(
                "Could not enable Gemini's Temporary chat mode."
            ) from error
        self._is_temporary = True

    @classmethod
    async def open(cls, chat: str, context: BrowserContext) -> Self:
        """Open an existing chat by its hexadecimal ID or canonical Gemini URL."""
        return await cls._open_url(_chat_url(chat), context)

    @classmethod
    async def _open_url(cls, url: str, context: BrowserContext) -> Self:
        page: BrowserPage | None = None
        instance: Self | None = None
        try:
            page = await context.new_page()
            instance = cls(page, await SITE.attach(page))
            await instance._web_page.run("open_chat", {"url": url})
            instance._check_url()
            await instance._web_page.run("wait_for_chat")
            instance._require_authenticated(await instance._snapshot())
            # The composer mounts before the conversation is fetched. Do not
            # mistake lazy-loaded old turns for the next submitted response.
            if url != BASE_URL:
                await page.locator(SELECTORS["turns"]).last.wait_for(state="attached")
                if page.url.rstrip("/") != url:
                    raise GeminiOpenError(f"Gemini did not open requested chat {url}.")
            instance._chat_url = page.url
            return instance
        except BaseException as error:
            failure = error
            if isinstance(error, PlaywrightError):
                failure = GeminiOpenError(f"Could not open Gemini chat {url}.")
                if instance is not None:
                    try:
                        instance._require_authenticated(await instance._snapshot())
                    except GeminiAuthenticationError as auth_error:
                        failure = auth_error
                    except (
                        PlaywrightError,
                        UnexpectedPageStateError,
                        GeminiOpenError,
                        GeminiChatClosedError,
                    ):
                        pass
            if page is not None:
                note_cleanup_failure(failure, await close_page_and_report_failure(page))
            if failure is error:
                raise
            raise failure from error

    @page_required
    async def close(self) -> None:
        """Close only this chat tab, retaining the handle if closing fails."""
        page = self._require_page()
        cleanup_error = await close_page_and_report_failure(page)
        if cleanup_error is not None:
            raise GeminiChatCloseError(
                "Could not close the Gemini chat tab."
            ) from cleanup_error
        self._page = None

    @page_required
    async def delete(self, *, timeout: float = 30.0) -> None:
        """Permanently delete the current saved chat, verify its removal, and close.

        Unsent and native Temporary chats have no saved conversation to delete.
        Pending responses must be resolved or stopped first. An ambiguous
        confirmation is never retried and invalidates this facade.
        """
        _validate_timeout(timeout)
        page = self._require_page()
        snapshot = await self._snapshot()
        self._ensure_idle(snapshot)
        path = urlsplit(page.url).path.rstrip("/")
        if snapshot["temporary"] or not re.fullmatch(r"/app/[a-fA-F0-9]+", path):
            raise GeminiChatDeletionError(
                "This chat has no saved conversation to delete."
            )
        chat_url = page.url
        confirmation_attempted = False
        try:
            async with asyncio.timeout(timeout):
                await self._web_page.run("open_delete_chat_dialog")
                if page.url != chat_url:
                    raise UnexpectedPageStateError(
                        "The current conversation changed before deletion confirmation."
                    )
                confirmation_attempted = True
                await self._web_page.run("confirm_delete_chat", {"base_url": BASE_URL})
                observed = await self._snapshot()
                if page.url.rstrip("/") != BASE_URL or observed["turns"]:
                    raise UnexpectedPageStateError(
                        "Gemini did not return to an empty composer after deletion."
                    )
        except BaseException as error:
            failure: BaseException = error
            if isinstance(
                error,
                (PlaywrightError, TimeoutError, UnexpectedPageStateError, ActionFailed),
            ):
                failure = GeminiChatDeletionError(
                    f"Could not verify deletion of Gemini chat {chat_url}."
                )
            if confirmation_attempted:
                failure.add_note(
                    "Deletion confirmation was attempted; the remote chat state is unknown. No retry was made and the facade has been closed."
                )
                self._page = None
                note_cleanup_failure(failure, await close_page_and_report_failure(page))
            else:
                try:
                    async with asyncio.timeout(CLEANUP_TIMEOUT_SECONDS):
                        await self._web_page.run("cancel_delete_chat")
                except Exception as cleanup_error:  # noqa: BLE001
                    failure.add_note(
                        f"Delete dialog cleanup also failed: {cleanup_error}"
                    )
            if failure is error:
                raise
            raise failure from error
        self._page = None
        cleanup_error = await close_page_and_report_failure(page)
        if cleanup_error is not None:
            failure = GeminiChatCloseError(
                "Gemini chat deletion was verified, but its tab could not be closed."
            )
            raise failure from cleanup_error

    @override
    def _closed_error(self) -> Exception:
        return GeminiChatClosedError("The Gemini chat tab is closed.")

    @page_required
    async def list_models(self) -> tuple[str, ...]:
        """Read selectable model labels from the live menu, excluding upgrades."""
        available, _ = await self._read_models()
        return available

    @page_required
    async def get_model(self) -> str:
        """Read the selected model's full menu label."""
        _, selected = await self._read_models()
        return selected

    @page_required
    async def set_model(self, model: str) -> None:
        """Select an account-available model and verify it by reopening the menu."""
        await self._set_model(model)

    @page_required
    async def list_thinking_levels(self) -> tuple[str, ...]:
        """Read enabled levels offered by this model/account; empty if unavailable."""
        available, _ = await self._read_thinking_levels()
        return available

    @page_required
    async def get_thinking_level(self) -> str | None:
        """Read the selected level, or None if no thinking submenu is offered."""
        _, selected = await self._read_thinking_levels()
        return selected

    @page_required
    async def set_thinking_level(self, level: str) -> None:
        """Select a live level label and verify it by reopening its submenu."""
        await self._set_thinking_level(level)

    async def _read_thinking_levels(self) -> tuple[tuple[str, ...], str | None]:
        self._ensure_idle(await self._snapshot())
        try:
            async with self._model_menu():
                results = await self._web_page.run("read_thinking_levels")
                available = tuple(
                    label.strip() for label in texts_result(results, "available")
                )
                selected = tuple(
                    label.strip() for label in texts_result(results, "selected")
                )
                if not available and not selected:
                    return (), None
                if len(selected) != 1:
                    raise UnexpectedPageStateError(
                        "Gemini did not identify one selected thinking level."
                    )
                return available, selected[0]
        except (PlaywrightError, UnexpectedPageStateError) as error:
            raise GeminiThinkingLevelError(
                "Could not read Gemini's thinking-level submenu."
            ) from error

    async def _set_thinking_level(self, level: str) -> None:
        if not level.strip():
            raise ValueError("level must not be empty or whitespace")
        available, selected = await self._read_thinking_levels()
        if level == selected:
            return
        if level not in available:
            raise GeminiThinkingLevelError(
                f"Gemini does not offer thinking level {level!r} for the current model. "
                f"Available levels: {list(available)}."
            )
        try:
            async with self._model_menu():
                await self._web_page.run("select_thinking_level", {"level": level})
        except (PlaywrightError, ActionFailed) as error:
            raise GeminiThinkingLevelError(
                f"Could not select Gemini thinking level {level!r}."
            ) from error
        _, observed = await self._read_thinking_levels()
        if observed != level:
            raise GeminiThinkingLevelError(
                f"Gemini thinking-level selection did not apply: requested {level!r}, observed {observed!r}."
            )

    async def _read_models(self) -> tuple[tuple[str, ...], str]:
        self._ensure_idle(await self._snapshot())
        try:
            async with self._model_menu():
                results = await self._web_page.run("read_models")
                return (
                    tuple(
                        label.strip() for label in texts_result(results, "available")
                    ),
                    text_result(results, "selected").strip(),
                )
        except (PlaywrightError, UnexpectedPageStateError) as error:
            raise GeminiModelError("Could not read Gemini's model picker.") from error

    @asynccontextmanager
    async def _model_menu(self) -> AsyncGenerator[None]:
        """Keep menu cleanup from replacing an operation error or cancellation."""
        try:
            yield
        except BaseException as error:
            try:
                await self._dismiss_model_menu()
            except Exception as cleanup_error:  # noqa: BLE001
                error.add_note(f"Gemini menu cleanup also failed: {cleanup_error}")
            raise
        else:
            await self._dismiss_model_menu()

    async def _dismiss_model_menu(self) -> None:
        if await self._require_page().locator(SELECTORS["model_menu"]).is_visible():
            await self._web_page.run("close_model_menu")

    async def _set_model(self, model: str) -> None:
        if not model.strip():
            raise ValueError("model must not be empty or whitespace")
        available, selected = await self._read_models()
        if model == selected:
            return
        if model not in available:
            raise GeminiModelError(
                f"Gemini does not offer model {model!r}. Available models: {list(available)}."
            )
        try:
            async with self._model_menu():
                await self._web_page.run("select_model", {"model": model})
        except (PlaywrightError, ActionFailed) as error:
            raise GeminiModelError(
                f"Could not select Gemini model {model!r}."
            ) from error
        _, observed = await self._read_models()
        if observed != model:
            raise GeminiModelError(
                f"Gemini model selection did not apply: requested {model!r}, observed {observed!r}."
            )

    @page_required
    async def get_messages(self) -> tuple[GeminiMessage, ...]:
        """Read rendered turns; this does not scroll in older, virtualized history."""
        snapshot = await self._snapshot()
        self._require_authenticated(snapshot)
        messages: list[GeminiMessage] = []
        for turn in snapshot["turns"]:
            messages.append(GeminiMessage("user", turn["prompt"], turn["id"]))
            if turn["text"]:
                messages.append(GeminiMessage("assistant", turn["text"], turn["id"]))
        return tuple(messages)

    @page_required
    async def send(
        self,
        prompt: str,
        *,
        model: str | None = None,
        thinking_level: str | None = None,
        timeout: float = RESPONSE_TIMEOUT_SECONDS,
    ) -> GeminiResponse:
        """Submit once and collect the matching, fully completed text response.

        The model is selected before its thinking level, then read back. The
        live level labels are model/account dependent. ``timeout`` covers
        configuration, submission and completion. A timeout
        or cancellation after clicking Send keeps the pending turn, including
        an ambiguous click; another send is refused until it is resolved.
        """
        _validate_timeout(timeout)
        if not prompt.strip():
            raise ValueError("prompt must not be empty or whitespace")
        try:
            async with asyncio.timeout(timeout):
                self._ensure_idle(await self._snapshot())
                if model is not None:
                    await self._set_model(model)
                if thinking_level is not None:
                    await self._set_thinking_level(thinking_level)
                results = await self._web_page.run("fill_prompt", {"prompt": prompt})
                observed = text_result(results, "text")
                if _normalize(observed) != _normalize(prompt):
                    raise GeminiGenerationError(
                        "Gemini's composer did not retain the requested prompt."
                    )
                snapshot = await self._snapshot()
                self._ensure_idle(snapshot)
                self._pending = _PendingTurn(
                    prompt,
                    frozenset(turn["id"] for turn in snapshot["turns"]),
                    frozenset(snapshot["errors"]),
                )
                await self._web_page.run("send_prompt")
                return await self._collect_response()
        except TimeoutError as error:
            raise self._timeout_error() from error
        except PlaywrightError as error:
            failure = GeminiGenerationError(
                "Gemini submission failed; no automatic resend was attempted."
            )
            if self._pending is not None:
                failure.add_note(
                    "Submission may have been accepted. Use wait_for_response to check the same turn."
                )
            raise failure from error

    @page_required
    async def wait_for_response(
        self, *, timeout: float = RESPONSE_TIMEOUT_SECONDS
    ) -> GeminiResponse:
        """Resume waiting for this facade's pending turn without resubmitting it."""
        _validate_timeout(timeout)
        if self._pending is None:
            raise GeminiGenerationError("There is no pending Gemini response.")
        try:
            async with asyncio.timeout(timeout):
                return await self._collect_response()
        except TimeoutError as error:
            raise self._timeout_error() from error

    def _timeout_error(self) -> GeminiTimeoutError:
        if self._pending is None:
            return GeminiTimeoutError("Gemini timed out before a prompt was submitted.")
        return GeminiTimeoutError(
            "Gemini response timed out. The turn remains pending; use wait_for_response "
            "to resume waiting, or stop_response to stop and abandon it."
        )

    async def _collect_response(self) -> GeminiResponse:
        pending = self._pending
        if pending is None:
            raise GeminiGenerationError("There is no pending Gemini response.")
        failed_snapshots = 0
        while True:
            try:
                snapshot = await self._snapshot()
            except PlaywrightError as error:
                self._require_page()
                failed_snapshots += 1
                if failed_snapshots >= 5:
                    raise GeminiGenerationError(
                        "Could not read Gemini's response state; the turn remains pending."
                    ) from error
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            failed_snapshots = 0
            self._require_authenticated(snapshot)
            errors = tuple(
                message
                for message in snapshot["errors"]
                if message not in pending.errors
            )
            if errors:
                # An error toast can precede Gemini accepting the prompt; keep
                # the pending turn until the caller explicitly resolves it.
                raise GeminiGenerationError(f"Gemini reported: {'; '.join(errors)}")
            for turn in reversed(snapshot["turns"]):
                if pending.turn_id is None:
                    if turn["id"] in pending.baseline or _normalize(
                        turn["prompt"]
                    ) != _normalize(pending.prompt):
                        continue
                    pending.turn_id = turn["id"]
                if turn["id"] != pending.turn_id:
                    continue
                if turn["complete"] and not turn["busy"] and not snapshot["stopping"]:
                    self._pending = None
                    if not turn["text"]:
                        raise GeminiGenerationError(
                            "Gemini completed the turn without a text response."
                        )
                    return GeminiResponse(turn["text"], turn["id"], self.chat_url)
                break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    @page_required
    async def stop_response(self) -> None:
        """Stop active generation, verify Stop disappears, then abandon pending state.

        Use after a timed-out/cancelled send. Public operations share a lock,
        so an in-progress send must first return or be cancelled.
        """
        self._require_authenticated(await self._snapshot())
        try:
            await self._web_page.run("stop_response")
            if (await self._snapshot())["stopping"]:
                raise GeminiGenerationError("Gemini still shows Stop response.")
        except PlaywrightError as error:
            raise GeminiGenerationError("Could not stop Gemini's response.") from error
        self._pending = None

    def _ensure_idle(self, snapshot: _Snapshot) -> None:
        self._require_authenticated(snapshot)
        if self._pending is not None or snapshot["stopping"]:
            raise GeminiGenerationError(
                "A Gemini response is pending. Use wait_for_response or stop_response first."
            )

    def _check_url(self) -> None:
        url = urlsplit(self._require_page().url)
        if url.hostname in {"google.com", "www.google.com"} and url.path.startswith(
            "/sorry/"
        ):
            raise GeminiOpenError(
                "Google requires browser verification before Gemini can load. Complete the unusual-traffic check in this profile first."
            )
        if url.hostname == "accounts.google.com":
            raise GeminiAuthenticationError(
                "Sign in to Gemini in this browser profile first."
            )
        if (
            url.scheme != "https"
            or url.netloc != "gemini.google.com"
            or not re.fullmatch(r"/app(?:/[a-fA-F0-9]+)?/?", url.path)
        ):
            raise GeminiOpenError(
                f"Gemini reached an unexpected page: {url.scheme}://{url.netloc}{url.path}."
            )

    @staticmethod
    def _require_authenticated(snapshot: _Snapshot) -> None:
        if snapshot["signed_out"]:
            raise GeminiAuthenticationError(
                "Sign in to Gemini in this browser profile first."
            )

    async def _snapshot(self) -> _Snapshot:
        self._check_url()
        page = self._require_page()
        raw = await page.evaluate(_SNAPSHOT_JS, dict(SELECTORS))
        if not is_str_keyed_mapping(raw):
            raise UnexpectedPageStateError("Gemini returned an invalid page snapshot.")
        raw_turns, errors = raw.get("turns"), raw.get("errors")
        stopping, signed_out = raw.get("stopping"), raw.get("signed_out")
        temporary = raw.get("temporary")
        if not is_list(raw_turns) or not is_str_list(errors):
            raise UnexpectedPageStateError("Gemini returned an invalid page snapshot.")
        turns: list[_Turn] = []
        for turn in raw_turns:
            if (
                not is_str_keyed_mapping(turn)
                or not all(
                    isinstance(turn.get(key), str) for key in ("id", "prompt", "text")
                )
                or not all(
                    isinstance(turn.get(key), bool) for key in ("complete", "busy")
                )
            ):
                raise UnexpectedPageStateError(
                    "Gemini returned an invalid conversation turn."
                )
            turns.append(
                _Turn(
                    id=str(turn["id"]),
                    prompt=str(turn["prompt"]),
                    text=str(turn["text"]),
                    complete=bool(turn["complete"]),
                    busy=bool(turn["busy"]),
                )
            )
        if (
            not isinstance(stopping, bool)
            or not isinstance(signed_out, bool)
            or not isinstance(temporary, bool)
        ):
            raise UnexpectedPageStateError(
                "Gemini returned invalid loading/authentication state."
            )
        if self._is_temporary and not temporary:
            raise GeminiTemporaryChatError(
                "Gemini left Temporary chat mode; refusing further operations in this context."
            )
        self._chat_url = page.url
        return _Snapshot(
            turns=turns,
            errors=errors,
            stopping=stopping,
            signed_out=signed_out,
            temporary=temporary,
        )


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _validate_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and greater than zero")


def _chat_url(chat: str) -> str:
    value = chat.strip()
    if re.fullmatch(r"[a-fA-F0-9]+", value):
        return f"{BASE_URL}/{value}"
    url = urlsplit(value)
    if (
        url.scheme == "https"
        and url.netloc == "gemini.google.com"
        and re.fullmatch(r"/app/[a-fA-F0-9]+/?", url.path)
        and not url.query
        and not url.fragment
    ):
        return value.rstrip("/")
    raise ValueError(
        "chat must be a Gemini hexadecimal chat ID or https://gemini.google.com/app/<id> URL"
    )
