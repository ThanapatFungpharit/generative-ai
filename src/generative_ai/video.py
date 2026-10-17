"""Assemble generated clips into one video with ffmpeg.

Generation sites return separate clips; this module joins them into a single
file where every clip lasts exactly the same time, with dip-to-black
transitions and optional burned-in captions. It needs ``ffmpeg`` and
``ffprobe`` on ``PATH`` (or passed explicitly).
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from generative_ai.site.types import Pixels, Seconds

_DEFAULT_FONTS: Final = (
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)
# The assembled duration may differ from the requested one by about a frame.
_DURATION_TOLERANCE_SECONDS: Final = 0.05
_AUDIO_SAMPLE_RATE: Final = 48_000


class VideoAssemblyError(RuntimeError):
    """Raised when ffmpeg cannot probe or assemble the clips."""


@dataclass(frozen=True, slots=True)
class Caption:
    """Text burned into the lower third of a clip: a title and a fact line."""

    title: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Clip:
    """One input video, optionally captioned."""

    path: Path
    caption: Caption | None = None


async def probe_duration(video: Path, *, ffprobe: str | None = None) -> Seconds:
    """Return the duration of ``video``'s first video stream in seconds."""
    output = await _run(
        _tool(ffprobe, "ffprobe"),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration:format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video),
    )  # fmt: skip
    for line in output.splitlines():
        try:
            return float(line)
        except ValueError:
            continue
    raise VideoAssemblyError(f"ffprobe reported no duration for {video}.")


