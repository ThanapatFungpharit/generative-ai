"""Project lifecycle against the real Google Flow site. Spends no credits."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from generative_ai.googleflow import (
    GoogleFlowProject,
    ProjectClosedError,
    ProjectNotFoundError,
)
from generative_ai.googleflow.constants import CONFIGS, URLS

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

pytestmark = pytest.mark.live


async def test_new_project_opens_a_workspace(project: GoogleFlowProject) -> None:
    assert project.project_magic
    assert project.project_url == f"{URLS.project}{project.project_magic}"
    assert not project.is_closed

    page = project._require_page()
    assert page.url.rstrip("/") == project.project_url
    assert page.viewport_size == {
        "width": CONFIGS.VIEWPORT_WIDTH,
        "height": CONFIGS.VIEWPORT_HEIGHT,
    }


async def test_open_existing_project_then_close(
    context: BrowserContext, project: GoogleFlowProject
) -> None:
    reopened = await GoogleFlowProject.open(project.project_magic, context)
    try:
        assert reopened.project_url == project.project_url
        assert reopened._require_page().url.rstrip("/") == project.project_url
    finally:
        await reopened.close()

    assert reopened.is_closed
    with pytest.raises(ProjectClosedError):
        await reopened.list_media()


async def test_open_unknown_project_raises_not_found(context: BrowserContext) -> None:
    pages_before = len(context.pages)

    with pytest.raises(ProjectNotFoundError):
        await GoogleFlowProject.open(uuid4().hex, context)

    # The failed open must not leak its page.
    assert len(context.pages) == pages_before


async def test_rename_project(project: GoogleFlowProject) -> None:
    # Success is verified by the manifest: the projects page shows the new
    # name and the workspace reloads.
    await project.rename(f"  pytest {uuid4().hex[:8]}  ")

    assert project._require_page().url.rstrip("/") == project.project_url


async def test_rename_rejects_blank_name(project: GoogleFlowProject) -> None:
    with pytest.raises(ValueError, match="empty"):
        await project.rename("   ")


async def test_delete_project_makes_it_unreachable(context: BrowserContext) -> None:
    project = await GoogleFlowProject.new(context)
    magic = project.project_magic

    await project.delete()

    assert project.is_closed
    with pytest.raises(ProjectNotFoundError):
        await GoogleFlowProject.open(magic, context)
