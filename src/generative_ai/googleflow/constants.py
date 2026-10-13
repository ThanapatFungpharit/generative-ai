from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _GoogleFlowUrls:
    base: str = "https://flow.google.com/"
    project: str = "https://flow.google.com/project/"
    media_preview: str = "https://flow-content.google/image/"


@dataclass(frozen=True, slots=True)
class _GoogleFlowSelectors:
    new_project_button: str = ".new-project-button"
    project_card: str = ".project-card"
    title_input: str = ".title-input"
    project_title: str = ".project-title-label"
    edit_title_button: str = ".project-title-label button"
    delete_project_button: str = "button[aria-label='Delete project']"
    confirm_delete_project_button: str = "flow-confirmation-dialog mat-dialog-actions button:has(span:text-is('Delete project'))"

    add_menu_popover: str = "flow-add-menu-popover-content"
    open_add_menu_button: str = ".add-menu-trigger"
    upload_media_button: str = "button[mattooltip='Upload media']"


@dataclass(frozen=True, slots=True)
class _Configs:
    MENU_CLOSE_MAX_ATTEMPTS: int = 3
    MENU_CLOSE_RETRY_TIMEOUT_MS: float = 300.0
    UPLOAD_TIMEOUT_SECONDS: float = 30.0
    OPTIONAL_CONSENT_TIMEOUT_MS: float = 500.0
    NAVIGATION_TIMEOUT_MS: float = 30_000.0
    RECOVERY_TIMEOUT_MS: float = 5_000.0
    CLEANUP_TIMEOUT_SECONDS: float = 2.0


URLS = _GoogleFlowUrls()
SELECTORS = _GoogleFlowSelectors()
CONFIGS = _Configs()
