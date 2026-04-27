"""Agent registry + delegation parsing tests."""

from __future__ import annotations

from pathlib import Path

from ivo.agents import AgentRegistry, extract_delegations, strip_delegations

AGENTS_DIR = Path(__file__).resolve().parent.parent / ".github" / "agents"


def test_registry_loads_bundled_chat_agent():
    reg = AgentRegistry(AGENTS_DIR)
    assert "chat" in reg.names()


def test_chat_agent_frontmatter_parses_correctly():
    reg = AgentRegistry(AGENTS_DIR)
    a = reg.get("chat")
    assert a is not None
    assert a.name == "chat"
    assert a.adapter == "copilot"
    assert "default chat agent" in a.system_prompt.lower()


def test_extract_delegations_finds_block():
    text = 'sure thing\n<delegate to="researcher">find the latest btc halving date</delegate>\ndone'
    calls = extract_delegations(text)
    assert len(calls) == 1
    assert calls[0].agent == "researcher"
    assert "btc halving" in calls[0].prompt


def test_strip_delegations_removes_blocks():
    text = 'before <delegate to="x">y</delegate> after'
    out = strip_delegations(text)
    assert "delegate" not in out
    assert "before" in out and "after" in out


# ── workspace overlay ───────────────────────────────────────────────────────

_WS_AGENT_TEMPLATE = """---
name: {name}
description: {desc}
adapter: copilot
system_prompt_inline: |
  {prompt}
---
"""


def test_registry_bundled_only_when_no_workspace():
    """Without workspace_path, only bundled agents are loaded."""
    reg = AgentRegistry(AGENTS_DIR)
    a = reg.get("chat")
    assert a is not None
    assert "WORKSPACE_OVERRIDE_MARKER" not in a.system_prompt


def test_registry_workspace_overrides_and_adds(tmp_path):
    """Workspace agents override bundled ones (same stem) and add new ones."""
    ws_agents = tmp_path / ".github" / "agents"
    ws_agents.mkdir(parents=True)
    # override 'chat'
    (ws_agents / "chat.md").write_text(
        _WS_AGENT_TEMPLATE.format(
            name="chat",
            desc="overridden",
            prompt="WORKSPACE_OVERRIDE_MARKER chat body",
        )
    )
    # brand-new agent only present in workspace
    (ws_agents / "ceo.md").write_text(
        _WS_AGENT_TEMPLATE.format(
            name="ceo",
            desc="host project ceo",
            prompt="i am the ceo",
        )
    )

    reg = AgentRegistry(AGENTS_DIR, workspace_path=tmp_path)
    # override took effect
    chat = reg.get("chat")
    assert chat is not None
    assert "WORKSPACE_OVERRIDE_MARKER" in chat.system_prompt
    assert chat.description == "overridden"
    # workspace-only agent loaded
    ceo = reg.get("ceo")
    assert ceo is not None
    assert "i am the ceo" in ceo.system_prompt


def test_registry_expands_user_home(tmp_path, monkeypatch):
    """workspace_path='~' expands to $HOME so personal ~/.github/agents loads."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ws_agents = tmp_path / ".github" / "agents"
    ws_agents.mkdir(parents=True)
    (ws_agents / "home_only.md").write_text(
        _WS_AGENT_TEMPLATE.format(
            name="home_only",
            desc="lives in user home",
            prompt="hello from home",
        )
    )
    reg = AgentRegistry(AGENTS_DIR, workspace_path="~")
    assert reg.get("home_only") is not None
