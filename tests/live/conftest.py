"""Fixtures for the live suite: one signed-in browser and throwaway projects.

Every project a test creates is deleted afterwards. If a deletion fails, the
project's URL is printed in a warning so it can be removed by hand.
"""

from __future__ import annotations

import struct
import warnings
import zlib
from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio
from cloakbrowser import launch_persistent_context_async
from playwright.async_api import Error as PlaywrightError

from generative_ai.googleflow import GoogleFlowError, GoogleFlowProject
from generative_ai.googleflow.constants import SELECTORS, URLS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from playwright.async_api import BrowserContext

_SIGN_IN_TIMEOUT_MS = 20_000.0


@pytest_asyncio.fixture(scope="session")
async def context(pytestconfig: pytest.Config) -> AsyncIterator[BrowserContext]:
    """A persistent Chromium context whose profile is signed in to Flow."""
    profile = cast("Path", pytestconfig.getoption("flow_profile"))
    if not profile.is_dir():
        pytest.fail(
            f"Browser profile {profile} does not exist. Sign in once with "
            "`uv run python -m tests.live.sign_in`.",
            pytrace=False,
        )
    context = cast(
        "BrowserContext",
        await launch_persistent_context_async(
            profile, headless=pytestconfig.getoption("headless")
        ),
    )
    try:
        await _require_signed_in(context, profile)
        yield context
    finally:
        await context.close()


async def _require_signed_in(context: BrowserContext, profile: Path) -> None:
    """Fail fast, instead of timing out in every test, when signed out."""
    page = await context.new_page()
    try:
        await page.goto(URLS.base)
        await page.locator(SELECTORS["new_project_button"]).wait_for(
            timeout=_SIGN_IN_TIMEOUT_MS
        )
    except PlaywrightError:
        pytest.fail(
            f"Profile {profile} is not signed in to Google Flow (no "
            f"'New project' button at {URLS.base}). Sign in with "
            "`uv run python -m tests.live.sign_in`.",
            pytrace=False,
        )
    finally:
        await page.close()


async def discard(project: GoogleFlowProject) -> None:
    """Delete a test project, warning rather than failing if Flow refuses."""
    if project.is_closed:
        return
    try:
        await project.delete()
    except GoogleFlowError as error:
        details = "\n".join(getattr(error, "__notes__", ()))
        if error.__cause__ is not None:
            details += f"\nCaused by: {error.__cause__}"
        warnings.warn(
            f"Could not delete test project {project.project_url}: {error}\n{details}",
            stacklevel=1,
        )


@pytest_asyncio.fixture
async def project(context: BrowserContext) -> AsyncIterator[GoogleFlowProject]:
    """A fresh project for one test."""
    project = await GoogleFlowProject.new(context)
    try:
        yield project
    finally:
        await discard(project)


@pytest_asyncio.fixture(scope="module")
async def shared_project(context: BrowserContext) -> AsyncIterator[GoogleFlowProject]:
    """One project shared by a module's tests, which run in file order."""
    project = await GoogleFlowProject.new(context)
    try:
        yield project
    finally:
        await discard(project)


@pytest.fixture(scope="session")
def sample_png(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small solid-colour PNG, written without an imaging library."""
    path = tmp_path_factory.mktemp("media") / "sample.png"
    path.write_bytes(_solid_png(256, 256, (220, 60, 40)))
    return path


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    # Each scanline starts with filter type 0 (None).
    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )
