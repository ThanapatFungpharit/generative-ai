"""Offline checks of how GoogleFlowProject's polling waits handle page trouble."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, cast

import pytest
from playwright.async_api import Error as PlaywrightError

from generative_ai.googleflow import GoogleFlowProject, ProjectClosedError
from generative_ai.googleflow import project as project_module
from generative_ai.googleflow.constants import CONFIGS

if TYPE_CHECKING:
    from web_page import WebPage

    from generative_ai.site import BrowserPage

_TILE = {
    "title": "",
    "labels": [],
    "kind": "image",
    "mediaId": "abc",
    "pending": False,
}


class _FakePage:
    """Fails the first ``failures`` grid snapshots, then returns one tile."""

    def __init__(self, failures: int, *, close_on_failure: bool = False) -> None:
        self.failures = failures
        self.close_on_failure = close_on_failure
        self.snapshots = 0
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def evaluate(self, expression: str, arg: object = None) -> object:
        self.snapshots += 1
        if self.snapshots <= self.failures:
            self.closed = self.close_on_failure
            raise PlaywrightError("Execution context was destroyed")
        return [_TILE]


class _FakeWebPage:
    def __init__(self, messages: list[list[str]]) -> None:
        self.messages = messages

    async def run(self, action: str, params: object = None) -> dict[str, object]:
        assert action == "read_flow_messages"
        return {"messages": self.messages.pop(0) if self.messages else []}


@pytest.fixture(autouse=True)
def _no_poll_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    configs = dataclasses.replace(CONFIGS, POLL_INTERVAL_SECONDS=0)
    monkeypatch.setattr(project_module, "CONFIGS", configs)


def _project(page: _FakePage, web_page: _FakeWebPage) -> GoogleFlowProject:
    return GoogleFlowProject(
        "magic", cast("BrowserPage", page), cast("WebPage", web_page)
    )


async def test_poll_rides_out_brief_snapshot_errors() -> None:
    page = _FakePage(failures=CONFIGS.POLL_SNAPSHOT_ATTEMPTS - 1)

    tiles = await _project(page, _FakeWebPage([]))._poll_media()

    assert [tile.item.media_id for tile in tiles] == ["abc"]


async def test_poll_gives_up_after_repeated_snapshot_errors() -> None:
    page = _FakePage(failures=CONFIGS.POLL_SNAPSHOT_ATTEMPTS)

    with pytest.raises(PlaywrightError):
        await _project(page, _FakeWebPage([]))._poll_media()
    assert page.snapshots == CONFIGS.POLL_SNAPSHOT_ATTEMPTS


async def test_poll_stops_at_once_when_the_page_closed() -> None:
    page = _FakePage(failures=100, close_on_failure=True)

    with pytest.raises(ProjectClosedError):
        await _project(page, _FakeWebPage([]))._poll_media()
    assert page.snapshots == 1


async def test_failure_reports_snackbars_seen_while_polling() -> None:
    web_page = _FakeWebPage([["Prompt was flagged"], [], ["Prompt was flagged"], []])
    project = _project(_FakePage(failures=0), web_page)
    for _ in range(3):
        await project._poll_media()

    error = RuntimeError("generation failed")
    await project._note_flow_message(error)

    assert error.__notes__ == ["Google Flow reported: Prompt was flagged"]
    # Reported messages are not repeated on a later failure.
    later = RuntimeError("again")
    await project._note_flow_message(later)
    assert not hasattr(later, "__notes__")
