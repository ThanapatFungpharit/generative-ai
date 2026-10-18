# Google Flow

[Back to README](../README.md)

`GoogleFlowProject` owns one tab in a caller-provided Chromium browser context.
The [README quickstart](../README.md#google-flow-quickstart) shows browser setup,
generation, download, and cleanup. Examples below run inside an async function
with an open `project` and, where needed, its browser `context`.

## Projects and page ownership

| Operation | Result |
| --- | --- |
| `await GoogleFlowProject.new(context)` | Creates a saved project in a new tab |
| `await GoogleFlowProject.open(project_magic, context)` | Opens an existing project by identifier, not by URL |
| `await project.rename(name)` | Trims surrounding whitespace and verifies the saved header name |
| `await project.close()` | Closes the tab; the saved project remains in Flow |
| `await project.delete()` | Permanently deletes the project, verifies the site transition, and closes its tab |
| `project.project_magic`, `project.project_url`, `project.is_closed` | Stable project identifier, canonical URL, and local facade state |

Constructors clean up their tab on failure or cancellation. They never close
the caller's context. Public operations on one facade share an operation lock;
operations on separate facades can run concurrently.

Each Flow tab is fixed at **1280 × 720** to keep the desktop layout consistent.
Before navigation, `Site.attach` enables Chromium focus emulation using a
page-scoped DevTools session retained until the tab closes. This supports
background rendering while other tabs are active, including Chromium contexts
connected with `no_defaults=True`. The context must support `new_cdp_session`;
setup failures propagate through constructor cleanup. This mechanism does not
require tab switching or a background polling loop.

## Settings and generation

`GenerationSettings` supports `mode`, `video_input`, `aspect_ratio`, `model`,
`resolution`, `duration`, and `outputs`. `None` leaves a current value unchanged.
Labels are read and validated against Flow's UI; availability depends on mode,
model, and account. Output counts are 1–4; durations are positive seconds.
`video_input`, `resolution`, and `duration` cannot be specified with image mode.

```python
from generative_ai.googleflow import GenerationMode, GenerationSettings

await project.configure(
    GenerationSettings(
        mode=GenerationMode.IMAGE,
        model="Nano Banana 2.1",
        aspect_ratio="1:1",
        outputs=1,
    )
)
observed = await project.get_generation_settings()
print(observed)
```

Settings are selected in dependency order, including the model before its
dependent fields, and then read back. If Flow resets requested values, the
library applies them once more. Unavailable options or a persistent mismatch
raise `GenerationSettingsError` with available options or requested/observed
values. Explicit settings on generation calls use the same verification.

`set_agent_mode(True)` emits a `UserWarning`: Flow removes generation settings,
and this state can carry over to other projects. Turn Agent mode off and
reapply settings before relying on them. `generate_*` and `start_*` already
switch it off before submission.

| Method | Arguments beyond the prompt | Returns |
| --- | --- | --- |
| `generate_image` | `model`, `aspect_ratio`, `outputs`, `references`, `timeout` | Tuple of generated media IDs |
| `generate_video` | `model`, `aspect_ratio`, `outputs`, `resolution`, `duration`, `start_frame`, `end_frame`, `ingredients`, `timeout` | Tuple of generated media IDs |
| `start_image`, `start_video` | Same options as their `generate_*` counterparts, except `timeout` | `GenerationJob` once Flow accepts the submission |
| `wait_for_generations(jobs, timeout=None)` | Jobs submitted by this facade | Mapping from each job to a tuple of media IDs |

Image `references` are existing media IDs added as ingredients. Video calls
use Frames mode when `start_frame` is supplied; `end_frame` requires a start
frame. Without frames, they use Ingredients mode with optional `ingredients`.
Frames and ingredients cannot be combined.

```python
from pathlib import Path

source = Path("cat.png")
uploaded = await project.upload_media([source])
videos = await project.generate_video(
    "The cat floats gently through a space station",
    model="Veo 3.1 - Fast",
    start_frame=uploaded[source],
    aspect_ratio="16:9",
    resolution="720p",
    duration=8,
    outputs=1,
)
```

The model and settings here are example Flow labels. Video generation spends
account credits. Default collection timeouts are **180 seconds for images**
and **900 seconds for videos**; `timeout` overrides the collection wait.
Submission and UI actions have their own deadlines.

## Concurrent jobs and recovery

Submit several jobs before waiting to let Flow generate them concurrently.
Submissions on one facade are serialized to protect the composer; generation
continues remotely between submissions.

```python
prompts = ("a blue cube on grass", "a yellow sphere on sand")
jobs = [
    await project.start_image(prompt, model="Nano Banana 2.1", outputs=1)
    for prompt in prompts
]
results = await project.wait_for_generations(jobs, timeout=300)
for job in jobs:
    print(job.prompt, results[job])
```

Collection uses media identities and the prompt Flow shows for each output,
including editor inspection. Uploads and a fixed-size virtualized grid do not
count as evidence that a job finished. Video content/editor ID relationships
are retained within the project instance when thumbnails change.

Collect a job before submitting another with the same prompt and output kind;
Flow exposes too little information to distinguish those concurrent jobs.
Repeated references to one job in a collection call return it once. A job
belongs to the facade that submitted it and cannot be collected again after
being returned.

Waiting may collect other outstanding jobs of the same kinds before returning
the requested ones. Unrequested results remain available for a later call.
An output dropped by Flow, for example by filtering, maps to an empty tuple;
the `generate_*` convenience methods instead raise `GenerationError` when no
output is produced.

After a collection timeout, call `wait_for_generations` again with the same
unreturned jobs while the facade remains usable. Retaining explicit job handles
with `start_*` makes this recovery possible without submitting another request.

## Media and scenes

| Method | Behavior |
| --- | --- |
| `upload_media(files)` | Returns a mapping from each `Path` to its media ID; duplicate paths are rejected |
| `list_media()` | Returns rendered `MediaItem` tiles, newest first by default; this is not a complete inventory of a large virtualized project |
| `download_media(media_id, destination, quality=None)` | Returns the saved `Path`; an existing directory uses Flow's suggested filename |
| `reuse_prompt(media_id)` | Returns and reuses the item's prompt |
| `toggle_favourite(media_id)` | Toggles its favourite state |
| `set_project_cover(media_id)` | Sets it as the project cover |
| `move_to_bin(media_id)`, `restore_from_bin(media_id)` | Moves or restores media through Flow's bin |
| `create_scene()` | Creates an empty scene, returns its ID, and returns to the workspace |

`MediaItem` exposes `title`, `kind`, `media_id`, and `pending`. Scenes,
collections, and pending generations may have no media ID. Pass the returned
media IDs to media operations, rather than grid positions or filenames.

Download qualities use the leading menu label, such as `"1K"`, `"2K"`, or
`"720p"`. `None` selects the original size, falling back to the first enabled
entry. Some upscales spend credits. Upload downloads ignore quality. Missing
video grid IDs fall back to the media editor; unavailable qualities and upscale
failures remain download errors. Create a destination directory before passing
it if you want Flow's suggested filename.

## Errors and cleanup

Import provider exceptions from `generative_ai.googleflow`; all derive from
`GoogleFlowError`. Principal errors include `GenerationSettingsError`,
`GenerationError`, `MediaFileReadError`, `MediaUploadError`,
`MediaNotFoundError`, `MediaActionError`, and the `Project*Error` lifecycle
errors. Invalid local arguments raise `ValueError`.

Error causes and `__notes__` can include Flow messages, available choices,
recovery failures, and cleanup details. If deletion confirmation was attempted
but the result cannot be verified, the facade is invalidated and the error
reports unknown remote state. Check Flow before deciding what to do next.
A verified deletion followed by failed tab cleanup raises `ProjectCloseError`
with a note that deletion succeeded.

Local browser coverage and opt-in real-site commands are described in
[Development and testing](DEVELOPMENT.md).
