"""Offline checks that applied generation settings are verified, not assumed."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, override

import pytest

from generative_ai.googleflow import (
    GenerationMode,
    GenerationSettings,
    GenerationSettingsError,
    GoogleFlowProject,
)
from generative_ai.googleflow.constants import CONFIGS
from generative_ai.googleflow.project import _settings_mismatches

if TYPE_CHECKING:
    from web_page import WebPage

    from generative_ai.site import BrowserPage

_IMAGE_1_1 = GenerationSettings(
    mode=GenerationMode.IMAGE, aspect_ratio="1:1", model="Nano Banana Pro", outputs=1
)


def test_matching_settings_have_no_mismatches() -> None:
    assert _settings_mismatches(_IMAGE_1_1, _IMAGE_1_1, ()) == []


def test_unrequested_fields_are_ignored() -> None:
    shown = GenerationSettings(
        mode=GenerationMode.IMAGE, aspect_ratio="16:9", model="X", outputs=4
    )
    assert _settings_mismatches(GenerationSettings(outputs=4), shown, ()) == []


def test_reset_setting_is_reported() -> None:
    shown = GenerationSettings(
        mode=GenerationMode.IMAGE,
        aspect_ratio="16:9",
        model="Nano Banana Pro",
        outputs=1,
    )
    assert _settings_mismatches(_IMAGE_1_1, shown, ()) == [
        "aspect ratio '1:1' (Flow shows '16:9')"
    ]


def test_hidden_group_counts_when_the_summary_names_it() -> None:
    requested = GenerationSettings(resolution="720p", duration=8)
    shown = GenerationSettings(mode=GenerationMode.VIDEO)

    assert _settings_mismatches(requested, shown, ("Video", "720p", "8s", "x1")) == []
    assert _settings_mismatches(requested, shown, ("Video", "x1")) == [
        "resolution '720p' (Flow shows None)",
        "duration '8s' (Flow shows None)",
    ]


def test_model_matches_like_the_model_menu() -> None:
    shown = GenerationSettings(model="Veo 3.1  -  Fast")
    assert (
        _settings_mismatches(GenerationSettings(model="veo 3.1 - fast"), shown, ())
        == []
    )
    assert _settings_mismatches(
        GenerationSettings(model="Veo 3.1 - Quality"), shown, ()
    )


class _FakeProject(GoogleFlowProject):
    """Records selection rounds and reads back a scripted series of settings."""

    __slots__ = ("reads", "rounds")

    def __init__(self, reads: list[GenerationSettings]) -> None:
        super().__init__(
            "magic", cast("BrowserPage", object()), cast("WebPage", object())
        )
        self.reads = reads
        self.rounds = 0

    @override
    async def _select_settings(self, settings: GenerationSettings) -> None:
        self.rounds += 1

    @override
    async def _read_settings_and_summary(
        self,
    ) -> tuple[GenerationSettings, tuple[str, ...]]:
        return self.reads.pop(0), ()


async def test_apply_retries_a_reset_setting() -> None:
    reset = GenerationSettings(
        mode=GenerationMode.IMAGE,
        aspect_ratio="16:9",
        model="Nano Banana Pro",
        outputs=1,
    )
    project = _FakeProject([reset, _IMAGE_1_1])

    await project._apply_settings(_IMAGE_1_1)

    assert project.rounds == 2


async def test_apply_raises_when_flow_keeps_another_value() -> None:
    reset = GenerationSettings(
        mode=GenerationMode.IMAGE,
        aspect_ratio="16:9",
        model="Nano Banana Pro",
        outputs=1,
    )
    project = _FakeProject([reset] * CONFIGS.SETTINGS_APPLY_ATTEMPTS)

    with pytest.raises(GenerationSettingsError, match=r"aspect ratio '1:1'"):
        await project._apply_settings(_IMAGE_1_1)
    assert project.rounds == CONFIGS.SETTINGS_APPLY_ATTEMPTS


async def test_model_selected_before_model_dependent_options() -> None:
    class Web:
        async def run(self, *args: object) -> dict[str, object]:
            return {}

    class Project(GoogleFlowProject):
        def __init__(self) -> None:
            super().__init__(
                "magic", cast("BrowserPage", object()), cast("WebPage", Web())
            )
            self.model = "old-model"
            self.ratio = "16:9"

        @override
        async def _select_model(self, model: str) -> None:
            self.model = model

        @override
        async def _select_setting(self, field: str, label: str) -> None:
            if field == "Aspect ratio":
                if self.model != "new-model":
                    raise GenerationSettingsError("Old model does not offer 1:1")
                self.ratio = label

        @override
        async def _close_overlays(self) -> None:
            pass

    project = Project()
    await project._select_settings(
        GenerationSettings(model="new-model", aspect_ratio="1:1")
    )
    assert project.model == "new-model"
    assert project.ratio == "1:1"
