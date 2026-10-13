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

    ``aspect_ratio`` and ``model`` are Flow's own labels, such as ``"9:16"``
    or ``"Veo 3.1 - Fast"``; the options offered depend on ``mode`` and on
    the account, so they are validated against the live UI, not here.
    """

    mode: GenerationMode | None = None
    video_input: VideoInput | None = None
    aspect_ratio: AspectRatio | None = None
    model: ModelLabel | None = None
    outputs: OutputCount | None = None

    def __post_init__(self) -> None:
        if self.outputs is not None and not 1 <= self.outputs <= 4:
            raise ValueError("outputs must be between 1 and 4")
        if self.video_input is not None and self.mode is GenerationMode.IMAGE:
            raise ValueError("video_input only applies to video generation")


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