async def assemble_video(
    clips: Sequence[Clip],
    output: Path,
    *,
    clip_duration: Seconds,
    fade: Seconds = 0.4,
    width: Pixels = 1280,
    height: Pixels = 720,
    fps: int = 24,
    font_file: Path | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> Path:
    """Join ``clips`` into ``output``, each lasting exactly ``clip_duration``.

    Longer clips are trimmed and shorter ones hold their last frame (and pad
    with silence), so the result is ``len(clips) * clip_duration`` long. Each
    clip fades in from and out to black over ``fade`` seconds, and clips
    are letterboxed to ``width`` x ``height``. Clips without sound get a
    silent track, so the output always has audio. Captions use
    ``font_file``, or a common system bold font when it is ``None``.

    Raises:
        ValueError: There are no clips, or a duration is out of range.
        VideoAssemblyError: ffmpeg fails, or the result has the wrong length.
    """
    if not clips:
        raise ValueError("clips must not be empty")
    if clip_duration <= 0:
        raise ValueError("clip_duration must be positive")
    if not 0 <= fade <= clip_duration / 2:
        raise ValueError("fade must be between 0 and half of clip_duration")

    ffmpeg = _tool(ffmpeg, "ffmpeg")
    ffprobe = _tool(ffprobe, "ffprobe")
    has_audio = await asyncio.gather(
        *(_has_audio(clip.path, ffprobe) for clip in clips)
    )
    font = font_file or next((path for path in _DEFAULT_FONTS if path.is_file()), None)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Encoded beside ``output`` and moved there once verified, so a failed or
    # interrupted run never leaves a truncated file under the final name.
    # The suffix stays last: ffmpeg picks the container from it.
    with tempfile.NamedTemporaryFile(
        prefix=f"{output.stem}.",
        suffix=f".partial{output.suffix}",
        dir=output.parent,
        delete=False,
    ) as temporary:
        partial = Path(temporary.name)
    try:
        await _encode(
            clips,
            partial,
            has_audio=has_audio,
            clip_duration=clip_duration,
            fade=fade,
            width=width,
            height=height,
            fps=fps,
            font=font,
            ffmpeg=ffmpeg,
        )
        expected = len(clips) * clip_duration
        actual = await probe_duration(partial, ffprobe=ffprobe)
        if abs(actual - expected) > _DURATION_TOLERANCE_SECONDS + 1 / fps:
            raise VideoAssemblyError(
                f"{output} would be {actual:.2f}s long; expected {expected:.2f}s."
            )
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    return output


async def _encode(
    clips: Sequence[Clip],
    output: Path,
    *,
    has_audio: Sequence[bool],
    clip_duration: Seconds,
    fade: Seconds,
    width: Pixels,
    height: Pixels,
    fps: int,
    font: Path | None,
    ffmpeg: str,
) -> None:
    """Run the ffmpeg filtergraph that joins ``clips`` into ``output``."""
    with tempfile.TemporaryDirectory(prefix="generative-ai-") as text_dir:
        filters: list[str] = []
        for index, (clip, audio) in enumerate(zip(clips, has_audio, strict=True)):
            video_chain = [
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                "setsar=1",
                f"fps={fps}",
                f"tpad=stop_mode=clone:stop_duration={clip_duration}",
                f"trim=duration={clip_duration}",
                "setpts=PTS-STARTPTS",
            ]
            if clip.caption is not None:
                video_chain.extend(
                    _caption_filters(clip.caption, Path(text_dir), index, height, font)
                )
            video_chain.extend(_fades("fade", clip_duration, fade))
            filters.append(f"[{index}:v]{','.join(video_chain)}[v{index}]")

            audio_source = (
                f"[{index}:a]aresample={_AUDIO_SAMPLE_RATE},apad"
                if audio
                else f"anullsrc=r={_AUDIO_SAMPLE_RATE}:cl=stereo"
            )
            audio_chain = [
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
                f"atrim=duration={clip_duration}",
                "asetpts=PTS-STARTPTS",
                *_fades("afade", clip_duration, fade),
            ]
            filters.append(f"{audio_source},{','.join(audio_chain)}[a{index}]")

        joined = "".join(f"[v{index}][a{index}]" for index in range(len(clips)))
        filters.append(f"{joined}concat=n={len(clips)}:v=1:a=1[v][a]")

        await _run(
            ffmpeg,
            "-hide_banner", "-loglevel", "error", "-y",
            *(arg for clip in clips for arg in ("-i", str(clip.path))),
            "-filter_complex", ";".join(filters),
            "-map", "[v]", "-map", "[a]",
            # concat drops the frame rate, so ffmpeg would otherwise use 25.
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(output),
        )  # fmt: skip


def _tool(explicit: str | None, name: str) -> str:
    found = explicit or shutil.which(name)
    if found is None:
        raise VideoAssemblyError(f"{name} was not found on PATH.")
    return found


async def _run(program: str, *args: str) -> str:
    """Run ``program`` and return its stdout, raising on a non-zero exit."""
    try:
        process = await asyncio.create_subprocess_exec(
            program,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise VideoAssemblyError(f"{program} could not be started.") from error
    try:
        stdout, stderr = await process.communicate()
    except BaseException:
        # Cancelled or interrupted: do not leave ffmpeg encoding unattended.
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode != 0:
        failure = VideoAssemblyError(
            f"{Path(program).name} exited with status {process.returncode}."
        )
        if message := stderr.decode(errors="replace").strip():
            failure.add_note(message)
        raise failure
    return stdout.decode(errors="replace")


async def _has_audio(video: Path, ffprobe: str) -> bool:
    output = await _run(
        ffprobe,
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(video),
    )  # fmt: skip
    return bool(output.strip())


def _fades(name: str, duration: Seconds, fade: Seconds) -> Iterable[str]:
    if fade <= 0:
        return ()
    return (
        f"{name}=t=in:st=0:d={fade}",
        f"{name}=t=out:st={duration - fade}:d={fade}",
    )


def _caption_filters(
    caption: Caption, text_dir: Path, index: int, height: Pixels, font: Path | None
) -> Iterable[str]:
    """``drawtext`` filters for a lower-third title with a fact line below it."""
    margin = height // 18
    gap = height // 24
    title_size = height // 20
    detail_size = height // 30
    lines = [(caption.title, title_size)]
    if caption.detail:
        lines.append((caption.detail, detail_size))

    # Bottom-up, so the last line sits on the bottom margin.
    bottom = height - margin
    filters: list[str] = []
    for line_index, (text, size) in reversed(list(enumerate(lines))):
        # Text goes through a file so it needs no filtergraph escaping.
        text_file = text_dir / f"caption-{index}-{line_index}.txt"
        text_file.write_text(text, encoding="utf-8")
        top = bottom - size
        options = [
            *((f"fontfile={_filter_path(font)}",) if font is not None else ()),
            f"textfile={_filter_path(text_file)}",
            "expansion=none",
            f"fontsize={size}",
            "fontcolor=white",
            "box=1",
            "boxcolor=black@0.55",
            f"boxborderw={size // 3}",
            f"x={margin}",
            f"y={top}",
        ]
        filters.append(f"drawtext={':'.join(options)}")
        bottom = top - gap
    return filters


def _filter_path(path: Path) -> str:
    """Quote ``path`` as a filter option value (``C:`` needs its colon escaped)."""
    escaped = path.resolve().as_posix().replace(":", r"\:").replace("'", r"'\''")
    return f"'{escaped}'"
