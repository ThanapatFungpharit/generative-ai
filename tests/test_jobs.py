"""Generation attribution without relying on counts or submission order."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, cast, override

import pytest

from generative_ai.googleflow import (
    GenerationError,
    GenerationJob,
    GoogleFlowProject,
    MediaItem,
)
from generative_ai.googleflow import project as project_module
from generative_ai.googleflow.constants import CONFIGS
from generative_ai.googleflow.project import _job_for_prompt, _JobState, _Tile

if TYPE_CHECKING:
    from web_page import WebPage

    from generative_ai.site import BrowserPage


class _Page:
    def is_closed(self) -> bool:
        return False


def _tile(media_id: str | None, *, pending: bool = False, kind: str = "image") -> _Tile:
    return _Tile(MediaItem("", kind, media_id, pending), ())


class _Project(GoogleFlowProject):
    def __init__(
        self, snapshots: list[tuple[_Tile, ...]], prompts: dict[str, str]
    ) -> None:
        super().__init__(
            "magic", cast("BrowserPage", _Page()), cast("WebPage", object())
        )
        self.snapshots = snapshots
        self.prompts = prompts
        self.current: tuple[_Tile, ...] = ()
        self.opened: list[str] = []

    @override
    async def _ensure_on_workspace(self) -> None:
        pass

    @override
    async def _poll_media(self) -> tuple[_Tile, ...]:
        self.current = self.snapshots[0]
        if len(self.snapshots) > 1:
            self.snapshots.pop(0)
        return self.current

    @override
    async def _open_tile(self, index: int) -> tuple[str, str]:
        media_id = self.current[index].item.media_id or "opaque-existing-id"
        self.opened.append(media_id)
        return media_id, self.prompts.get(media_id, "")

    @override
    async def _snapshot_media(self) -> tuple[_Tile, ...]:
        return await self._poll_media()

    @override
    async def _note_flow_message(self, error: BaseException) -> None:
        pass


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        project_module,
        "CONFIGS",
        dataclasses.replace(
            CONFIGS, POLL_INTERVAL_SECONDS=0, GENERATION_FAILURE_GRACE_SECONDS=0
        ),
    )


async def test_capped_grid_collects_new_identity() -> None:
    project = _Project([(_tile("new"), _tile("old-1"))], {"new": "a cat"})
    job = GenerationJob("a cat", "image", 1)
    project._jobs[job] = _JobState(frozenset({"old-1", "old-2"}))

    assert await project.wait_for_generations([job]) == {job: ("new",)}
    assert project.opened == ["new"]


async def test_unrelated_upload_is_not_a_generation_result() -> None:
    project = _Project([(_tile("upload"), _tile("generated"))], {"generated": "a cat"})
    job = GenerationJob("a cat", "image", 1)
    project._jobs[job] = _JobState(frozenset())

    assert await project.wait_for_generations([job]) == {job: ("generated",)}


async def test_duplicate_wait_inputs_return_one_result() -> None:
    project = _Project([], {})
    job = GenerationJob("a cat", "image", 1)
    project._jobs[job] = _JobState(frozenset(), outputs=("generated",))

    assert await project.wait_for_generations([job, job]) == {job: ("generated",)}
    assert job not in project._jobs


async def test_same_prompt_rejected_before_any_ui_changes() -> None:
    project = _Project([], {})
    job = GenerationJob("a cat", "image", 1)
    project._jobs[job] = _JobState(frozenset())

    with pytest.raises(GenerationError, match="same prompt"):
        await project.start_image("  a\ncat ", references=["other-reference"])
    assert not project.opened


def test_ambiguous_prompt_is_never_assigned_arbitrarily() -> None:
    jobs = [GenerationJob("a cat", "image", 1) for _ in range(2)]
    with pytest.raises(GenerationError, match="ambiguous"):
        _job_for_prompt("a cat", {job: [] for job in jobs})


def test_prompt_substrings_do_not_match_unrelated_outputs() -> None:
    job = GenerationJob("a cat", "image", 1)
    assert _job_for_prompt("a cat in a different scene", {job: []}) is None


async def test_outputs_retained_across_virtualized_snapshots() -> None:
    project = _Project(
        [(_tile("first"), _tile(None, pending=True)), (_tile("second"),)],
        {"first": "a cat", "second": "a cat"},
    )
    job = GenerationJob("a cat", "image", 2)
    project._jobs[job] = _JobState(frozenset())

    assert await project.wait_for_generations([job]) == {job: ("second", "first")}


async def test_timed_out_wait_keeps_already_matched_outputs() -> None:
    project = _Project(
        [(_tile("first"), _tile(None, pending=True))], {"first": "a cat"}
    )
    job = GenerationJob("a cat", "image", 2)
    project._jobs[job] = _JobState(frozenset())
    with pytest.raises(GenerationError, match="in time"):
        await project.wait_for_generations([job], timeout=0)
    project.snapshots = [(_tile("second"),)]
    project.prompts["second"] = "a cat"

    assert await project.wait_for_generations([job]) == {job: ("second", "first")}


async def test_baseline_resolves_tiles_without_exposed_ids() -> None:
    project = _Project([(_tile(None),)], {})
    assert await project._generation_baseline("image") == frozenset(
        {"opaque-existing-id"}
    )


async def test_invalid_wait_input_does_not_consume_valid_result() -> None:
    project = _Project([], {})
    valid, invalid = (GenerationJob("a cat", "image", 1) for _ in range(2))
    project._jobs[valid] = _JobState(frozenset(), outputs=("generated",))
    with pytest.raises(ValueError):
        await project.wait_for_generations([valid, invalid])
    assert await project.wait_for_generations([valid]) == {valid: ("generated",)}


async def test_subset_collection_retains_the_other_jobs_result() -> None:
    project = _Project(
        [(_tile("second"), _tile("first"), _tile("old"))],
        {"first": "a cat", "second": "a dog"},
    )
    first = GenerationJob("a cat", "image", 1)
    second = GenerationJob("a dog", "image", 1)
    project._jobs[first] = _JobState(frozenset({"old"}))
    # The first output finished before the second job was submitted.
    project._jobs[second] = _JobState(frozenset({"old", "first"}))

    assert await project.wait_for_generations([first]) == {first: ("first",)}
    project.snapshots = []  # The cached second result needs no further polling.
    assert await project.wait_for_generations([second]) == {second: ("second",)}


async def test_one_snapshot_preserves_newest_first_order() -> None:
    project = _Project(
        [(_tile("newer"), _tile("older"))],
        {"newer": "a cat", "older": "a cat"},
    )
    job = GenerationJob("a cat", "image", 2)
    project._jobs[job] = _JobState(frozenset())

    assert await project.wait_for_generations([job]) == {job: ("newer", "older")}


async def test_opaque_video_lookup_uses_the_editor_alias() -> None:
    project = _Project([(_tile(None, kind="video"),)], {})
    project._media_editor_ids["content-id"] = "opaque-existing-id"

    assert await project._find_media_tile("content-id") == 0


async def test_video_baseline_remembers_both_identity_forms() -> None:
    project = _Project([(_tile("content-id", kind="video"),)], {})
    project._media_editor_ids["content-id"] = "editor-id"

    assert await project._generation_baseline("video") == frozenset(
        {"content-id", "editor-id"}
    )
