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
| Generation | `generate_image`, `generate_video` (Frames or Ingredients), `start_image`, `start_video`, `wait_for_generations`, `configure`, `get_generation_settings`, `set_agent_mode` |
| Media | `upload_media`, `list_media`, `download_media`, `reuse_prompt`, `toggle_favourite`, `set_project_cover`, `move_to_bin`, `restore_from_bin` |
| Scenes | `create_scene` |

Media are addressed by Flow's media ID (the tile's `data-media-id`), which
`upload_media`, `generate_*` and `list_media` return. Video tiles expose no
ID once rendered, so `download_media` falls back to the media's editor page,
which also accepts the IDs a video's thumbnail and editor URL carry.

Generations can run concurrently in one project: `start_image` and
`start_video` return a `GenerationJob` as soon as Flow accepts it, and
`wait_for_generations` maps jobs to media IDs, matching each new tile to its
job by the prompt Flow shows for it:

```python
jobs = [await project.start_video(prompt, start_frame=frame) for prompt, frame in shots]
videos = await project.wait_for_generations(jobs)
```

Model, aspect-ratio and
download-quality values are Flow's own labels; the options depend on the mode
and the account, and an unavailable value raises an error listing what Flow
offers.

Selectors and linear UI flows live in `src/generative_ai/googleflow/manifests/`.
Every page's viewport is locked to 1280×720, whatever the context or window
size, because Flow's layout shifts with the viewport (narrower ones switch to a
mobile layout with different controls).

## Joining clips into one video

Flow returns each generation as a separate clip. `assemble_video` (needs
`ffmpeg` and `ffprobe` on `PATH`) joins clips into one file where each lasts
exactly `clip_duration` — longer clips are trimmed, shorter ones hold their
last frame — with dip-to-black transitions and optional burned-in captions:

```python
from generative_ai import Caption, Clip, assemble_video

await assemble_video(
    [Clip(Path("scene_1.mp4"), Caption("1 · The basics", "One fact line")), ...],
    Path("final.mp4"),
    clip_duration=8.0,
)
```

## Adding a site

Site-independent code lives in `generative_ai.site`; `googleflow` is the
reference implementation. A new site `foo` needs:

1. `src/generative_ai/foo/manifests/selectors.yaml` and
   `manifests/actions/*.yaml` — `web_page` selectors and linear UI flows.
2. `foo/constants.py` — a `Site` registered with `register_site`:

   ```python
   SITE = register_site(
       Site(name="foo", base_url="https://foo.example/", manifests_dir=_MANIFESTS_DIR)
   )
   ```

   `Site.attach(page)` sets timeouts and locks the viewport, then loads every
   manifest; `get_site("foo")` finds it by name.
3. `foo/exceptions.py` — errors deriving from `SiteError`.
4. `foo/page.py` — a `SitePage` subclass. Implement `_closed_error`, decorate
   public operations with `@page_required` (serialises operations and requires
   a live page), build instances with `await SITE.attach(page)`, and use
   `close_page_and_report_failure` / `note_cleanup_failure` in constructors'
   failure paths.
5. Re-export the public names from `foo/__init__.py`.
