"""Lookup of supported sites by name."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from generative_ai.site.exceptions import SiteAlreadyRegisteredError, UnknownSiteError

if TYPE_CHECKING:
    from generative_ai.site.definition import Site
    from generative_ai.site.types import SiteName

_SITES: Final[dict[SiteName, Site]] = {}


def register_site(site: Site) -> Site:
    """Make ``site`` discoverable by name; returns it for ``SITE = register_site(...)``."""
    if site.name in _SITES:
        raise SiteAlreadyRegisteredError(site.name)
    _SITES[site.name] = site
    return site


def get_site(name: SiteName) -> Site:
    try:
        return _SITES[name]
    except KeyError:
        raise UnknownSiteError(name, tuple(_SITES)) from None


def registered_sites() -> tuple[Site, ...]:
    return tuple(_SITES.values())
