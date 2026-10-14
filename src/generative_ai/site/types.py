"""Semantic aliases shared by every supported site."""

from __future__ import annotations

# Raw input: untrusted data, not yet validated.
type RawJson = object
"""A value returned from page JavaScript, before it is checked."""

# Units
type Seconds = float | int
type Pixels = int

# Identifiers
type SiteName = str
"""Registry key of a site, e.g. ``googleflow``."""
type Url = str
