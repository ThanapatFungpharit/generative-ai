"""Prompt-box settings and image generation against the real Google Flow site.

Settings tests spend no credits. Generation tests are also marked
``credits`` and only run with ``--live-credits``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from generative_ai.googleflow import (
    GenerationError,
    GenerationMode,
    GenerationSettings,
    GenerationSettingsError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from generative_ai.googleflow import GoogleFlowProject

pytestmark = pytest.mark.live

_PROMPT = "a single red apple on a plain white table, studio lighting"
_IMAGE_MODEL = "Nano Banana 2.1"
# Always name the allowed video model. Never inherit an account's last model.
_VIDEO_MODEL = "Veo 3.1 - Fast"


async def test_read_generation_settings(shared_project: GoogleFlowProject) -> None:
    settings = await shared_project.get_generation_settings()

    assert settings.mode in set(GenerationMode)
    assert settings.model is not None and settings.model != ""
    assert settings.outputs is not None
    assert 1 <= settings.outputs <= 4


async def test_configure_image_mode_round_trips(
    shared_project: GoogleFlowProject,
) -> None:
    await shared_project.configure(
        GenerationSettings(mode=GenerationMode.IMAGE, outputs=1)
    )

    settings = await shared_project.get_generation_settings()
    assert settings.mode is GenerationMode.IMAGE
    assert settings.outputs == 1


async def test_configure_rejects_unoffered_aspect_ratio(
    shared_project: GoogleFlowProject,
) -> None:
    with pytest.raises(GenerationSettingsError, match="available options"):
        await shared_project.configure(
            GenerationSettings(mode=GenerationMode.IMAGE, aspect_ratio="7:3")
        )

    # The popover was closed, so the project is still usable.
    await shared_project.get_generation_settings()


async def test_toggle_agent_mode(shared_project: GoogleFlowProject) -> None:
    await shared_project.set_agent_mode(True)
    await shared_project.set_agent_mode(False)


@pytest.mark.credits
async def test_generate_and_download_image(
    shared_project: GoogleFlowProject, tmp_path: Path
) -> None:
    media_ids = await shared_project.generate_image(
        _PROMPT, model=_IMAGE_MODEL, outputs=1
    )

    assert len(media_ids) == 1
    listed = {item.media_id: item for item in await shared_project.list_media()}
    assert listed[media_ids[0]].kind == "image"

    assert await shared_project.reuse_prompt(media_ids[0]) == _PROMPT

    saved = await shared_project.download_media(media_ids[0], tmp_path)
    assert saved.is_file()
    assert saved.stat().st_size > 0


@pytest.mark.credits
async def test_concurrent_image_jobs_map_to_their_prompts(
    shared_project: GoogleFlowProject,
) -> None:
    prompts = ("a blue cube on grass", "a yellow sphere on sand")
    jobs = [
        await shared_project.start_image(p, model=_IMAGE_MODEL, outputs=1)
        for p in prompts
    ]

    results = await shared_project.wait_for_generations(jobs)

    assert results.keys() == set(jobs)
    ids = [media_id for job in jobs for media_id in results[job]]
    assert len(ids) == len(set(ids)) == len(prompts)
    for job in jobs:
        assert await shared_project.reuse_prompt(results[job][0]) == job.prompt


@pytest.mark.credits
async def test_upload_does_not_contaminate_a_generation(
    shared_project: GoogleFlowProject, sample_png: Path
) -> None:
    prompt = "a green pyramid on a white tabletop, no text"
    job = await shared_project.start_image(prompt, model=_IMAGE_MODEL, outputs=1)
    with pytest.raises(GenerationError, match="same prompt"):
        await shared_project.start_image(prompt, model=_IMAGE_MODEL, outputs=1)

    uploaded_id = (await shared_project.upload_media([sample_png]))[sample_png]
    results = await shared_project.wait_for_generations([job, job])

    assert len(results[job]) == 1
    assert uploaded_id not in results[job]
    assert await shared_project.reuse_prompt(results[job][0]) == prompt


@pytest.mark.credits
async def test_generate_fast_video_and_restore(
    shared_project: GoogleFlowProject, sample_png: Path, tmp_path: Path
) -> None:
    from tests.live.test_media import _bin_and_restore

    frame = (await shared_project.upload_media([sample_png]))[sample_png]
    prompt = "A static camera views a solid red backdrop with a gentle light shift"
    videos = await shared_project.generate_video(
        prompt,
        model=_VIDEO_MODEL,
        start_frame=frame,
        aspect_ratio="16:9",
        outputs=1,
        resolution="720p",
        duration=8,
    )
    assert len(videos) == 1
    saved = await shared_project.download_media(videos[0], tmp_path, quality="720p")
    assert saved.is_file() and saved.stat().st_size > 0
    await _bin_and_restore(shared_project, videos[0])
    assert await shared_project.reuse_prompt(videos[0]) == prompt
