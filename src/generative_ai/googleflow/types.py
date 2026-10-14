"""Semantic aliases for the Google Flow domain."""

from __future__ import annotations

from typing import Literal

# Flow identifiers
type ProjectMagic = str
"""The stable project identifier in a Flow URL: ``.../project/{magic}``."""
type MediaId = str
"""Flow's ID for a hosted media item; it is interpolated into CSS selectors."""
type SceneId = str
type UploadToken = str
"""Random hex embedded in an upload's file name, to find the tile it becomes."""

# Media
type MediaKind = str
"""A tile's component kind, e.g. ``image``, ``video``, ``scene`` or ``unknown``;
for an upload, the major part of its MIME type."""
type OutputKind = Literal["image", "video"]
"""The kind of tile a generation produces."""
type TileLabel = str
"""Any label-like text a tile exposes (aria-label, title, alt, inner text)."""
type QualityLabel = str
"""Leading label of a Download submenu entry, e.g. ``1K``."""
type MediaMenuItem = Literal[
    "Add to prompt", "Reuse prompt", "Favorite", "Set project cover", "Move to trash"
]
"""An entry of a media tile's context menu, as Flow's en-US locale labels it."""
type MediaAction = str
"""Verb phrase naming a media operation in error messages, e.g. ``favourite``."""

# Generation
type AspectRatio = str
"""Flow's aspect-ratio label, e.g. ``9:16``."""
type ModelLabel = str
"""Flow's model label, e.g. ``Veo 3.1 - Fast``."""
type VideoResolution = str
"""Flow's video-resolution label, e.g. ``720p``."""
type VideoDuration = int
"""A video's length in whole seconds; Flow labels 8 as ``8s``."""
type OutputCount = int
"""How many outputs one generation produces, from 1 to 4."""
type SettingLabel = str
"""The visible label of a toggle in the generation settings popover."""
type SettingField = Literal[
    "Mode",
    "Video type",
    "Aspect ratio",
    "Video resolution",
    "Video duration",
    "Output count",
]
"""A group of toggles in the settings popover, by its ``aria-label``."""
type FrameSlot = Literal["Start", "End"]
type PromptText = str
