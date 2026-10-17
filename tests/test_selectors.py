"""Exercise manifests against local DOM fixtures with real Playwright locators."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, override

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from web_page.exceptions import ActionFailed

from generative_ai.googleflow import GoogleFlowProject
from generative_ai.googleflow.constants import SITE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from playwright.async_api import Page
    from web_page import WebPage

    from generative_ai.site import BrowserPage


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install Playwright Chromium to run DOM fixture tests")
            raise
        try:
            yield await browser.new_page()
        finally:
            await browser.close()


@pytest.mark.parametrize("kind", ["image", "video"])
async def test_restore_supports_both_tile_identity_shapes(
    page: Page, kind: str
) -> None:
    identity = (
        '<img data-media-id="media-id">'
        if kind == "image"
        else '<img src="https://flow-content.google/video/media-id?token=fixture">'
    )
    html = (
        f"<flow-grid-tile-container>{identity}"
        '<button aria-label="Restore" '
        "onclick=\"this.closest('flow-grid-tile-container').remove()\">"
        "Restore</button></flow-grid-tile-container>"
    )
    await page.route(
        "https://flow.test/trash",
        lambda route: route.fulfill(content_type="text/html", body=html),
    )
    web_page = await SITE.attach(cast("BrowserPage", page))
    await web_page.run(
        "restore_from_bin",
        {"trash_url": "https://flow.test/trash", "media_id": "media-id"},
    )
    assert await page.locator("flow-grid-tile-container").count() == 0


async def test_delete_waits_for_server_completion_after_dialog_closes(
    page: Page,
) -> None:
    await page.set_content("""
        <flow-confirmation-dialog><mat-dialog-actions>
          <button onclick="
            this.closest('flow-confirmation-dialog').remove();
            setTimeout(() => {
              const ready = document.createElement('button');
              ready.className = 'new-project-button';
              ready.textContent = 'New project';
              document.body.append(ready);
            }, 100);
          "><span>Delete project</span></button>
        </mat-dialog-actions></flow-confirmation-dialog>
    """)
    web_page = await SITE.attach(cast("BrowserPage", page))
    await web_page.run("confirm_delete_project")

    assert await page.locator(".new-project-button").is_visible()


async def test_model_selection_keeps_target_when_hover_opens_submenu(
    page: Page,
) -> None:
    await page.set_content("""
        <flow-prompt-box-settings>
          <button aria-label="Select model family" onclick="
            document.querySelector('.cdk-overlay-container').innerHTML = `
              <div role='menu'>
                <button role='menuitem' onmouseenter='showSubmenu()'
                  onclick='selectModel()'>Nano Banana 2.1</button>
              </div>`;
          ">Other model</button>
        </flow-prompt-box-settings>
        <div class="cdk-overlay-container"></div>
        <script>
          function showSubmenu() {
            if (document.getElementById('submenu')) return;
            const menu = document.createElement('div');
            menu.id = 'submenu';
            menu.setAttribute('role', 'menu');
            menu.style = 'position:absolute;left:600px;top:100px';
            menu.innerHTML = '<button role="menuitem">Other option</button>';
            document.querySelector('.cdk-overlay-container').append(menu);
          }
          function selectModel() {
            document.querySelector('[aria-label="Select model family"]').textContent =
              'Nano Banana 2.1';
            document.querySelector('.cdk-overlay-container').innerHTML = '';
          }
        </script>
    """)
    web_page = await SITE.attach(cast("BrowserPage", page))
    await web_page.run("select_model", {"model": "Nano Banana 2.1"})

    assert await page.locator('[aria-label="Select model family"]').inner_text() == (
        "Nano Banana 2.1"
    )


async def test_probing_a_video_editor_returns_to_the_bin(page: Page) -> None:
    trash_url = "https://flow.google.com/project/magic/trash"
    edit_url = "https://flow.google.com/project/magic/edit/editor-id"
    await page.route(
        trash_url,
        lambda route: route.fulfill(
            content_type="text/html",
            body=f"<flow-grid-tile-container onclick=\"location.href='{edit_url}'\">"
            "Video</flow-grid-tile-container>",
        ),
    )
    await page.route(
        edit_url,
        lambda route: route.fulfill(
            content_type="text/html",
            body='<flow-expandable-prompt><div class="prompt-text">A video</div>'
            "</flow-expandable-prompt>",
        ),
    )
    await page.goto(trash_url)
    web_page = await SITE.attach(cast("BrowserPage", page))
    project = GoogleFlowProject("magic", cast("BrowserPage", page), web_page)

    assert await project._open_tile(0) == ("editor-id", "A video")
    assert page.url == trash_url


async def test_opaque_tile_removal_tracks_the_node_when_next_tile_shifts(
    page: Page,
) -> None:
    await page.set_content("""
        <flow-grid-tile-container id="first" oncontextmenu="
          document.querySelector('.cdk-overlay-container').innerHTML = `
            <div role='menu'><button role='menuitem' onclick='removeTile()'>
              Move to trash</button></div>`;
          return false;
        ">First video</flow-grid-tile-container>
        <flow-grid-tile-container id="second">Other video</flow-grid-tile-container>
        <div class="cdk-overlay-container"></div>
        <script>
          function removeTile() {
            document.getElementById('first').remove();
            document.querySelector('.cdk-overlay-container').innerHTML = '';
          }
        </script>
    """)
    web_page = await SITE.attach(cast("BrowserPage", page))

    class Web:
        async def run(
            self, action: str, params: dict[str, str] | None = None
        ) -> dict[str, object]:
            if action == "run_media_menu_item":
                raise ActionFailed("No content ID on opaque tile", {})
            return dict(await web_page.run(action, params))

    class Project(GoogleFlowProject):
        @override
        async def _ensure_on_workspace(self) -> None:
            pass

        @override
        async def _find_media_tile(self, media_id: str) -> int | None:
            return 0

    project = Project("magic", cast("BrowserPage", page), cast("WebPage", Web()))
    await project.move_to_bin("content-id")

    assert await page.locator("flow-grid-tile-container").count() == 1
    assert (
        await page.locator("flow-grid-tile-container").get_attribute("id") == "second"
    )
