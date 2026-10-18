from __future__ import annotations

from generative_ai.site import SiteError


class GeminiError(SiteError):
    """Base class for Google Gemini browser automation failures."""


class GeminiAuthenticationError(GeminiError):
    """The browser profile needs to sign in to Google Gemini."""


class GeminiOpenError(GeminiError):
    """Gemini did not open a usable chat workspace."""


class GeminiChatClosedError(GeminiError):
    """The chat's browser page is closed."""


class GeminiChatCloseError(GeminiError):
    """The chat's browser page could not be closed."""


class GeminiChatDeletionError(GeminiError):
    """Gemini did not verify deletion of the current saved conversation."""


class GeminiTemporaryChatError(GeminiError):
    """Gemini's native Temporary chat mode could not be verified."""


class GeminiModelError(GeminiError):
    """A model is unavailable or its selection could not be verified."""


class GeminiThinkingLevelError(GeminiError):
    """A thinking level is unavailable or its selection could not be verified."""


class GeminiGenerationError(GeminiError):
    """Gemini could not submit or complete a response."""


class GeminiTimeoutError(GeminiGenerationError):
    """A response wait expired; wait_for_response can resume the same turn."""
