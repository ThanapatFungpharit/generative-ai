"""Opt-in switches for tests that drive real generative AI websites."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_PROFILE = Path(os.environ.get("GOOGLE_FLOW_PROFILE", "google-profile"))
DEFAULT_GEMINI_PROFILE = Path(os.environ.get("GOOGLE_GEMINI_PROFILE", "gemini-profile"))


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("live", "Live website tests")
    group.addoption(
        "--live",
        action="store_true",
        help="Run tests marked 'live' against the real Flow and Gemini websites.",
    )
    group.addoption(
        "--gemini-profile",
        type=Path,
        default=DEFAULT_GEMINI_PROFILE,
        help="CloakBrowser profile signed in to Gemini "
        "(default: $GOOGLE_GEMINI_PROFILE or ./gemini-profile).",
    )
    group.addoption(
        "--live-credits",
        action="store_true",
        help="Also run tests marked 'credits', which spend generation credits "
        "(implies --live).",
    )
    group.addoption(
        "--flow-profile",
        type=Path,
        default=DEFAULT_PROFILE,
        help="CloakBrowser profile directory signed in to Google "
        "(default: $GOOGLE_FLOW_PROFILE or ./google-profile).",
    )
    group.addoption(
        "--headless",
        action="store_true",
        help="Run Chromium headless. Google may challenge headless sign-ins, "
        "so a headed browser is the default.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: Iterable[pytest.Item]
) -> None:
    credits = config.getoption("live_credits")
    live = credits or config.getoption("live")
    skip_live = pytest.mark.skip(reason="live test: pass --live to run")
    skip_credits = pytest.mark.skip(reason="spends credits: pass --live-credits")
    for item in items:
        if "credits" in item.keywords and not credits:
            item.add_marker(skip_credits)
        elif "live" in item.keywords and not live:
            item.add_marker(skip_live)
