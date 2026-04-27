"""Build the dynamic command catalog from discovered models.

The result is consumed by `handlers.py` to register one Telegram CommandHandler
per slash alias.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..adapters import Adapter, ModelInfo


@dataclass(frozen=True)
class ModelCommand:
    alias: str           # slash command without leading "/"
    model_id: str
    provider: str
    display_name: str


def build_catalog(
    adapters: dict[str, Adapter],
    discovered: dict[str, list[ModelInfo]],
) -> dict[str, ModelCommand]:
    """Produce a {alias: ModelCommand} map.

    On alias collisions across providers, the second one is suffixed with the
    provider name (e.g. `gpt5_copilot`) so we never silently drop a model.
    """
    out: dict[str, ModelCommand] = {}
    for provider, models in discovered.items():
        for m in models:
            alias = m.slash_alias
            if alias in out:
                alias = f"{m.slash_alias}_{provider}"
            out[alias] = ModelCommand(
                alias=alias,
                model_id=m.id,
                provider=provider,
                display_name=m.display_name,
            )
    return out


def render_models_message(catalog: dict[str, ModelCommand]) -> str:
    """Human-readable /models output, grouped by provider."""
    by_provider: dict[str, list[ModelCommand]] = {}
    for cmd in catalog.values():
        by_provider.setdefault(cmd.provider, []).append(cmd)

    if not by_provider:
        return "No models discovered."

    lines: list[str] = ["Available models:"]
    for provider in sorted(by_provider):
        lines.append(f"\n[{provider}]")
        for cmd in sorted(by_provider[provider], key=lambda c: c.alias):
            lines.append(f"  /{cmd.alias} — {cmd.display_name}")
    return "\n".join(lines)
