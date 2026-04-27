"""Configuration loader.

Reads a YAML config file, expands ${ENV_VAR} references against the process
environment (loaded from `.env` if present), and validates with pydantic.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


# ── Schema ───────────────────────────────────────────────────────────────────


class TelegramConfig(BaseModel):
    token: str
    admin_chat_id: int | None = None
    long_poll_timeout: int = 30


class APIConfig(BaseModel):
    enable: bool = True
    host: str = "0.0.0.0"
    port: int = 8085
    # Whitelist of client IPs that may call the API. Default = loopback only.
    # Set to an empty list to disable the gate (fully open — trust your LAN).
    allowed_ips: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])


class AdapterConfig(BaseModel):
    enabled: bool = False
    # Adapter-specific options live here as a free-form dict.
    options: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class DefaultsConfig(BaseModel):
    adapter: str


class SessionConfig(BaseModel):
    backend: str = "sqlite"  # sqlite | json | memory
    path: str = "./data/sessions.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"


class SkillsConfig(BaseModel):
    dir: str = "./.github/skills"
    # If non-empty, this list overrides meta.yaml's `auto_load: true` flags.
    # If empty/None, the meta.yaml defaults win.
    auto_load: list[str] = Field(default_factory=list)


class MemoryConfig(BaseModel):
    dir: str = "./.ivo/memory"
    per_user: bool = False
    max_chars: int = 4000
    # When True (and agents.workspace_path is set), memory files live in
    # <workspace_path>/.ivo/memory/ instead of `dir`. Lets a host project
    # share its existing memory directory with the bot.
    use_workspace: bool = False


class AgentsConfig(BaseModel):
    dir: str = "./.github/agents"
    # Absolute path to a host project. When set, agents from
    # <workspace_path>/.github/agents/*.md are merged on top of the bundled
    # ones (workspace agents override bundled ones with the same name).
    # Defaults to the user's home (~) so a personal `.github/agents/` there
    # is auto-discovered.
    workspace_path: str | None = "~"
    front_door: str = "chat"
    max_delegation_depth: int = 3
    delegation_mode: str = "splice"  # "splice" | "replace"


class Config(BaseModel):
    telegram: TelegramConfig
    api: APIConfig = Field(default_factory=APIConfig)
    adapters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    defaults: DefaultsConfig
    session: SessionConfig = Field(default_factory=SessionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)


# ── Loader ───────────────────────────────────────────────────────────────────


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} references in strings, against os.environ."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            var = m.group(1)
            return os.environ.get(var, "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: Path | str, *, env_file: Path | str | None = ".env") -> Config:
    """Load a YAML config from `path`, expand env vars, validate."""
    if env_file:
        env_path = Path(env_file)
        if env_path.exists():
            load_dotenv(env_path)

    raw = yaml.safe_load(Path(path).read_text())
    expanded = _expand_env(raw)
    return Config.model_validate(expanded)
