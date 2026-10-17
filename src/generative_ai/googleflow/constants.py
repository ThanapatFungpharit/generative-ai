from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from generative_ai.site import Site, register_site

if TYPE_CHECKING:
    from web_page.types import Milliseconds

    from generative_ai.site.types import Pixels, Seconds, Url

_MANIFESTS_DIR: Final = Path(__file__).parent / "manifests"


@dataclass(frozen=True, slots=True)
class _GoogleFlowUrls:
    base: Url = "https://flow.google.com/"
    project: Url = "https://flow.google.com/project/"
    # Finished media is served as ``{media_content}{image|video}/{media_id}``.
    media_content: Url = "https://flow-content.google/"


@dataclass(frozen=True, slots=True)
class _Configs:
    MENU_CLOSE_MAX_ATTEMPTS: int = 3
    MENU_CLOSE_RETRY_TIMEOUT_MS: Milliseconds = 300.0
    UPLOAD_TIMEOUT_SECONDS: Seconds = 30.0
    # Added per MiB uploaded, so large videos get time to reach Flow.
    UPLOAD_SECONDS_PER_MIB: Seconds = 1.0
    NAVIGATION_TIMEOUT_MS: Milliseconds = 30_000.0
    # A reload of the workspace plus its prompt editor; a recovery that times
    # out closes the page, so this must cover a slow but healthy load.
    RECOVERY_TIMEOUT_SECONDS: Seconds = 45.0
    POLL_INTERVAL_SECONDS: Seconds = 1.0
    # Consecutive grid snapshots a polling wait may lose to a page error
    # (e.g. Flow re-rendering) before it gives up.
    POLL_SNAPSHOT_ATTEMPTS: int = 5
    GENERATION_START_TIMEOUT_SECONDS: Seconds = 30.0
    GENERATION_FAILURE_GRACE_SECONDS: Seconds = 5.0
    # Rounds of choosing the settings and reading them back; a later choice
    # (e.g. the model) can reset an earlier one, which a second round fixes.
    SETTINGS_APPLY_ATTEMPTS: int = 2
    # How long finished uploads may stay unnamed before they are matched by
    # elimination instead of by title.
    UPLOAD_SETTLE_SECONDS: Seconds = 3.0
    # How long a binned item may take to be listed in the bin.
    BIN_LISTING_TIMEOUT_SECONDS: Seconds = 60.0
    IMAGE_GENERATION_TIMEOUT_SECONDS: Seconds = 180.0
    VIDEO_GENERATION_TIMEOUT_SECONDS: Seconds = 900.0
    # Flow's layout shifts with the viewport (narrower ones switch to a mobile
    # layout with different controls), so every page is pinned to this size.
    VIEWPORT_WIDTH: Pixels = 1280
    VIEWPORT_HEIGHT: Pixels = 720


URLS: Final = _GoogleFlowUrls()
CONFIGS: Final = _Configs()
SITE: Final = register_site(
    Site(
        name="googleflow",
        base_url=URLS.base,
        manifests_dir=_MANIFESTS_DIR,
        navigation_timeout_ms=CONFIGS.NAVIGATION_TIMEOUT_MS,
        viewport={"width": CONFIGS.VIEWPORT_WIDTH, "height": CONFIGS.VIEWPORT_HEIGHT},
        keep_active=True,
    )
)
SELECTORS: Final = SITE.load_selectors()
