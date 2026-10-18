"""Open a headed browser on the test profile so you can sign in to Google.

Usage: ``uv run python -m tests.live.sign_in [profile_dir] [--site gemini]``.
Sign in, wait for the workspace, then close the browser window.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from cloakbrowser import launch_persistent_context_async

from generative_ai.gemini.constants import BASE_URL
from generative_ai.googleflow.constants import URLS
from tests.conftest import DEFAULT_GEMINI_PROFILE, DEFAULT_PROFILE


async def main(profile: Path, url: str = URLS.base) -> None:
    context = await launch_persistent_context_async(
        profile, headless=False, humanize=True
    )
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(url)
    print(f"Sign in to Google in the browser, then close it. Profile: {profile}")
    await context.wait_for_event("close", timeout=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path, nargs="?")
    parser.add_argument(
        "--site", choices=("googleflow", "gemini"), default="googleflow"
    )
    args = parser.parse_args()
    gemini = args.site == "gemini"
    profile = args.profile or (DEFAULT_GEMINI_PROFILE if gemini else DEFAULT_PROFILE)
    asyncio.run(main(profile, BASE_URL if gemini else URLS.base))
