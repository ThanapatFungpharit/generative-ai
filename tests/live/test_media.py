"""Uploaded-media operations against the real Google Flow site.

The tests share one project and one upload, and run in file order: later
tests act on the media ID the upload test stores. Spends no credits.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from generative_ai.googleflow import MediaNotFoundError

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from generative_ai.googleflow import GoogleFlowProject
    from generative_ai.googleflow.types import MediaId

pytestmark = pytest.mark.live

# Flow lists a restored item in the grid again only after a delay.
_REAPPEAR_TIMEOUT_SECONDS = 60.0

_uploaded: dict[str, MediaId] = {}


def _media_id() -> MediaId:
    if "image" not in _uploaded:
        pytest.skip("depends on test_upload_media, which did not pass")
    return _uploaded["image"]


async def test_upload_media(
    shared_project: GoogleFlowProject, sample_png: Path
) -> None:
    media_ids = await shared_project.upload_media([sample_png])

    assert media_ids.keys() == {sample_png}
    assert media_ids[sample_png]
    _uploaded["image"] = media_ids[sample_png]


async def test_upload_nothing_is_a_no_op(shared_project: GoogleFlowProject) -> None:
    assert await shared_project.upload_media([]) == {}


async def test_list_media_includes_upload(shared_project: GoogleFlowProject) -> None:
    media_id = _media_id()

    items = {item.media_id: item for item in await shared_project.list_media()}

    assert media_id in items
    assert items[media_id].kind == "image"
    assert not items[media_id].pending


async def test_download_upload(
    shared_project: GoogleFlowProject, tmp_path: Path
) -> None:
    saved = await shared_project.download_media(_media_id(), tmp_path)

    assert saved.parent == tmp_path
    assert saved.is_file()
    assert saved.stat().st_size > 0


async def test_toggle_favourite_twice(shared_project: GoogleFlowProject) -> None:
    media_id = _media_id()

    await shared_project.toggle_favourite(media_id)
    await shared_project.toggle_favourite(media_id)


async def test_set_project_cover(shared_project: GoogleFlowProject) -> None:
    await shared_project.set_project_cover(_media_id())


async def test_move_to_bin_and_restore(
    shared_project: GoogleFlowProject, sample_png: Path
) -> None:
    # A fresh upload, so a failure here leaves the shared one in place.
    media_id = (await shared_project.upload_media([sample_png]))[sample_png]

    await _bin_and_restore(shared_project, media_id)


async def test_unknown_media_raises_not_found(
    shared_project: GoogleFlowProject, tmp_path: Path
) -> None:
    unknown = f"pytest-{uuid4().hex}"

    with pytest.raises(MediaNotFoundError):
        await shared_project.toggle_favourite(unknown)
    with pytest.raises(MediaNotFoundError):
        await shared_project.download_media(unknown, tmp_path)


async def test_create_scene(shared_project: GoogleFlowProject) -> None:
    scene_id = await shared_project.create_scene()

    assert scene_id
    # create_scene returns to the workspace.
    page_url = shared_project._require_page().url.rstrip("/")
    assert page_url == shared_project.project_url


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_uploaded_video_survives_reload_and_bin(
    shared_project: GoogleFlowProject, tmp_path: Path
) -> None:
    video = tmp_path / "sample.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=320x180:r=24:d=1",
            "-pix_fmt", "yuv420p", str(video),
        ],
        check=True,
    )  # fmt: skip
    media_id = (await shared_project.upload_media([video]))[video]
    await shared_project._return_to_workspace()

    await _bin_and_restore(shared_project, media_id)


async def _bin_and_restore(project: GoogleFlowProject, media_id: MediaId) -> None:
    await project.move_to_bin(media_id)
    assert not await _contains_media(project, media_id)

    await project.restore_from_bin(media_id)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _REAPPEAR_TIMEOUT_SECONDS
    while not await _contains_media(project, media_id):
        if loop.time() >= deadline:
            pytest.fail(f"Restored media {media_id} did not reappear in the grid.")
        await asyncio.sleep(5)
        # The grid does not pick the restored item up until it is reloaded.
        page = cast("Page", project._require_page())
        await page.reload()


async def _contains_media(project: GoogleFlowProject, media_id: MediaId) -> bool:
    if media_id in await _listed_ids(project):
        return True
    # Reloaded video tiles have opaque thumbnails and expose no content ID.
    return await project._find_media_tile(media_id) is not None


async def _listed_ids(project: GoogleFlowProject) -> set[MediaId | None]:
    return {item.media_id for item in await project.list_media()}
