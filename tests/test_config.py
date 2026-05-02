"""Tests for the YAML+env config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ivo.config import load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"


def test_loads_example_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    cfg = load_config(EXAMPLE, env_file=None)

    assert cfg.telegram.token == "fake-token-123"
    assert cfg.telegram.admin_chat_id == 42
    assert cfg.api.port == 8085
    assert cfg.adapters["copilot"]["enabled"] is True
    assert cfg.adapters["ollama"]["enabled"] is False
    assert cfg.defaults.adapter == "copilot"
    assert "claude-opus-4.7" in cfg.adapters["copilot"]["aliases"].values()


def test_env_substitution_missing_var_yields_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "telegram:\n"
        "  token: ${TELEGRAM_BOT_TOKEN}\n"
        "  admin_chat_id: ${TELEGRAM_CHAT_ID}\n"
        "adapters:\n"
        "  copilot:\n"
        "    enabled: true\n"
        "defaults:\n"
        "  adapter: copilot\n"
    )
    cfg = load_config(cfg_file, env_file=None)
    assert cfg.telegram.token == ""
    assert cfg.telegram.admin_chat_id == 1


def test_workspace_paths_and_active_helpers(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "telegram:\n"
        "  token: x\n"
        "adapters:\n"
        "  copilot:\n"
        "    enabled: true\n"
        "defaults:\n"
        "  adapter: copilot\n"
        "workspaces:\n"
        "  active: ivo\n"
        "  paths:\n"
        "    root: ~/\n"
        "    ivo: ~/Developer/ivo\n"
    )
    cfg = load_config(cfg_file, env_file=None)
    assert "root" in cfg.workspace_paths()
    assert cfg.active_workspace_name() == "ivo"
    assert cfg.active_workspace_path() is not None
