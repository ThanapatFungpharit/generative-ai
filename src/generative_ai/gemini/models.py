from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class GeminiMessage:
    """A rendered message in a chat. Older history may be virtualized by Gemini."""

    role: Literal["user", "assistant"]
    text: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class GeminiResponse:
    """The completed text of one response, with its site-owned identifiers."""

    text: str
    turn_id: str
    chat_url: str
