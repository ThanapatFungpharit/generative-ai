from __future__ import annotations


class SiteError(RuntimeError):
    """Base class for failures raised by any supported site's domain API."""


class UnexpectedPageStateError(SiteError):
    """An expected UI postcondition was not reached."""


class UnknownSiteError(SiteError, LookupError):
    def __init__(self, name: str, known: tuple[str, ...]) -> None:
        super().__init__(f"Unknown site {name!r}; registered sites: {list(known)}.")


class SiteAlreadyRegisteredError(SiteError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Site {name!r} is already registered.")
