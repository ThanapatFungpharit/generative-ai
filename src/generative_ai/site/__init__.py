"""Site-independent building blocks for browser automation of generative AI tools.

To support a new website, create a subpackage next to ``googleflow`` with:

* ``manifests/selectors.yaml`` and ``manifests/actions/*.yaml`` (``web_page`` flows),
* a ``Site`` describing them, registered with ``register_site``,
* a ``SitePage`` subclass exposing the site's operations.

See the README's "Adding a site" section.
"""

from __future__ import annotations

from generative_ai.site.definition import Site
from generative_ai.site.exceptions import (
    SiteAlreadyRegisteredError,
    SiteError,
    UnexpectedPageStateError,
    UnknownSiteError,
)
from generative_ai.site.page import (
    SitePage,
    close_page_and_report_failure,
    note_cleanup_failure,
    page_required,
    text_result,
    texts_result,
)
from generative_ai.site.protocols import BrowserContext, BrowserLocator, BrowserPage
from generative_ai.site.registry import get_site, register_site, registered_sites

__all__ = [
    "BrowserContext",
    "BrowserLocator",
    "BrowserPage",
    "Site",
    "SiteAlreadyRegisteredError",
    "SiteError",
    "SitePage",
    "UnexpectedPageStateError",
    "UnknownSiteError",
    "close_page_and_report_failure",
    "get_site",
    "note_cleanup_failure",
    "page_required",
    "register_site",
    "registered_sites",
    "text_result",
    "texts_result",
]
