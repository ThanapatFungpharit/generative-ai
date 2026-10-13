import re
from urllib.parse import urlparse

from generative_ai.googleflow.constants import URLS
from generative_ai.googleflow.exceptions import (
    ProjectCreationError,
)


def project_magic_from_url(url: str) -> str:
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
    return normalize_project_magic(project_magic)


def is_project_url(url: str, project_magic: str) -> bool:
    """Return whether ``url`` identifies the expected Flow project."""
    parsed_url = urlparse(url)
    parsed_base = urlparse(URLS.project)
    return (
        parsed_url.scheme == parsed_base.scheme
        and parsed_url.netloc == parsed_base.netloc
        and parsed_url.path.rstrip("/")
        == f"{parsed_base.path}{project_magic}".rstrip("/")
    )


def is_not_found_url(url: str) -> bool:
    """Return whether Flow redirected to its not-found route."""
    return urlparse(url).path.rstrip("/") == "/404"


def normalize_project_magic(project_magic: str) -> str:
    normalized = project_magic.strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        raise ValueError(
            "project_magic must contain only ASCII letters, digits, hyphens, "
            "and underscores"
        )
    return normalized
