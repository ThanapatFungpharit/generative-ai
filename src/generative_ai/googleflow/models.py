from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from generative_ai.googleflow.types import (
        AspectRatio,
        MediaId,
        MediaKind,
        ModelLabel,
        OutputCount,
        OutputKind,
        PromptText,
        VideoDuration,
        VideoResolution,
    )


class GenerationMode(StrEnum):
    """The top-level output type, named as Flow labels it."""

    IMAGE = "Image"
    VIDEO = "Video"


class VideoInput(StrEnum):
    """How a video prompt is conditioned, named as Flow labels it."""

    FRAMES = "Frames"
    INGREDIENTS = "Ingredients"


@dataclass(frozen=True, slots=True)
class GenerationSettings:
    """Prompt-box settings. ``None`` leaves the current Flow value unchanged.

    ``aspect_ratio``, ``model`` and ``resolution`` are Flow's own labels,
    such as ``"9:16"``, ``"Veo 3.1 - Fast"`` or ``"720p"``, and ``duration``
    is in seconds, such as ``8``.
    The options offered depend on ``mode``, the model and the account, so
    they are validated against the live UI, not here.
    """

    mode: GenerationMode | None = None
    video_input: VideoInput | None = None
    aspect_ratio: AspectRatio | None = None
    model: ModelLabel | None = None
    resolution: VideoResolution | None = None
    duration: VideoDuration | None = None
    outputs: OutputCount | None = None

    def __post_init__(self) -> None:
        if self.outputs is not None and not 1 <= self.outputs <= 4:
            raise ValueError("outputs must be between 1 and 4")
        if self.duration is not None and self.duration < 1:
            raise ValueError("duration must be a positive number of seconds")
        if self.mode is GenerationMode.IMAGE:
            for name in ("video_input", "resolution", "duration"):
                if getattr(self, name) is not None:
                    raise ValueError(f"{name} only applies to video generation")


@dataclass(frozen=True, slots=True)
class MediaItem:
    """One tile in the project's media grid.

    ``kind`` comes from Flow's tile component, e.g. ``"image"``, ``"video"``,
    ``"scene"`` or ``"collection"``. ``media_id`` is ``None`` for tiles that
    do not expose one, such as scenes, collections and pending generations.
    """

    title: str
    kind: MediaKind
    media_id: MediaId | None
    pending: bool


@dataclass(frozen=True, slots=True, eq=False)
class GenerationJob:
    """A generation submitted with ``start_image`` or ``start_video``.

    Pass it to ``GoogleFlowProject.wait_for_generations`` to get its media
    IDs. Jobs compare by identity, so two jobs with one prompt stay distinct.
    """

    prompt: PromptText
    kind: OutputKind
    outputs: OutputCount
