"""Telegram update handlers.

Pure functions, easy to test. The poller wires them into a
python-telegram-bot Application.

Command surface (multi-level wizard):
  /help                       — overview + adapters + general commands
  /models                     — list adapters; `/<adapter>` lists its models
  /<adapter>                  — set adapter (e.g. /copilot, /ollama), then
                                 prompts a model
  /<model_alias>              — set model+adapter, then prompts an agent
  /agent                      — list agents
  /<agent_name>               — set the active agent
  /clear (alias /reset)       — clear chat history
  /voice on|off               — toggle voice replies
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..adapters import Adapter, Message
from ..config import Config
from ..session import SessionStore
from .commands import ModelCommand, render_models_message  # noqa: F401  (re-export)

StatusCb = Callable[[str], Awaitable[None]] | None

log = logging.getLogger("bot.handlers")


@dataclass
class BotContext:
    """Everything the handlers need, bundled."""

    config: Config
    adapters: dict[str, Adapter]
    catalog: dict[str, ModelCommand]
    sessions: SessionStore
    orchestrator: object | None = None  # ivo.orchestrator.Orchestrator
    agent_names: list[str] | None = None


def is_allowed(ctx: BotContext, user_id: int) -> bool:
    # Only the configured admin user may interact with the bot.
    admin_id = ctx.config.telegram.admin_chat_id
    if admin_id is None:
        log.warning("TELEGRAM_CHAT_ID not set; allowing no users")
        return False
    return user_id == admin_id


# ── helpers ──────────────────────────────────────────────────────────────────


def _models_for_adapter(ctx: BotContext, adapter_name: str) -> list[ModelCommand]:
    return [c for c in ctx.catalog.values() if c.provider == adapter_name]


def _render_adapter_list(ctx: BotContext) -> str:
    lines = ["Adapters:"]
    for name in ctx.adapters:
        n = len(_models_for_adapter(ctx, name))
        lines.append(f"  /{name} — {n} models")
    return "\n".join(lines)


def _render_model_list(ctx: BotContext, adapter_name: str) -> str:
    cmds = sorted(_models_for_adapter(ctx, adapter_name), key=lambda c: c.alias)
    if not cmds:
        return f"No models discovered for {adapter_name}."
    lines = [f"Models for {adapter_name}:"]
    for c in cmds:
        lines.append(f"  /{c.alias} — {c.display_name}")
    return "\n".join(lines)


def _render_agent_list(ctx: BotContext) -> str:
    if not ctx.agent_names:
        return "No agents loaded."
    lines = ["Agents:"]
    for name in sorted(ctx.agent_names):
        lines.append(f"  /{name}")
    return "\n".join(lines)


def _current_state(ctx: BotContext, user_id: int) -> str:
    sess = ctx.sessions.get(user_id)
    agent = sess.agent or ctx.config.agents.front_door
    return (
        f"adapter={sess.adapter or '?'}  "
        f"model={sess.model or '?'}  "
        f"agent={agent or '?'}  "
        f"voice={'on' if sess.voice_reply else 'off'}"
    )


# ── command handlers ────────────────────────────────────────────────────────


async def handle_start(ctx: BotContext, user_id: int) -> str:
    import os

    workspace = ctx.config.agents.workspace_path or "(none)"
    if workspace and workspace != "(none)":
        workspace = os.path.expanduser(workspace)
    agents = sorted(ctx.agent_names or [])
    agents_str = ", ".join(f"/{a}" for a in agents) if agents else "(none)"

    lines = [
        "🤖 ivo — Intelligent Virtual Operator — online.",
        f"Workspace: {workspace}",
        f"Agents:    {agents_str}",
        "",
        _render_adapter_list(ctx),
        "",
        "Browse:",
        "  /models         — list adapters (then models)",
        "  /agent          — list agents",
        "",
        "Session:",
        "  /clear          — clear chat history",
        "  /voice          — toggle voice replies",
        "  /stop           — stop the current turn",
        "  /start          — show this message",
        "",
        f"Now: {_current_state(ctx, user_id)}",
    ]
    return "\n".join(lines)


async def handle_models(ctx: BotContext) -> str:
    return _render_adapter_list(ctx) + "\n\nPick one: /<adapter> to see its models."


async def handle_agent_list(ctx: BotContext) -> str:
    return _render_agent_list(ctx) + "\n\nPick one with /<agent_name>."


async def handle_clear(ctx: BotContext, user_id: int) -> str:
    ctx.sessions.reset(user_id)
    # Hot-reload skills + agents from disk so file edits show up instantly.
    reloaded = []
    orch = ctx.orchestrator
    if orch is not None:
        try:
            orch.skills._load()
            reloaded.append(f"{len(orch.skills.skills)} skills")
        except Exception as e:
            log.warning("skill reload failed: %s", e)
        try:
            orch.registry.agents.clear()
            orch.registry._load()
            reloaded.append(f"{len(orch.registry.agents)} agents")
        except Exception as e:
            log.warning("agent reload failed: %s", e)
    suffix = f" — reloaded {', '.join(reloaded)}" if reloaded else ""
    return f"✓ History cleared{suffix}. ({_current_state(ctx, user_id)})"


async def handle_voice_toggle(ctx: BotContext, user_id: int) -> str:
    sess = ctx.sessions.get(user_id)
    new_state = not sess.voice_reply
    ctx.sessions.set_voice_reply(user_id, new_state)
    return f"✓ voice replies {'enabled' if new_state else 'disabled'}"


async def handle_select_adapter(ctx: BotContext, user_id: int, adapter_name: str) -> str:
    if adapter_name not in ctx.adapters:
        return f"Unknown adapter: /{adapter_name}. {_render_adapter_list(ctx)}"
    ctx.sessions.set_adapter(user_id, adapter_name)
    sess = ctx.sessions.get(user_id)
    note = ""
    cmds_here = _models_for_adapter(ctx, adapter_name)
    if not any(c.model_id == sess.model for c in cmds_here):
        note = "\n(your previous model belongs to a different adapter; pick a new one below)"
    return (
        f"✓ adapter = {adapter_name}{note}\n\n"
        + _render_model_list(ctx, adapter_name)
    )


async def handle_select_model(ctx: BotContext, user_id: int, alias: str) -> str:
    cmd = ctx.catalog.get(alias)
    if cmd is None:
        return f"Unknown model: /{alias}. Try /models."
    ctx.sessions.set_model(user_id, cmd.provider, cmd.model_id)
    
    # Auto-select the only available agent to skip unnecessary prompts.
    agent_names = ctx.agent_names or []
    if len(agent_names) == 1:
        ctx.sessions.set_agent(user_id, agent_names[0])
        return (
            f"✓ model = {cmd.display_name} ({cmd.provider})\n"
            f"✓ agent = {agent_names[0]} (auto-selected)\n"
            f"{_current_state(ctx, user_id)}"
        )
    
    return (
        f"✓ model = {cmd.display_name} ({cmd.provider})\n\n"
        + _render_agent_list(ctx)
    )


async def handle_select_agent(ctx: BotContext, user_id: int, agent_name: str) -> str:
    if not ctx.agent_names or agent_name not in ctx.agent_names:
        return f"Unknown agent: /{agent_name}. {_render_agent_list(ctx)}"
    ctx.sessions.set_agent(user_id, agent_name)
    return f"✓ agent = {agent_name}\n{_current_state(ctx, user_id)}"


# ── message handler ─────────────────────────────────────────────────────────


async def handle_message(
    ctx: BotContext,
    user_id: int,
    text: str,
    *,
    status_cb: StatusCb = None,
) -> str:
    """Route a free-form text message through the orchestrator (or adapter)."""
    sess = ctx.sessions.get(user_id)
    if not sess.model or sess.adapter not in ctx.adapters:
        return "No active model. Run /models and pick one."

    # Preferred path: full orchestration (skills + memory + agents).
    if ctx.orchestrator is not None:
        try:
            return await ctx.orchestrator.handle(user_id, text, status_cb=status_cb)
        except Exception as e:
            log.exception("orchestrator failed")
            return f"[error from orchestrator] {e}"

    # Fallback path: direct adapter call (kept for tests / minimal setups).
    adapter = ctx.adapters[sess.adapter]
    user_msg = Message(role="user", content=text)
    ctx.sessions.append(user_id, user_msg)
    sess = ctx.sessions.get(user_id)
    messages: list[Message] = list(sess.history)

    chunks: list[str] = []
    try:
        async for chunk in adapter.chat(sess.model, messages):
            chunks.append(chunk)
    except Exception as e:
        log.exception("adapter chat failed")
        return f"[error from {sess.adapter}] {e}"

    reply = "".join(chunks).strip() or "(no reply)"
    ctx.sessions.append(user_id, Message(role="assistant", content=reply))
    return reply
