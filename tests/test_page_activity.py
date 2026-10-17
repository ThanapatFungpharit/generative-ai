"""Background execution without relying on Playwright's launch defaults."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from generative_ai.googleflow import GoogleFlowProject, ProjectCreationError
from generative_ai.googleflow.constants import SITE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from playwright.async_api import BrowserContext

    from generative_ai.site import BrowserPage
    from generative_ai.site.protocols import BrowserContext as ContextProtocol


class _Session:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.detached = False

    async def send(self, method: str, params: dict[str, object]) -> None:
        assert (method, params) in [
            ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
        ]
        if self.error:
            raise self.error

    async def detach(self) -> None:
        self.detached = True


class _Page:
    viewport_size = SITE.viewport
    url = "about:blank"

    def __init__(self, error: BaseException | None = None) -> None:
        self.session = _Session(error)
        self.sessions = 0
        self.closed = False

    @property
    def context(self) -> _Page:
        return self

    async def new_page(self) -> _Page:
        return self

    async def new_cdp_session(self, page: object) -> _Session:
        assert page is self
        self.sessions += 1
        return self.session

    def set_default_timeout(self, timeout: object) -> None:
        pass

    def set_default_navigation_timeout(self, timeout: object) -> None:
        pass

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


async def test_activity_is_site_specific_and_retains_the_session() -> None:
    page = _Page()
    await replace(SITE, keep_active=False).attach(cast("BrowserPage", page))
    assert page.sessions == 0

    await SITE.attach(cast("BrowserPage", page))
    assert page.sessions == 1
    assert not page.session.detached


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_activity_setup_releases_session_and_project_page(
    cancelled: bool,
) -> None:
    error = asyncio.CancelledError() if cancelled else PlaywrightError("unsupported")
    page = _Page(error)
    expected = asyncio.CancelledError if cancelled else ProjectCreationError
    with pytest.raises(expected):
        await GoogleFlowProject.new(cast("ContextProtocol", page))
    assert page.session.detached
    assert page.closed


@pytest.fixture(params=[True, False], ids=["headless", "headed"])
async def native_context(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[BrowserContext]:
    """Raw Chromium + CDP omits launch flags and built-in focus emulation.

    A normal Playwright launch already emulates focus, masking the regression.
    Only this owned, disposable browser process is terminated at teardown.
    """
    async with async_playwright() as playwright:
        executable = playwright.chromium.executable_path
        if not Path(executable).is_file():
            pytest.skip("Install Playwright Chromium to run background execution tests")
        args = [
            executable,
            "--no-sandbox",
            "--remote-debugging-port=0",
            f"--user-data-dir={tmp_path}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if request.param:
            args.append("--headless=new")
        process = await asyncio.create_subprocess_exec(
            *args,
            "about:blank",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            port_file = tmp_path / "DevToolsActivePort"
            async with asyncio.timeout(15):
                while not port_file.exists():
                    if process.returncode is not None:
                        pytest.fail(f"Chromium exited with code {process.returncode}")
                    await asyncio.sleep(0.05)
            port = port_file.read_text().splitlines()[0]
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}", no_defaults=True
            )
            try:
                yield browser.contexts[0]
            finally:
                await browser.close()
        finally:
            if process.returncode is None:
                process.terminate()
            await process.wait()


_LAZY_PAGE = """
<button id="load">Load deferred component</button><output></output>
<script>
window.initiallyActive = !document.hidden && document.hasFocus();
window.stage = 'loaded';
document.querySelector('button').addEventListener('click', () => {
    window.stage = 'clicked';
    requestAnimationFrame(() => {
        window.stage = 'frame';
        requestIdleCallback(async () => {
            window.stage = 'idle';
            if (document.hidden || !document.hasFocus()) return;
            const lazy = await import('data:text/javascript,export default 42');
            window.stage = 'imported';
            document.querySelector('output').textContent = String(lazy.default);
        });
    });
});
</script>
"""


async def test_parallel_pages_run_lazy_code_after_navigation(
    native_context: BrowserContext,
) -> None:
    context = native_context
    await context.route(
        "https://activity.test/**",
        lambda route: route.fulfill(content_type="text/html", body=_LAZY_PAGE),
    )

    # A control proves this browser really hides inactive tabs. Keep the
    # default tab selected while application pages navigate in parallel.
    control = await context.new_page()
    await control.goto("https://activity.test/control")
    foreground = await context.new_page()
    await foreground.bring_to_front()
    await control.wait_for_function("document.hidden && !document.hasFocus()")

    async def attach_and_navigate(index: int) -> BrowserPage:
        page = await context.new_page()
        await SITE.attach(cast("BrowserPage", page))
        await page.goto(f"https://activity.test/{index}")
        return cast("BrowserPage", page)

    pages = await asyncio.gather(*(attach_and_navigate(i) for i in range(4)))
    try:
        for round_number in range(2):
            await foreground.bring_to_front()

            async def load_component(page: BrowserPage) -> None:
                # All pages must be active at document startup, before any
                # automation interaction can incidentally focus them.
                assert await page.evaluate("window.initiallyActive")
                await page.locator("button").click()
                try:
                    async with asyncio.timeout(5):
                        await page.evaluate("""() => new Promise(resolve => {
                            const output = document.querySelector('output');
                            if (output.textContent === '42') return resolve();
                            const observer = new MutationObserver(() => {
                                if (output.textContent !== '42') return;
                                observer.disconnect();
                                resolve();
                            });
                            observer.observe(output, {childList:true});
                        })""")
                except TimeoutError:
                    pytest.fail(
                        str(
                            await page.evaluate(
                                "({stage:window.stage,visible:document.visibilityState,focus:document.hasFocus()})"
                            )
                        )
                    )

            await asyncio.gather(*(load_component(page) for page in pages))
            if round_number == 0:
                await asyncio.gather(
                    *(
                        page.goto(
                            "https://activity.test/reload",
                            wait_until="domcontentloaded",
                        )
                        for page in pages
                    )
                )
        # Closing one project's target must leave the others functional.
        await pages[0].close()
        assert await pages[1].evaluate("document.hasFocus() && !document.hidden")
    finally:
        await asyncio.gather(*(page.close() for page in pages if not page.is_closed()))
