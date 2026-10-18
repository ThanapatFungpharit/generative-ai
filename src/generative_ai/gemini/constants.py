from __future__ import annotations

from pathlib import Path
from typing import Final

from generative_ai.site import Site, register_site

BASE_URL: Final = "https://gemini.google.com/app"
RESPONSE_TIMEOUT_SECONDS: Final = 180.0
POLL_INTERVAL_SECONDS: Final = 0.25

SITE: Final = register_site(
    Site(
        name="gemini",
        base_url=BASE_URL,
        manifests_dir=Path(__file__).parent / "manifests",
        keep_active=True,
    )
)
SELECTORS: Final = SITE.load_selectors()
