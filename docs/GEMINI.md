# Gemini browser support

[Back to README](../README.md)

`GeminiChat` supports text conversations on Gemini's English website using the
same `Site`, `SitePage`, browser protocols, and YAML engine as Flow. Start with
the [complete quickstart](../README.md#gemini-quickstart) for browser setup and
cleanup. Examples below run inside an async function with an open `chat` and
its browser `context`.

## Public API

Import `GeminiChat`, its result models, and provider exceptions from
`generative_ai.gemini`.

| Area | Methods and properties |
| --- | --- |
| Saved chats | `new(context)`, `open(chat_id_or_url, context)`, `close()`, `delete(timeout=30)` |
| Native Temporary mode | `temporary(context)` async context manager; `is_temporary` property |
| Text | `send(prompt, model=None, thinking_level=None, timeout=180)`, `wait_for_response(timeout=180)`, `stop_response()`, `get_messages()` |
| Models | `list_models()`, `get_model()`, `set_model(label)` |
| Thinking | `list_thinking_levels()`, `get_thinking_level()`, `set_thinking_level(label)` |
| Page state | `chat_url`, `is_closed` |

`new` creates a tab with an empty composer; the conversation becomes a saved
chat after submission. `close()` closes only that tab and leaves saved history
in Gemini. Reopen a saved conversation with:

```python
from generative_ai.gemini import GeminiChat

reopened = await GeminiChat.open(response.chat_url, context)
try:
    messages = await reopened.get_messages()
    for message in messages:
        print(message.role, message.text, message.turn_id)
finally:
    if not reopened.is_closed:
        await reopened.close()
```

`GeminiResponse` has `text`, `turn_id`, and `chat_url`. `GeminiMessage` has
`role` (`"user"` or `"assistant"`), `text`, and `turn_id`; the user and assistant
messages in one turn share its ID. `get_messages()` returns a tuple of the
currently rendered messages, without fetching virtualized older history.

## Models and thinking levels

Discover choices from the account's live menus. Labels are account dependent;
disabled and upgrade entries are excluded. Selection is checked by reopening
the relevant menu and reading the selected option.

```python
models = await chat.list_models()
print(models)
model = await chat.get_model()
await chat.set_model(model)

levels = await chat.list_thinking_levels()
print(levels, await chat.get_thinking_level())
if "High" in levels:
    await chat.set_thinking_level("High")

# send() applies the model before any requested thinking level.
response = await chat.send("Explain this proof.", model=model)
```

Pass `thinking_level="High"` to `send()` only if that label is available for
the model you select. A model without a thinking submenu returns `()` from
`list_thinking_levels()` and `None` from `get_thinking_level()`. Unavailable
choices or failed selection verification raise `GeminiModelError` or
`GeminiThinkingLevelError`, including the available labels or the mismatch.
Settings cannot be changed while a response is pending.

## Timeouts, cancellation, and response recovery

`send()` submits once and returns only the matching completed text response.
Its default 180-second deadline covers configuration, submission, and
collection. Completion requires a new prompt-matching turn with a site ID,
a complete footer, no busy response content, and no visible Stop response
control. A pause in streaming is insufficient.

`GeminiTimeoutError` distinguishes two cases in its message:

- Before submission, no turn is pending. There is no response to resume.
- After submission was attempted, the turn remains pending, including an
  ambiguous click. Resume that same turn with the call below, or stop it.

```python
# Use after an error reports that this chat has a pending response.
response = await chat.wait_for_response(timeout=180)
print(response.text)
```

Cancellation during submission/collection also retains pending state. A site
alert or repeated snapshot failure may leave it pending as well. `send()`
refuses another prompt until the pending turn is collected or abandoned with
`await chat.stop_response()`. Stopping verifies that Stop disappears and clears
the local pending state; it does not return a partial `GeminiResponse`.

Public operations share a per-chat lock. If another task is currently awaiting
`send()`, cancel and await that task before calling `stop_response()`; otherwise
the stop operation waits behind the active send. Separate chat facades can run
concurrently. Each uses Chromium focus emulation for background rendering and
requires a context with `new_cdp_session` support.

## Temporary chats and saved-chat deletion

Use Gemini's native Temporary mode with an async context manager:

```python
async with GeminiChat.temporary(context) as temporary:
    assert temporary.is_temporary
    response = await temporary.send("Explain this example.")
    print(response.text)
# The owned tab is closed; the caller's browser context remains open.
```

Native mode is verified before yielding and checked during later operations.
Leaving it raises `GeminiTemporaryChatError` before another prompt can be sent.
Cleanup runs after normal exit, failure, or cancellation and preserves the
body's original exception, attaching any cleanup error as a note. Temporary
responses use the `/app` URL, which cannot reopen the conversation. They follow
Gemini's native retention behavior; the observed banner is recorded below.

`await chat.delete(timeout=30)` **permanently deletes the current saved
conversation**, verifies the return to an empty composer, and closes its tab.
Collect or stop pending responses first. Unsent and native Temporary chats
raise `GeminiChatDeletionError` because there is no saved chat to delete.
If confirmation was attempted but the result is unknown, the facade is
invalidated and confirmation is never retried automatically. A failure before
confirmation cancels the dialog and keeps the tab usable.

## Errors and supported scope

All provider errors derive from `GeminiError`. Use `GeminiAuthenticationError`
for signed-out profiles, `GeminiOpenError` for unexpected pages or browser
verification, `GeminiGenerationError` for submission/response/site failures,
and its `GeminiTimeoutError` subclass for deadlines. Model/thinking, lifecycle,
and Temporary-mode errors have the specific classes described above;
`GeminiChatClosedError` and `GeminiChatCloseError` distinguish a closed facade
from failed tab cleanup. Invalid local arguments raise `ValueError`.

File attachments, image/music generation, and Canvas are outside this public
API. Account-dependent tools and other UI languages need additional selector
work. For sign-in, unusual-traffic checks, test commands, and profile ownership,
see [Development and testing](DEVELOPMENT.md).

## Recorded implementation observations (October 18, 2026)

The provider was based on interactive inspection of the actual signed-in
`https://gemini.google.com/app` page on October 18, 2026, followed by running
the Python implementation against a signed-in CloakBrowser profile. No Gemini
API client, private RPC, internal Angular state, or authentication token is used.

## Observed page structure

Gemini uses Angular custom elements with asynchronously mounted controls.
Initial composer readiness does not imply that the model picker, sidebar,
or existing conversation history has loaded. Background execution uses the
repository's existing page-scoped Chromium focus emulation.

| Element | Observed DOM and behavior |
| --- | --- |
| Composer | `rich-textarea [contenteditable="true"][role="textbox"]`; a Quill `.ql-editor` labeled `Enter a prompt for Gemini`. A separate `.ql-clipboard` is also contenteditable and must not be filled. |
| Submit | `button[aria-label="Send message"]` appears after entering nonempty text. Angular state can lag the DOM input event. |
| Generating | `button[aria-label="Stop response"]` appears when a request is running. It may disappear before the response's busy state and footer finish updating. |
| Turn | `.conversation-container` initially mounts without an ID. Its hexadecimal `id` arrives after acceptance. Only containers with nonempty IDs are attributed. |
| User prompt | `user-query .query-text-line`; the neighboring hidden `You said` heading duplicates the prompt and must be excluded. Multiple prompt lines are joined in order. |
| Assistant text | `model-response message-content .markdown`; its `aria-busy` transitions to `false`. |
| Finished response | `model-response .response-footer.complete`; completion also requires no busy response nodes and no visible Stop response control. |
| Model picker | `button[aria-label^="Open mode picker, currently "]`; the button's short label can differ from the full menu label. |
| Model menu | `[data-test-id="gem-mode-menu"][data-visible="true"]`, mounted on demand. Nested thinking-level menus can exist in the DOM with `data-visible="false"`. |
| Model option | A `[role="menuitem"][data-mode-id]` with a `.label` child. The selected entry has `.selected`. Entries with `data-mode-id^="upsell_"` may have `aria-disabled="false"` but open an upgrade flow. They are excluded. |
| Thinking submenu | `[role="menuitem"][value="thinking_level"]` opens a nested `.submenu-container > [role="menu"][data-visible="true"]`. Its generated ID changes across sessions. Level entries use `.label` and `.selected`; disabled entries are excluded. |
| Temporary mode | `button[aria-label="Temporary chat"]` toggles native mode. The persistent `chat-window.is-temporary-chat` marker remains during generation and after completion. The URL remains `/app`; the toggle's accessible name does not change when active. |
| Current chat actions | `button[aria-label="Open menu for conversation actions."]` arrives lazily after a saved conversation loads. Its menu's Delete entry has `data-test-id="delete-button"`. This avoids dependence on virtualized sidebar history. |
| Delete confirmation | `[role="dialog"][aria-label="Delete chat"]` presents Cancel and Delete buttons. Its text states that prompts, responses, feedback, and created content are deleted. |
| Errors | Visible `[role="alert"]` and `mat-snack-bar-container` messages are captured during response polling. |
| Tools | `Upload & tools` opens a lazy menu containing Upload files, Add from Drive, Create image, Create music, and Canvas. These features are outside the text provider API. |

Selectors are centralized in `src/generative_ai/gemini/manifests/selectors.yaml`.
The linear flows use the same `web_page` engine as Flow. Python handles lifecycle,
validated DOM snapshots, turn attribution, branching, and response deadlines.

## Workflow and failure behavior

1. Create a tab in the caller's context and attach the registered Gemini site.
2. Navigate and wait for the composer. Reject signed-out or unexpected pages.
   Opening an existing conversation also waits for a turn with a nonempty ID.
3. Read the selected and available models from the live menu. Selection is
   checked by reopening that menu and reading its selected entry. If requested,
   apply the thinking level after model selection, then reopen its submenu and
   verify the selected label as well.
4. Fill the composer, verify its text, and wait for the enabled Send control.
5. Record existing turn IDs, click Send exactly once, and wait for a new turn
   whose prompt matches the submitted text. Ignore placeholders without IDs.
6. Return text only after the complete/busy/Stop conditions agree. Do not use
   text stability, a fixed sleep, or network-idle as completion evidence.

One deadline bounds configuration, submission, and response collection. Polling
fails after five consecutive transient Playwright snapshot errors, while page
closure is reported immediately. Constructors close their own tab after
failure or cancellation, retaining cleanup failure details. They never close
the caller's context.

A timeout or cancellation after a submission attempt keeps the pending turn.
The caller can wait again without a second click, or stop/abandon it explicitly.
An ambiguous click is never retried automatically. Model changes and subsequent
prompts are rejected while that pending turn exists.

Thinking choices are model/account dependent: live inspection found Low,
Medium, High, and an additional Deep Think entry for Pro. The public API reads
the offered labels instead of defining a fixed enum. No submenu yields `()`
from `list_thinking_levels()` and `None` from `get_thinking_level()`; setting an
unavailable level raises `GeminiThinkingLevelError`.

Repeatedly opening the live submenu also exposed a positioning bug: Gemini
could render the submenu beyond the right edge of the viewport. Its focusable
menuitems still handled Enter correctly. Selection therefore uses the observed
keyboard interaction, then checks that the submenu closed and reopens it to
read the selected level. It does not force coordinate clicks or edit app state.

`GeminiChat.temporary(context)` is an async context manager over Gemini's native
Temporary mode. It verifies the persistent window marker before yielding and
checks it on subsequent snapshots, refusing operations if the page leaves
Temporary mode. Tab cleanup runs on normal exit, body error, activation failure,
or cancellation, retaining the caller's context and any original exception.
Cleanup bypasses the facade's operation lock so an abandoned background send
cannot prevent closing its owned tab. Gemini's observed banner says Temporary
chats do not appear in recent history and are stored for 72 hours for safety.

`delete()` operates on the current saved conversation through its own actions
menu. It requires an idle saved chat, checks that the conversation did not
change before confirmation, submits once, waits for the dialog to close and
the loaded empty `/app` composer, then invalidates the facade and closes its tab.
A timeout or cancellation after attempting confirmation leaves remote state
unknown; the facade is closed and no confirmation retry occurs. A failure
before confirmation cancels any opened deletion dialog and keeps the tab usable.

## Recorded validation

`tests/test_gemini.py` runs real Playwright locators and the actual manifests
against local fixtures derived from these observed DOM shapes. It exercises
lazy controls, delayed turn IDs, streaming and completion transitions, stale
same-prompt responses, multiline prompts, cancellation, timeout/resume,
signed-out cleanup, model availability/read-back, saved chat reopening, and
input validation. Thinking coverage includes lazy and offscreen submenus,
disabled/unavailable options, model-dependent choices, ignored selections,
pending-turn protection, and applying model settings before thinking settings.
Lifecycle coverage additionally checks native Temporary activation and mode
loss, cleanup on error/cancellation, delayed deletion after the dialog closes,
ambiguous confirmation, unsaved-chat rejection, and retention of other tabs.

`uv run pytest tests/live/test_gemini.py --live --gemini-profile <profile>` drives the real
website with the implemented provider: model discovery and verified selection,
two prompts in the same chat, distinct response attribution, message retrieval,
and reopening by URL. These tests consume text quota and leave their chat in
Gemini history. They do not generate paid media.
An additional live test discovers and selects every offered thinking level for
each available model, reading it back and restoring the original settings.
The lifecycle live tests send a prompt in native Temporary mode and delete a
disposable saved conversation created in that exact test, never an existing
user conversation.

For the lifecycle addition, live Chrome inspection verified Temporary mode
through an actual text response and inspected both delete-menu paths and the
confirmation dialog, cancelling the preview. Automated lifecycle live tests
were pending at the time of these notes because Google required a human
unusual-traffic check in the test profile. No permanent chat deletion was
performed during that inspection. These observations are historical evidence;
they do not establish that a later run or account will behave the same way.

Additional live probes verified recovery after a two-second response timeout:
`wait_for_response()` returned the completed response and the chat contained
exactly one user turn. Another probe cancelled the waiting coroutine, clicked
Stop response, and checked that the control disappeared and pending state was
cleared. With only the disposable browser context temporarily offline, Gemini
showed `Something went wrong (4)`; the provider surfaced it immediately as a
`GeminiGenerationError` without a resend. Connectivity and the original model
were restored after the probes.

Current control labels were observed in English. Other languages and
account-dependent tools require further live inspection before adding selectors.
