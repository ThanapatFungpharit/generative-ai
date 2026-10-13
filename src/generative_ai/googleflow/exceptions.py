from pathlib import Path


class GoogleFlowError(RuntimeError):
    """Base class for failures raised by the Google Flow domain API."""


class ProjectNotFoundError(GoogleFlowError):
    def __init__(self, project_magic: str) -> None:
        super().__init__(f"Google Flow project {project_magic!r} was not found.")


class ProjectOpenError(GoogleFlowError):
    def __init__(self, project_magic: str) -> None:
        super().__init__(f"Google Flow project {project_magic!r} could not be opened.")


class ProjectClosedError(GoogleFlowError):
    def __init__(self, project_magic: str) -> None:
        super().__init__(f"Google Flow project {project_magic!r} is already closed.")


class ProjectCloseError(GoogleFlowError):
    def __init__(self, project_magic: str) -> None:
        super().__init__(
            f"The browser page for Google Flow project {project_magic!r} "
            "could not be closed."
        )


class ProjectCreationError(GoogleFlowError):
    """Raised when Google Flow does not create a usable project page."""


class ProjectRenameError(GoogleFlowError):
    def __init__(self, project_magic: str) -> None:
        super().__init__(f"Google Flow project {project_magic!r} could not be renamed.")


class ProjectDeletionError(GoogleFlowError):
    def __init__(self, project_magic: str) -> None:
        super().__init__(f"Google Flow project {project_magic!r} could not be deleted.")


class MediaUploadError(GoogleFlowError):
    """Raised when Google Flow does not complete a media upload."""


class MediaFileReadError(MediaUploadError):
    def __init__(self, file: Path) -> None:
        super().__init__(f"Media file {file!s} could not be read.")
