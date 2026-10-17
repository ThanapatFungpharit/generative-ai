"""Offline checks of assemble_video's failure handling. Needs ffmpeg on PATH."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from generative_ai import video
from generative_ai.video import Clip, VideoAssemblyError, assemble_video

if TYPE_CHECKING:
    from pathlib import Path

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg and ffprobe on PATH",
)


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("clips") / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=1",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
async def test_assembles_without_leaving_a_partial_file(
    clip: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out.mp4"

    await assemble_video(
        [Clip(clip), Clip(clip)], output, clip_duration=1.0, width=320, height=180
    )

    assert output.is_file()
    assert [path.name for path in tmp_path.iterdir()] == ["out.mp4"]


@needs_ffmpeg
async def test_failure_keeps_the_previous_output(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"
    output.write_bytes(b"previous")

    with pytest.raises(VideoAssemblyError):
        await assemble_video(
            [Clip(tmp_path / "missing.mp4")], output, clip_duration=1.0
        )

    assert output.read_bytes() == b"previous"
    assert [path.name for path in tmp_path.iterdir()] == ["out.mp4"]


@needs_ffmpeg
async def test_encode_failure_preserves_a_preexisting_partial(
    clip: Path, tmp_path: Path
) -> None:
    existing = tmp_path / "out.partial.mp4"
    existing.write_bytes(b"user data")
    output = tmp_path / "out.mp4"
    output.write_bytes(b"previous output")

    with pytest.raises(VideoAssemblyError):
        # yuv420p cannot encode these odd dimensions. Fail after probing and
        # creating our temporary file, so this exercises encode cleanup.
        await assemble_video(
            [Clip(clip)], output, clip_duration=1, width=321, height=181
        )

    assert existing.read_bytes() == b"user data"
    assert output.read_bytes() == b"previous output"
    assert {path.name for path in tmp_path.iterdir()} == {"out.partial.mp4", "out.mp4"}


@needs_ffmpeg
async def test_concurrent_assemblies_do_not_share_temporary_files(
    clip: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out.mp4"
    results = await asyncio.gather(
        *(
            assemble_video([Clip(clip)], output, clip_duration=1, width=320, height=180)
            for _ in range(2)
        )
    )
    assert results == [output, output]
    assert abs(await video.probe_duration(output) - 1) < 0.1
    assert [path.name for path in tmp_path.iterdir()] == ["out.mp4"]


async def test_cancelling_a_run_kills_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[asyncio.subprocess.Process] = []
    create = asyncio.create_subprocess_exec

    async def recording_create(
        *args: str, **kwargs: object
    ) -> asyncio.subprocess.Process:
        process = await create(*args, **kwargs)  # ty: ignore[invalid-argument-type]
        started.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_create)
    task = asyncio.create_task(
        video._run(sys.executable, "-c", "import time; time.sleep(60)")
    )
    while not started:
        await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert started[0].returncode is not None
