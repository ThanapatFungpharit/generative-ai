from __future__ import annotations

import asyncio
from typing import Self

from playwright.async_api import BrowserContext, Page

from generative_ai.googleflow.constants import CONFIGS, SELECTORS, URLS
from generative_ai.googleflow.exceptions import (
    ProjectNotFoundError,
    ProjectOpenError,
)
from generative_ai.googleflow.utils import (
    is_not_found_url,
    is_project_url,
    normalize_project_magic,
    project_magic_from_url,
)


class _UnexpectedProjectStateError(RuntimeError):
    """An expected Flow UI postcondition was not reached."""


class GoogleFlowProject:
    def __init__(self, project_magic: str, page: Page) -> None:
        self._project_magic = normalize_project_magic(project_magic)
        self._project_url = f"{URLS.project}{self._project_magic}"
        self._page: Page | None = page
        self._operation_lock = asyncio.Lock()

    @property
    def project_magic(self) -> str:
        """The stable identifier contained in this project's Flow URL."""
        return self._project_magic

    @property
    def project_url(self) -> str:
        """The canonical browser URL for this project."""
        return self._project_url

    @classmethod
    async def open(cls, project_magic: str, context: BrowserContext) -> Self:
        project_magic = normalize_project_magic(project_magic)
        page: Page | None = None

        page = await context.new_page()
        project = cls(project_magic, page)
        await page.goto(
            project.project_url,
            wait_until="domcontentloaded",
            timeout=CONFIGS.NAVIGATION_TIMEOUT_MS,
        )

        if is_not_found_url(page.url):
            raise ProjectNotFoundError(project.project_magic)
        if not is_project_url(page.url, project.project_magic):
            raise ProjectOpenError(project.project_magic)

        await page.locator(SELECTORS.open_add_menu_button).wait_for(
            state="visible", timeout=CONFIGS.NAVIGATION_TIMEOUT_MS
        )
        return project

    @classmethod
    async def new(cls, context: BrowserContext) -> Self:
        page = await context.new_page()
        await page.goto(
            URLS.base,
            wait_until="domcontentloaded",
            timeout=CONFIGS.NAVIGATION_TIMEOUT_MS,
        )

        new_project_button = page.locator(SELECTORS.new_project_button)
        await new_project_button.wait_for(state="visible")
        await new_project_button.click()

        await page.wait_for_url(
            f"{URLS.project}*", timeout=CONFIGS.NAVIGATION_TIMEOUT_MS
        )
        await page.locator(SELECTORS.open_add_menu_button).wait_for(
            state="visible", timeout=CONFIGS.NAVIGATION_TIMEOUT_MS
        )
        project_magic = project_magic_from_url(page.url)
        return cls(project_magic, page)
