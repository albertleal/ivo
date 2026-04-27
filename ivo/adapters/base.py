"""Adapter contract for LLM backends.

An adapter wraps a single provider (Copilot CLI, Ollama, OpenAI, …). The bot
asks each enabled adapter to discover its models on startup, then routes user
messages to the active adapter via `chat()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

StatusCb = Callable[[str], Awaitable[None]] | None


@dataclass
class ModelInfo:
    """Metadata describing a single model exposed by an adapter."""

    id: str                    # provider-native id, e.g. "claude-opus-4.7"
    display_name: str          # human label shown in /models
    slash_alias: str           # the /command alias, e.g. "opus"
    provider: str              # adapter name, e.g. "copilot"


@dataclass
class Message:
    """Chat message in the canonical format passed to adapters."""

    role: str                  # "system" | "user" | "assistant"
    content: str


class Adapter(ABC):
    """Base class every LLM backend must implement."""

    name: str = "base"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}

    @abstractmethod
    async def discover_models(self) -> list[ModelInfo]:
        """Return the models this adapter can route to.

        Called once at startup. Adapters that can't enumerate at runtime should
        return whatever is configured (e.g. from `aliases` in config).
        """

    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[Message],
        status_cb: StatusCb = None,
    ) -> AsyncIterator[str]:
        """Stream the assistant's reply as text chunks.

        Implementations should be async generators (`async def` + `yield`).
        Callers stitch the chunks into the final reply.

        ``status_cb`` is an optional async callback invoked with short
        human-readable status strings (e.g. "👀 Reading file") so the bot
        can surface tool-use progress to the user.
        """

    async def health(self) -> bool:
        """Return True if the backend is reachable. Default: optimistic."""
        return True
