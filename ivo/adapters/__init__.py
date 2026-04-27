"""Adapter registry & factory."""

from __future__ import annotations

from typing import Any

from .base import Adapter, Message, ModelInfo
from .copilot import CopilotAdapter
from .ollama import OllamaAdapter

REGISTRY: dict[str, type[Adapter]] = {
    "copilot": CopilotAdapter,
    "ollama": OllamaAdapter,
}


def build_adapters(adapter_configs: dict[str, dict[str, Any]]) -> dict[str, Adapter]:
    """Instantiate all enabled adapters from the config's `adapters:` section."""
    out: dict[str, Adapter] = {}
    for name, raw in adapter_configs.items():
        if not raw.get("enabled"):
            continue
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown adapter: {name!r} (available: {sorted(REGISTRY)})")
        # Strip 'enabled'; pass the rest as options.
        options = {k: v for k, v in raw.items() if k != "enabled"}
        out[name] = cls(options=options)
    return out


__all__ = [
    "Adapter",
    "Message",
    "ModelInfo",
    "REGISTRY",
    "build_adapters",
]
