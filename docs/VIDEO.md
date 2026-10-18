# Video assembly

[Back to README](../README.md)

Video assembly is an optional local utility alongside the project's AI website
integrations. See [Project direction and architecture](ARCHITECTURE.md) for the
main scope.

`assemble_video` joins downloaded clips locally using FFmpeg and FFprobe.
It does not open a browser or spend generation credits. Install both tools on
`PATH`, or pass their executable paths with `ffmpeg=` and `ffprobe=`. Captioned
output also needs an FFmpeg build with the `drawtext` filter and a usable font.

```python
import asyncio
from pathlib import Path

from generative_ai import Caption, Clip, assemble_video, probe_duration


async def main() -> None:
    output = await assemble_video(
        [
            Clip(Path("scene_1.mp4"), Caption("1 · The basics", "One fact line")),
            Clip(Path("scene_2.mp4"), Caption("2 · A closer look")),
        ],
        Path("output/final.mp4"),
        clip_duration=8.0,
        fade=0.4,
        width=1280,
        height=720,
        fps=24,
    )
    print(output, await probe_duration(output))


if __name__ == "__main__":
    asyncio.run(main())
```

Each clip occupies `clip_duration` seconds. Longer clips are trimmed; shorter
clips hold their last frame and pad audio with silence. Inputs are letterboxed
to the chosen dimensions. Each fades in from and out to black, so the total
length remains `len(clips) * clip_duration` within frame rounding tolerance.
The output always has an audio track; silent clips receive silence.

Encoding uses H.264 (`libx264`, CRF 18, medium preset, `yuv420p`) and AAC audio
at 192 kbps, with `+faststart`. Codec and quality settings are currently fixed
by the helper; dimensions, frame rate, timing, and fonts are configurable.

| Argument | Default and behavior |
| --- | --- |
| `clips` | Nonempty sequence of `Clip(path, caption=None)` |
| `output` | Destination `Path`; missing parent directories are created |
| `clip_duration` | Required positive number of seconds per clip |
| `fade` | `0.4` seconds; must be between zero and half the clip duration |
| `width`, `height`, `fps` | `1280`, `720`, `24` |
| `font_file` | Optional font `Path`; otherwise a common system bold font is selected if available |
| `ffmpeg`, `ffprobe` | Optional executable paths; otherwise resolved from `PATH` |

`Caption(title, detail="")` burns a title and optional detail line into the
lower third. Omit the caption for an uncaptioned clip. `probe_duration(path,
ffprobe=None)` reads the first video stream's duration, falling back to the
container duration.

Assembly encodes into a unique temporary file beside the destination, probes
its duration, and replaces the destination only after that check passes.
Failure or cancellation removes that invocation's temporary file and preserves
the previous output. Concurrent calls use different temporary files; if they
target the same destination, the last successful replacement wins.

Empty clips or invalid durations raise `ValueError`; tool/probe/encode failures
and unexpected output duration raise `VideoAssemblyError`. Duration validation
does not assess visual quality or prompt adherence. See
[Development and testing](DEVELOPMENT.md) for the FFmpeg tests.
