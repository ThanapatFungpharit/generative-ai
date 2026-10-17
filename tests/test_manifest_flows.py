"""Nested Flow actions exercised through real locators and local downloads."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from generative_ai.googleflow import GoogleFlowProject, MediaActionError
from generative_ai.googleflow.constants import SITE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from playwright.async_api import Browser, Page

    from generative_ai.site import BrowserPage


@pytest.fixture(scope="module")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Install Playwright Chromium to run DOM fixture tests")
            raise
        try:
            yield browser
        finally:
            await browser.close()


@pytest.fixture
async def page(browser: Browser) -> AsyncIterator[Page]:
    page = await browser.new_page()
    try:
        yield page
    finally:
        await page.close()


_DOWNLOAD_PAGE = """
<flow-grid-tile-container oncontextmenu="openGridMenu(); return false;">
  <img data-media-id="media-id">
</flow-grid-tile-container>
<flow-editor-header>
  <button aria-label="Download media" onclick="openEditorMenu()">Download</button>
</flow-editor-header>
<div class="cdk-overlay-container"></div>
<script>
const fixture = FIXTURE;
window.downloadClicks = 0;
const tile = document.querySelector('flow-grid-tile-container');
if (fixture.entry === 'editor') tile.remove();
const editorButton = document.querySelector('flow-editor-header button');
if (fixture.scenario !== 'direct') editorButton.setAttribute('aria-haspopup', 'menu');
function choose(label) {
  window.selected = label;
  window.downloadClicks++;
  window.staleAtClick = !!document.getElementById('stale-upscale');
  if (fixture.scenario === 'upscale-failure') {
    document.body.insertAdjacentHTML('beforeend', `
      <mat-snack-bar-container>Upscaling your media</mat-snack-bar-container>
      <mat-snack-bar-container><mat-icon>error</mat-icon>
        Upscaling failed<button>Dismiss</button></mat-snack-bar-container>`);
    return;
  }
  if (fixture.scenario === 'upscale-success') {
    document.body.insertAdjacentHTML('beforeend',
      '<mat-snack-bar-container>Upscaling your media</mat-snack-bar-container>');
    setTimeout(() => {
      document.querySelector('mat-snack-bar-container').textContent = 'Upscaling complete';
      save();
    }, 100);
    return;
  }
  save();
}
function save() {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob(['fixture download'], {
    type: 'application/octet-stream'
  }));
  link.download = 'fixture.bin';
  document.body.append(link);
  link.click();
}
function menu() {
  const element = document.createElement('div');
  element.setAttribute('role', 'menu');
  document.querySelector('.cdk-overlay-container').append(element);
  return element;
}
function openQualities() {
  const element = menu();
  if (fixture.scenario === 'upscale-success') {
    document.body.insertAdjacentHTML('beforeend',
      '<mat-snack-bar-container id="stale-upscale">Upscaling failed</mat-snack-bar-container>');
    setTimeout(() => document.getElementById('stale-upscale').remove(), 250);
  }
  const labels = fixture.scenario === 'first-enabled'
    ? ['Disabled quality', '720p', '2K Upscaled']
    : ['270p Animated GIF', 'Original size', '1K (fast)', '2K Upscaled'];
  for (const label of labels) {
    const button = document.createElement('button');
    button.setAttribute('role', 'menuitem');
    button.textContent = label;
    const icon = document.createElement('mat-icon');
    icon.textContent = 'download';
    button.append(icon);
    button.disabled = label === 'Disabled quality';
    button.onclick = () => choose(label);
    element.append(button);
  }
}
function openGridMenu() {
  const button = document.createElement('button');
  button.setAttribute('role', 'menuitem');
  button.textContent = 'Download';
  if (fixture.scenario === 'direct') button.onclick = () => choose('direct');
  else {
    button.setAttribute('aria-haspopup', 'menu');
    button.onclick = openQualities;
  }
  menu().append(button);
}
function openEditorMenu() {
  if (fixture.scenario === 'direct') choose('direct');
  else openQualities();
}
document.addEventListener('keydown', event => {
  if (event.key === 'Escape')
    document.querySelector('.cdk-overlay-container').replaceChildren();
});
</script>
"""


async def _download_project(page: Page, entry: str, scenario: str) -> GoogleFlowProject:
    html = _DOWNLOAD_PAGE.replace(
        "FIXTURE", json.dumps({"entry": entry, "scenario": scenario})
    )

    await page.route(
        "https://flow.google.com/**",
        lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html),
    )
    web = await SITE.attach(cast("BrowserPage", page))
    await page.goto("https://flow.google.com/project/magic")
    return GoogleFlowProject("magic", cast("BrowserPage", page), web)


@pytest.mark.parametrize("entry", ["grid", "editor"])
@pytest.mark.parametrize(
    ("scenario", "quality", "expected"),
    [
        ("direct", "unavailable", "direct"),
        ("requested", "1K (fast)", "1K (fast)"),
        ("original", None, "Original size"),
        ("first-enabled", None, "720p"),
        ("upscale-success", "2K", "2K Upscaled"),
    ],
)
async def test_download_paths_share_quality_selection(
    page: Page,
    tmp_path: Path,
    entry: str,
    scenario: str,
    quality: str | None,
    expected: str,
) -> None:
    project = await _download_project(page, entry, scenario)

    target = await project.download_media("media-id", tmp_path, quality=quality)

    assert target == tmp_path / "fixture.bin"
    assert target.read_bytes() == b"fixture download"
    assert await page.evaluate("window.selected") == expected
    assert await page.evaluate("window.downloadClicks") == 1
    assert not await page.evaluate("window.staleAtClick")


@pytest.mark.parametrize("entry", ["grid", "editor"])
async def test_unavailable_quality_preserves_options_without_downloading(
    page: Page,
    tmp_path: Path,
    entry: str,
) -> None:
    project = await _download_project(page, entry, "requested")

    with pytest.raises(MediaActionError) as caught:
        await project.download_media("media-id", tmp_path, quality="4K")

    assert caught.value.__notes__ == [
        "Available downloads: 270p Animated GIF, Original size, 1K (fast), 2K Upscaled."
    ]
    assert await page.evaluate("window.downloadClicks") == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("entry", ["grid", "editor"])
async def test_upscale_failure_preserves_flow_messages_without_retrying(
    page: Page,
    tmp_path: Path,
    entry: str,
) -> None:
    project = await _download_project(page, entry, "upscale-failure")

    with pytest.raises(MediaActionError) as caught:
        await project.download_media("media-id", tmp_path, quality="2K")

    assert "Google Flow reported: Upscaling failed" in caught.value.__notes__
    assert all("Dismiss" not in note for note in caught.value.__notes__)
    assert await page.evaluate("window.downloadClicks") == 1
    assert list(tmp_path.iterdir()) == []


async def test_settings_read_opens_panel_and_keeps_it_open_for_repeated_reads(
    page: Page,
) -> None:
    html = """
        <flow-base-prompt-box>
          <button class="settings-trigger-button" onclick="
            window.toggles++;
            const panel = document.querySelector('flow-prompt-box-settings');
            panel.hidden = !panel.hidden;
          ">Settings</button>
          <span class="settings-summary">Image · 1:1 · x1</span>
        </flow-base-prompt-box>
        <flow-prompt-box-settings hidden>
          <button aria-label="Select model family">Nano Banana 2.1</button>
          <flow-toggles aria-label="Mode">
            <button role="radio" aria-checked="true">Image<mat-icon>image</mat-icon></button>
          </flow-toggles>
        </flow-prompt-box-settings>
        <script>
          window.toggles = 0;
          document.addEventListener('keydown', event => {
            if (event.key === 'Escape')
              document.querySelector('flow-prompt-box-settings').hidden = true;
          });
        </script>
    """
    await page.route(
        "https://flow.google.com/project/magic",
        lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=html),
    )
    web = await SITE.attach(cast("BrowserPage", page))
    await page.goto("https://flow.google.com/project/magic")

    first = await web.run("read_generation_settings")
    second = await web.run("read_generation_settings")

    assert first == second
    assert first["model"] == "Nano Banana 2.1"
    assert first["mode"] == ["Image"]
    assert first["aspect_ratio"] == []
    assert first["summary"] == "Image · 1:1 · x1"
    assert await page.evaluate("window.toggles") == 1

    project = GoogleFlowProject("magic", cast("BrowserPage", page), web)
    settings = await project.get_generation_settings()
    assert settings.model == "Nano Banana 2.1"
    assert not await page.locator("flow-prompt-box-settings").is_visible()
    assert await project.get_generation_settings() == settings
    assert await page.evaluate("window.toggles") == 2
    assert not await page.locator("flow-prompt-box-settings").is_visible()
