"""Resource ownership after browser errors and cancelled deletion."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast, override

import pytest
from playwright.async_api import Error as PlaywrightError

from generative_ai.googleflow import GoogleFlowProject, ProjectCloseError

if TYPE_CHECKING:
    from web_page import WebPage

    from generative_ai.site import BrowserPage


class _Page:
    def __init__(self, *, fail_close: bool = False, stay_open: bool = False) -> None:
        self.closed = False
        self.fail_close = fail_close
        self.stay_open = stay_open
        self.close_calls = 0

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            self.fail_close = False
            raise PlaywrightError("close failed")
        self.closed = not self.stay_open


class _DeleteWeb:
    def __init__(self) -> None:
        self.submitted = asyncio.Event()

    async def run(self, action: str, params: object = None) -> dict[str, object]:
        if action == "confirm_delete_project":
            self.submitted.set()
            await asyncio.Event().wait()
        return {}


class _Project(GoogleFlowProject):
    @override
    async def _ensure_on_workspace(self) -> None:
        pass


@pytest.mark.parametrize("stay_open", [False, True])
async def test_failed_close_keeps_handle_for_retry(stay_open: bool) -> None:
    page = _Page(fail_close=not stay_open, stay_open=stay_open)
    project = _Project("magic", cast("BrowserPage", page), cast("WebPage", object()))
    with pytest.raises(ProjectCloseError):
        await project.close()
    assert not project.is_closed
    assert project._page is page

    page.stay_open = False
    await project.close()
    assert project.is_closed
    assert page.close_calls == 2


async def test_cancelled_deletion_invalidates_facade_and_closes_page() -> None:
    page, web = _Page(), _DeleteWeb()
    project = _Project("magic", cast("BrowserPage", page), cast("WebPage", web))
    deletion = asyncio.ensure_future(project.delete())
    await web.submitted.wait()
    deletion.cancel()
    with pytest.raises(asyncio.CancelledError) as error:
        await deletion

    assert project.is_closed
    assert page.closed
    assert any("state is unknown" in note for note in error.value.__notes__)


async def test_cancelled_deletion_preserves_cleanup_failure() -> None:
    page, web = _Page(fail_close=True), _DeleteWeb()
    project = _Project("magic", cast("BrowserPage", page), cast("WebPage", web))
    deletion = asyncio.ensure_future(project.delete())
    await web.submitted.wait()
    deletion.cancel()
    with pytest.raises(asyncio.CancelledError) as error:
        await deletion

    assert project.is_closed
    assert any("cleanup also failed" in note for note in error.value.__notes__)
