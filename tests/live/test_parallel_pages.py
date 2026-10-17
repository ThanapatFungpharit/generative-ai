"""Parallel project pages against Flow; no generation credits are spent."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from generative_ai.googleflow import GoogleFlowProject

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

pytestmark = pytest.mark.live


@pytest.mark.parametrize("page_count", [2, 4], ids=["two-pages", "four-pages"])
async def test_parallel_project_pages_remain_active(
    context: BrowserContext,
    project: GoogleFlowProject,
    page_count: int,
) -> None:
    pages: list[GoogleFlowProject] = []

    async def reopen() -> None:
        pages.append(await GoogleFlowProject.open(project.project_magic, context))

    try:
        # A TaskGroup waits for cancelled siblings before cleanup, so an open
        # which fails cannot leak another page that is still being constructed.
        async with asyncio.TaskGroup() as group:
            for _ in range(page_count - 1):
                group.create_task(reopen())

        async def check(reopened: GoogleFlowProject) -> None:
            page = reopened._require_page()
            assert await page.evaluate("!document.hidden && document.hasFocus()")
            async with asyncio.timeout(10):
                assert await page.evaluate("""() => new Promise(resolve => {
                    requestAnimationFrame(() => requestIdleCallback(() => resolve(true)));
                })""")
            await reopened.get_generation_settings()
            await reopened.list_media()

        async with asyncio.TaskGroup() as group:
            for reopened in [project, *pages]:
                group.create_task(check(reopened))
    finally:
        await asyncio.gather(*(reopened.close() for reopened in pages))
