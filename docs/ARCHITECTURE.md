# Project direction and architecture

[Back to README](../README.md)

`generative-ai` focuses on interacting with AI websites through their browser
interfaces. The reusable unit is a website integration that supports the site's
conversations, settings, content, and lifecycle operations. Image and video
generation are capabilities of integrations that offer them.

## Current scope

| Component | Responsibility |
| --- | --- |
| `generative_ai.site` | Shared site definitions and registry, browser protocols, manifest loading, page activity, operation locking, errors, and cleanup helpers |
| `generative_ai.gemini` | Text conversations, model and thinking-level selection, response collection and recovery, saved chats, and Temporary chats |
| `generative_ai.googleflow` | Projects, generation settings, image/video jobs, uploads, downloads, media actions, and scenes |
| `generative_ai.video` | Optional local FFmpeg assembly of downloaded clips |

Gemini and Flow are the current website integrations. Each exposes a Python
interface for supported interactions, with details in the [Gemini](GEMINI.md)
and [Flow](GOOGLE_FLOW.md) guides. Supporting a feature or another website
requires an explicit implementation and validation against that site's UI.

## How the layers fit together

Applications create and own the browser context, including the persistent
profile used for sign-in. They pass that context to an integration such as
`GeminiChat` or `GoogleFlowProject`. Each instance creates and owns one tab;
closing it leaves the application's browser context available to other tabs.
Remote deletion is a separate operation from closing a tab.

The `Site` definition describes the website's URL, manifests, timeouts, and
optional viewport or focus requirements. `Site.attach(page)` loads the
manifests and applies browser requirements before navigation. The registry
provides lookup by site name; it stores site definitions, while the integration
classes construct conversations and projects.

`SitePage` supplies page lifecycle state and an operation lock. Integrations
use `@page_required` to serialize public operations on the same instance while
allowing separate instances to operate concurrently. Provider exceptions derive
from `SiteError` so callers can handle common failures or a specific site's
errors.

Each integration keeps selectors in `manifests/selectors.yaml` and linear UI
actions in `manifests/actions/*.yaml`, executed by `web_page`. Python handles
stateful behavior such as response attribution, generation job tracking,
recovery, deadlines, and result models. Generic browser workflow mechanics
belong in `web_page`; website behavior belongs in the integration.

## Guidelines for new interactions

- Expose operations that reflect what the website supports. Use result models
  suited to the interaction, such as chat responses or media jobs.
- Verify observable state after UI changes. Settings selection should read back
  the selected value; completion should identify the corresponding response or
  output; deletion should verify the resulting site state.
- Keep ownership explicit. Clean up an integration's tab on constructor failure
  or cancellation, preserve the caller's context, and report uncertain remote
  state when an operation cannot be verified.
- Preserve pending work across recoverable timeouts. Allow callers to resume
  collection without submitting the same prompt or generation again.
- Share infrastructure when multiple integrations need the same behavior.
  Keep site-specific selectors, settings, models, and recovery decisions with
  their integration.
- Cover deterministic behavior locally and keep real-site validation opt-in.
  Document supported UI languages, account requirements, quota effects, and
  the boundary of live verification.

## Development priorities

Extend supported website interactions and improve their reliability, recovery,
and state verification. Add other AI websites through the same site definition,
manifest, and page lifecycle structure as their workflows are implemented.
Applications can compose these integrations into chat, research, media, or
other workflows according to their needs.

Video generation remains part of Flow's website API. The local assembly helper
remains available for callers that need it, with FFmpeg required only when that
utility is used. Website integrations and shared infrastructure define the
project's main direction.

See [Development and testing](DEVELOPMENT.md#adding-a-site) for the concrete
steps to add a site and the local and live validation commands.
