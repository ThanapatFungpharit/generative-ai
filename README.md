# generative-ai

Async Python tools for interacting with AI websites through
[CloakBrowser](https://github.com/CloakHQ/CloakBrowser), Playwright-compatible
browser contexts, and YAML workflows.

The project focuses on website interactions: conversations, model and settings
selection, projects, generated content, and session lifecycle. Each supported
website exposes its own operations through a Python API built on shared browser
and workflow infrastructure.

Current integrations are [Google Gemini](https://gemini.google.com/app) for text
conversations and [Google Flow](https://flow.google.com/) for project and media
workflows, including image and video generation. Local FFmpeg video assembly
remains available as an optional utility. See
[Project direction and architecture](docs/ARCHITECTURE.md) for scope and extension
guidelines.

| Guide | Contents |
| --- | --- |
| [Project direction and architecture](docs/ARCHITECTURE.md) | AI website interaction, shared infrastructure, integration boundaries, and development priorities |
| [Gemini](docs/GEMINI.md) | Text conversations, model/thinking selection, response recovery, Temporary chats, deletion, and DOM notes |
| [Google Flow](docs/GOOGLE_FLOW.md) | Projects, verified settings, image/video generation, concurrent jobs, media, and errors |
| [Development and testing](docs/DEVELOPMENT.md) | Local setup, live-test flags, profiles, checks, and adding a site |
| [Optional video assembly](docs/VIDEO.md) | Joining clips, captions, output settings, and failure handling |

## Install

Requires **Python 3.14 or newer** and Git for the Git-based package dependencies.

```bash
uv add git+https://github.com/ThanapatFungpharit/generative-ai
```

CloakBrowser downloads its patched Chromium on first launch. FFmpeg and FFprobe
are only needed for video assembly. Browser automation requires a Google account
with access to the selected site and a persistent profile signed in to it.

For a source checkout, install the development dependencies and open a browser
for sign-in:

```bash
uv sync
uv run python -m tests.live.sign_in
uv run python -m tests.live.sign_in --site gemini
```

Complete sign-in and wait for the site to load, then close the browser window
before using that profile again. The defaults are `./google-profile` for Flow
and `./gemini-profile` for Gemini. The sign-in helper belongs to the source
checkout; applications using the installed library can sign in through their
own headed CloakBrowser session.

One persistent profile can be opened by only one browser at a time. Multiple
project or chat tabs can share one already-open browser context. Each facade
owns its tab; the application owns and closes the browser context.

## Gemini quickstart

This example sends two prompts in one saved conversation. It consumes Gemini
text quota and leaves the conversation in the account's history.

```python
import asyncio

from cloakbrowser import launch_persistent_context_async

from generative_ai.gemini import GeminiChat


async def main() -> None:
    context = await launch_persistent_context_async(
        "gemini-profile", headless=False, humanize=True
    )
    try:
        chat = await GeminiChat.new(context)
        try:
            print("Available models:", await chat.list_models())
            print("Current model:", await chat.get_model())
            response = await chat.send("Explain why the sky is blue.")
            print(response.text)
            print(response.chat_url, response.turn_id)
            follow_up = await chat.send("Summarize that in one sentence.")
            print(follow_up.text)
        finally:
            if not chat.is_closed:
                await chat.close()
    finally:
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Gemini supports text chat, live model and thinking-level selection, saved-chat
reopening/deletion, and native Temporary chats on the observed English UI.
File attachments, image/music generation, and Canvas are outside its public
API. See the [Gemini guide](docs/GEMINI.md) for timeout recovery and lifecycle
examples.

## Google Flow quickstart

This example creates a saved project, generates an image, and downloads it.
Generation uses the account's credits or quota. Model labels depend on the
account; replace the example label if Flow offers different options.

```python
import asyncio
from pathlib import Path

from cloakbrowser import launch_persistent_context_async

from generative_ai.googleflow import GoogleFlowProject


async def main() -> None:
    context = await launch_persistent_context_async(
        "google-profile", headless=False, humanize=True
    )
    try:
        project = await GoogleFlowProject.new(context)
        try:
            print(project.project_magic, project.project_url)
            images = await project.generate_image(
                "a cat astronaut",
                model="Nano Banana 2.1",
                aspect_ratio="1:1",
                outputs=1,
            )
            destination = Path("output/downloads")
            destination.mkdir(parents=True, exist_ok=True)
            saved = await project.download_media(images[0], destination)
            print(saved)
        finally:
            if not project.is_closed:
                await project.close()
    finally:
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
```

`close()` leaves the project saved in Flow. Reopen it with
`GoogleFlowProject.open(project_magic, context)`, using the identifier printed
above. See the [Flow guide](docs/GOOGLE_FLOW.md) for video generation, uploads,
settings read-back, and collecting several generation jobs.

`GoogleFlowProject`, `GeminiChat`, their result models, and the video assembly
helpers are also available from `generative_ai`. Import provider-specific
exceptions from `generative_ai.googleflow` or `generative_ai.gemini`.

## Testing

From a source checkout, run the local suite with:

```bash
uv run python -m playwright install chromium
uv run pytest
```

Local tests include browser fixtures; they do not contact the real sites.
Live tests are skipped by default. `--live` enables real-site checks, including
Gemini prompts that consume text quota; `--live-credits` additionally enables
Flow generation-credit tests and implies `--live`. See
[Development and testing](docs/DEVELOPMENT.md) for focused commands, profile
options, prerequisites, and the difference between local and live coverage.
