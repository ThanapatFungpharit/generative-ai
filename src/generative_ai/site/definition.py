"""What a site contributes: its URLs, manifests and browser requirements."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from web_page import WebPage
from web_page.utils import is_str_dict
from yaml import safe_load

from generative_ai.site.guards import is_str_keyed_mapping

if TYPE_CHECKING:
    from collections.abc import Mapping

    from web_page.types import CssSelector, Milliseconds, RawYaml, SelectorKey

    from generative_ai.site.protocols import BrowserPage
    from generative_ai.site.types import Pixels, SiteName, Url


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
    # Sites that switch to a mobile layout on narrow viewports set a floor.
    min_viewport_width: Pixels = 0
    min_viewport_height: Pixels = 0

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

        viewport = page.viewport_size
        if viewport is not None and (
            viewport["width"] < self.min_viewport_width
            or viewport["height"] < self.min_viewport_height
        ):
            await page.set_viewport_size(
                {
                    "width": max(viewport["width"], self.min_viewport_width),
                    "height": max(viewport["height"], self.min_viewport_height),
                }
            )

        web_page = WebPage(page)
        await web_page.load_selectors(self.selectors_path)
        for action in self.action_paths:
            await web_page.load_action(action)
        return web_page
