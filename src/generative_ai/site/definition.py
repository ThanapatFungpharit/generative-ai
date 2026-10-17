"""What a site contributes: its URLs, manifests and browser requirements."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from web_page import WebPage
from web_page.utils import is_str_dict
from yaml import safe_load

from generative_ai.site.guards import is_str_keyed_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping

    from playwright.async_api import Page as ChromiumPage
    from web_page.types import CssSelector, Milliseconds, RawYaml, SelectorKey

    from generative_ai.site.protocols import BrowserPage, ViewportSize
    from generative_ai.site.types import SiteName, Url


@dataclass(frozen=True, slots=True)
class Site:
    """Static description of one website and how to attach to its pages.

    ``manifests_dir`` holds ``selectors.yaml`` and an ``actions/`` directory of
    ``*.yaml`` flows; both are loaded into every page this site attaches.
    """

    name: SiteName
    base_url: Url
    manifests_dir: Path
    navigation_timeout_ms: Milliseconds = 30_000.0
    # Sites whose layout shifts with the viewport pin one exact size.
    viewport: ViewportSize | None = None
    # Chromium sites which defer work when their tab loses visibility or focus.
    keep_active: bool = False

    @property
    def selectors_path(self) -> Path:
        return self.manifests_dir / "selectors.yaml"

    @property
    def action_paths(self) -> tuple[Path, ...]:
        return tuple(sorted((self.manifests_dir / "actions").glob("*.yaml")))

    def load_selectors(self) -> Mapping[SelectorKey, CssSelector]:
        """Read the selectors manifest so Python-side locators share one source."""
        path = self.selectors_path
        manifest: RawYaml = safe_load(path.read_text(encoding="utf-8"))
        selectors: RawYaml = (
            manifest.get("selectors") if is_str_keyed_mapping(manifest) else None
        )
        if not is_str_dict(selectors):
            msg = f"{path} must map 'selectors' to a mapping of strings to strings."
            raise TypeError(msg)
        return MappingProxyType(selectors)

    async def attach(self, page: BrowserPage) -> WebPage:
        """Wrap ``page`` in a ``WebPage`` with every manifest of this site loaded."""
        # Steps without their own manifest ``timeout`` use the page defaults.
        page.set_default_timeout(self.navigation_timeout_ms)
        page.set_default_navigation_timeout(self.navigation_timeout_ms)

        # Enforced in both directions, and also for ``no_viewport=True`` contexts
        # (``viewport_size`` is None); once emulated, window resizes cannot move it.
        if self.viewport is not None and page.viewport_size != self.viewport:
            await page.set_viewport_size(self.viewport)

        web_page = WebPage(page)
        await web_page.load_selectors(self.selectors_path)
        for action in self.action_paths:
            await web_page.load_action(action)

        if self.keep_active:
            # Set this before the first navigation, so lazy bootstrap code sees
            # an active page even if another constructor creates a newer tab.
            # Keep the session attached: detaching clears the emulation. The
            # browser owns it until the target closes (including failed opens).
            session = await page.context.new_cdp_session(cast("ChromiumPage", page))
            try:
                await session.send(
                    "Emulation.setFocusEmulationEnabled", {"enabled": True}
                )
            except BaseException as error:
                try:
                    await session.detach()
                except Exception as cleanup_error:  # noqa: BLE001
                    error.add_note(
                        f"Focus session cleanup also failed: {cleanup_error}"
                    )
                raise
        return web_page
