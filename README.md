# generative-ai

Async Playwright automation for [Google Flow](https://flow.google.com/).

## Install

```bash
uv add git+https://github.com/ThanapatFungpharit/generative-ai
uv run playwright install chromium
```

`GoogleFlowProject` and the generation models are also re-exported from the
top-level `generative_ai` package.

## Usage

```python
from pathlib import Path

from playwright.async_api import async_playwright

from generative_ai.googleflow import GoogleFlowProject


async def main() -> None:
    async with async_playwright() as playwright:
        # A persistent profile keeps the Google sign-in between runs.
        context = await playwright.chromium.launch_persistent_context(
            "google-profile", headless=False
        )
        project = await GoogleFlowProject.new(context)

        uploads = await project.upload_media([Path("cat.png")])
        images = await project.generate_image(
            "a cat astronaut", model="Nano Banana 2.1", aspect_ratio="1:1", outputs=1
        )
        videos = await project.generate_video(
            "the cat floats through the station", start_frame=images[0]
        )
        await project.download_media(videos[0], Path("downloads"))
        await project.close()
```

## `GoogleFlowProject`

| Area | Methods |
| --- | --- |
| Projects | `open`, `new`, `rename`, `delete`, `close` |
| Generation | `generate_image`, `generate_video` (Frames or Ingredients), `configure`, `get_generation_settings`, `set_agent_mode` |
| Media | `upload_media`, `list_media`, `download_media`, `reuse_prompt`, `toggle_favourite`, `set_project_cover`, `move_to_bin`, `restore_from_bin` |
| Scenes | `create_scene` |

Media are addressed by Flow's media ID (the tile's `data-media-id`), which
`upload_media`, `generate_*` and `list_media` return. Model, aspect-ratio and
download-quality values are Flow's own labels; the options depend on the mode
and the account, and an unavailable value raises an error listing what Flow
offers.

Selectors and linear UI flows live in `src/generative_ai/googleflow/manifests/`.
Pages are kept at least 1280×720 because narrower viewports switch Flow to a
mobile layout with different controls.
