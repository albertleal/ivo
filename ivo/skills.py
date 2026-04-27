"""Skill loader.

Reads the skills directory's `meta.yaml` and concatenates skill bodies into
a system-prompt fragment based on which triggers fired this turn.

Skills are plain markdown; meta.yaml says which to auto-load and which are
trigger-conditional (e.g. `chat` vs `voice`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("orchestration.skills")


@dataclass
class Skill:
    name: str
    path: str
    auto_load: bool = False
    triggers: list[str] = field(default_factory=list)
    body: str = ""


class SkillManager:
    """Loads skills from a directory and composes a merged prompt."""

    def __init__(self, skills_dir: Path | str, auto_load: list[str] | None = None) -> None:
        self.dir = Path(skills_dir).expanduser()
        self.auto_load_override = auto_load  # config-level allowlist; None = trust meta.yaml
        self.skills: dict[str, Skill] = {}
        self._load()

    # ── loading ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        meta_path = self.dir / "meta.yaml"
        if not meta_path.exists():
            log.warning("no meta.yaml in %s, skill manager will be empty", self.dir)
            return

        meta = yaml.safe_load(meta_path.read_text()) or {}
        for entry in meta.get("skills", []):
            name = entry["name"]
            rel = entry["path"]
            body_path = self.dir / rel
            body = body_path.read_text() if body_path.exists() else ""
            if not body:
                log.warning("skill %s body missing at %s", name, body_path)
            self.skills[name] = Skill(
                name=name,
                path=rel,
                auto_load=bool(entry.get("auto_load", False)),
                triggers=list(entry.get("triggers", []) or []),
                body=body,
            )

    # ── composition ──────────────────────────────────────────────────────────

    def load(self, triggers: list[str] | None = None) -> str:
        """Return a merged system-prompt fragment for the given triggers.

        A skill is included if:
          - its name is in the config-level auto_load override, OR
          - meta.yaml marks it auto_load AND override is None, OR
          - any of its triggers is present in the `triggers` argument.
        """
        triggers = triggers or []
        active: list[Skill] = []
        for skill in self.skills.values():
            include = False
            if self.auto_load_override is not None:
                if skill.name in self.auto_load_override:
                    include = True
            elif skill.auto_load:
                include = True
            if not include and skill.triggers:
                if any(t in triggers for t in skill.triggers):
                    include = True
            if include:
                active.append(skill)

        if not active:
            return ""

        log.debug("skills active: %s (triggers=%s)", [s.name for s in active], triggers)
        parts = [f"# Skill: {s.name}\n{s.body.strip()}" for s in active]
        return "\n\n".join(parts)
