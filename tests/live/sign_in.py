"""Open a headed browser on the test profile so you can sign in to Google.

Usage: ``uv run python -m tests.live.sign_in [profile_dir]``. Sign in, wait
for Flow's projects page, then close the browser window.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from generative_ai.googleflow.constants import URLS
from tests.conftest import DEFAULT_CHANNEL, DEFAULT_PROFILE


async def main(profile: Path) -> None:
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            profile, channel=DEFAULT_CHANNEL, headless=False
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(URLS.base)
        print(f"Sign in to Google in the browser, then close it. Profile: {profile}")
        await context.wait_for_event("close", timeout=0)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROFILE))
