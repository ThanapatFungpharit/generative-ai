from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from yaml import safe_load

from generative_ai.googleflow.guards import is_str_dict, is_str_keyed_mapping

if TYPE_CHECKING:
    from web_page.types import CssSelector, SelectorKey

    from generative_ai.googleflow.types import (
        Milliseconds,
        Pixels,
        RawYaml,
        Seconds,
        Url,
    )

_MANIFESTS_DIR: Final = Path(__file__).parent / "manifests"


@dataclass(frozen=True, slots=True)
class _GoogleFlowUrls:
    base: Url = "https://flow.google.com/"
    project: Url = "https://flow.google.com/project/"
    # Finished media is served as ``{media_content}{image|video}/{media_id}``.
    media_content: Url = "https://flow-content.google/"


@dataclass(frozen=True, slots=True)
class _Manifests:
    selectors: Path = _MANIFESTS_DIR / "selectors.yaml"
    actions: tuple[Path, ...] = field(
        default_factory=lambda: tuple(
            sorted((_MANIFESTS_DIR / "actions").glob("*.yaml"))
        )
    )


@dataclass(frozen=True, slots=True)
class _Configs:
    MENU_CLOSE_MAX_ATTEMPTS: int = 3
    MENU_CLOSE_RETRY_TIMEOUT_MS: Milliseconds = 300.0
    UPLOAD_TIMEOUT_SECONDS: Seconds = 30.0
    NAVIGATION_TIMEOUT_MS: Milliseconds = 30_000.0
    RECOVERY_TIMEOUT_SECONDS: Seconds = 5.0
    CLEANUP_TIMEOUT_SECONDS: Seconds = 2.0
    POLL_INTERVAL_SECONDS: Seconds = 1.0
    GENERATION_START_TIMEOUT_SECONDS: Seconds = 30.0
    GENERATION_FAILURE_GRACE_SECONDS: Seconds = 5.0
    # How long finished uploads may stay unnamed before they are matched by
    # elimination instead of by title.
    UPLOAD_SETTLE_SECONDS: Seconds = 3.0
    IMAGE_GENERATION_TIMEOUT_SECONDS: Seconds = 180.0
    VIDEO_GENERATION_TIMEOUT_SECONDS: Seconds = 900.0
    # Narrower viewports switch Flow to a mobile layout with different controls.
    MIN_VIEWPORT_WIDTH: Pixels = 1280
    MIN_VIEWPORT_HEIGHT: Pixels = 720


def _load_selectors(path: Path) -> Mapping[SelectorKey, CssSelector]:
    """Read the selectors manifest so Python-side locators share one source."""
    manifest: RawYaml = safe_load(path.read_text(encoding="utf-8"))
    selectors: RawYaml = (
        manifest.get("selectors") if is_str_keyed_mapping(manifest) else None
    )
    if not is_str_dict(selectors):
        msg = f"{path} must map 'selectors' to a mapping of strings to strings."
        raise TypeError(msg)
    return MappingProxyType(selectors)


URLS: Final = _GoogleFlowUrls()
MANIFESTS: Final = _Manifests()
SELECTORS: Final = _load_selectors(MANIFESTS.selectors)
CONFIGS: Final = _Configs()
