# generative-ai

Async [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) automation for [Google Flow](https://flow.google.com/).

## Install

```bash
uv add git+https://github.com/ThanapatFungpharit/generative-ai
# CloakBrowser downloads its patched Chromium on first launch.
```

`GoogleFlowProject` and the generation models are also re-exported from the
top-level `generative_ai` package.

## Usage

```python
from pathlib import Path

from cloakbrowser import launch_persistent_context_async

from generative_ai.googleflow import GoogleFlowProject


async def main() -> None:
    # A persistent profile keeps the Google sign-in between runs.
    context = await launch_persistent_context_async("google-profile", headless=False)
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

> **Agent mode:** `set_agent_mode(True)` makes Flow remove the generation
> settings, and the change carries over to other projects, leaving them in an
> unexpected state. It emits a `UserWarning`; turn it off and re-apply settings
> afterwards. `generate_image` and `generate_video` already switch it off.

Media are addressed by Flow's media ID (the tile's `data-media-id`), which
`upload_media`, `generate_*` and `list_media` return. Video tiles expose no
ID once rendered, so `download_media` falls back to the media's editor page,
which also accepts the IDs a video's thumbnail and editor URL carry.
The project instance retains the relationship between those two IDs, so its
media actions can still find a video after Flow replaces its thumbnail URL.

Generations can run concurrently in one project: `start_image` and
`start_video` return a `GenerationJob` as soon as Flow accepts it, and
`wait_for_generations` maps jobs to media IDs, matching each new tile to its
job by the prompt Flow shows for it:

```python
jobs = [await project.start_video(prompt, start_frame=frame) for prompt, frame in shots]
videos = await project.wait_for_generations(jobs)
```

Collection compares media identities and verifies each output's prompt, so
uploads and a fixed-size virtualized grid do not change job attribution.
Collect a job before submitting another with the same prompt and output kind;
Flow does not expose enough information to distinguish those concurrent jobs.
Repeated jobs in one `wait_for_generations` call are returned once.

Model, aspect-ratio and
download-quality values are Flow's own labels; the options depend on the mode
and the account, and an unavailable value raises an error listing what Flow
offers.

Selectors and linear UI flows live in `src/generative_ai/googleflow/manifests/`.
Every page's viewport is locked to 1280×720, whatever the context or window
size, because Flow's layout shifts with the viewport (narrower ones switch to a
mobile layout with different controls).

Each Flow page also enables Chromium focus emulation before navigation. This
keeps visibility- and focus-dependent JavaScript active while other project
tabs are being used, including projects opened concurrently with
`asyncio.gather`. It uses one page-scoped DevTools session, retained until the
page closes, without a polling loop, tab switching, or extra browser processes.
Normal Playwright launches already apply this emulation; attaching it here
also covers Chromium contexts connected with `no_defaults=True`. Flow contexts
must support `new_cdp_session`; setup errors propagate through the project
constructor and its page cleanup instead of leaving a partially configured tab.

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

## Live tests

`tests/live/` drives the real Flow site and is skipped unless `--live` is
passed. Each test creates throwaway projects and deletes them afterwards. Tests
that spend generation credits also need `--live-credits`.

```bash
uv run python -m tests.live.sign_in   # once: sign in to Google in ./google-profile
uv run pytest --live
```

`--flow-profile` (or `GOOGLE_FLOW_PROFILE`) picks another profile. Sign in with
`tests.live.sign_in` (CloakBrowser), since Google rejects sign-in in stock
Playwright Chromium.

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
   manifest; `get_site("foo")` finds it by name. Set `keep_active=True` for
   Chromium sites that require focus emulation during parallel execution.
3. `foo/exceptions.py` — errors deriving from `SiteError`.
4. `foo/page.py` — a `SitePage` subclass. Implement `_closed_error`, decorate
   public operations with `@page_required` (serialises operations and requires
   a live page), build instances with `await SITE.attach(page)`, and use
   `close_page_and_report_failure` / `note_cleanup_failure` in constructors'
   failure paths.
5. Re-export the public names from `foo/__init__.py`.
