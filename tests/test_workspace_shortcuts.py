"""Workspace shortcut command tests."""

from __future__ import annotations

import pytest

from ivo.bot.handlers import BotContext, handle_workspace_list, workspace_shortcuts
from ivo.config import Config, DefaultsConfig, TelegramConfig
from ivo.session import SessionStore


@pytest.mark.asyncio
async def test_workspace_shortcuts_and_list_rendering():
    cfg = Config(
        telegram=TelegramConfig(token="x", admin_chat_id=1),
        adapters={
            "copilot": {"enabled": True},
        },
        defaults=DefaultsConfig(adapter="copilot"),
        workspaces={
            "active": "ivo",
            "paths": {
                "root": "/tmp",
                "ivo": "/tmp",
                "eltomatic": "/tmp",
            },
        },
    )
    sessions = SessionStore(backend="memory", default_workspace="ivo")
    sessions.set_workspace("ivo")
    ctx = BotContext(
        config=cfg,
        adapters={},
        catalog={},
        sessions=sessions,
        agent_names=[],
    )

    shortcuts = workspace_shortcuts(ctx)
    assert shortcuts["wroot"] == "root"
    assert shortcuts["wivo"] == "ivo"
    assert shortcuts["weltomatic"] == "eltomatic"

    text = await handle_workspace_list(ctx, user_id=1)
    assert "/wroot" in text
    assert "/wivo" in text
    assert "/weltomatic" in text
