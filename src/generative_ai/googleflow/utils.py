from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from generative_ai.googleflow.constants import URLS
from generative_ai.googleflow.exceptions import ProjectCreationError

if TYPE_CHECKING:
    from generative_ai.googleflow.types import MediaId, ProjectMagic, SceneId
    from generative_ai.site.types import Url


def project_magic_from_url(url: Url) -> ProjectMagic:
    project_path = urlparse(URLS.project).path
    current_path = urlparse(url).path
    if not current_path.startswith(project_path):
        raise ProjectCreationError(
            f"Google Flow returned an unexpected project URL: {url}"
        )

    project_magic = current_path.removeprefix(project_path).removesuffix("/")
    if not project_magic or "/" in project_magic:
        raise ProjectCreationError(
            f"Google Flow returned an unexpected project URL: {url}"
        )

    try:
        return normalize_project_magic(project_magic)
    except ValueError as error:
        raise ProjectCreationError(
            f"Google Flow returned an unexpected project URL: {url}"
        ) from error


def is_project_url(url: Url, project_magic: ProjectMagic) -> bool:
    """Return whether ``url`` identifies the expected Flow project."""
    parsed_url = urlparse(url)
    parsed_base = urlparse(URLS.project)
    return (
        parsed_url.scheme == parsed_base.scheme
        and parsed_url.netloc == parsed_base.netloc
        and parsed_url.path.rstrip("/")
        == f"{parsed_base.path}{project_magic}".rstrip("/")
    )


def is_not_found_url(url: Url) -> bool:
    """Return whether Flow redirected to its not-found route."""
    return urlparse(url).path.rstrip("/") == "/404"


def scene_id_from_url(url: Url, project_magic: ProjectMagic) -> SceneId | None:
    """Return the scene ID if ``url`` is a scene of the given project."""
    scene_path = f"{urlparse(URLS.project).path}{project_magic}/scene/"
    current_path = urlparse(url).path.rstrip("/")
    if not current_path.startswith(scene_path):
        return None
    scene_id = current_path.removeprefix(scene_path)
    return scene_id if _is_identifier(scene_id) else None


def normalize_project_magic(project_magic: str) -> ProjectMagic:
    normalized = project_magic.strip().strip("/")
    if not _is_identifier(normalized):
        raise ValueError(
            "project_magic must contain only ASCII letters, digits, hyphens, "
            "and underscores"
        )
    return normalized


def normalize_media_id(media_id: str) -> MediaId:
    """Validate a media ID; it is interpolated into CSS attribute selectors."""
    normalized = media_id.strip()
    if not _is_identifier(normalized):
        raise ValueError(
            "media_id must contain only ASCII letters, digits, hyphens, and underscores"
        )
    return normalized


def _is_identifier(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None
