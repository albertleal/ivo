"""Sub-agent registry.

Each agent is a markdown file with YAML frontmatter:

    ---
    name: researcher
    description: ...
    adapter: copilot
    model: claude-opus-4.7      # optional
    system_prompt_inline: |     # OR system_prompt_file: ./relative/path.md
      You are a researcher...
    tools: []
    ---
    Optional extra body appended after the inline prompt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("orchestration.agents")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass
class AgentDef:
    name: str
    description: str = ""
    adapter: str = ""
    model: str | None = None
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)


class AgentRegistry:
    """Loads agent .md files from a directory.

    If ``workspace_path`` is given, a second pass scans
    ``<workspace_path>/.github/agents/*.md`` and merges those agents on top
    of the bundled set — workspace agents override bundled ones with the
    same stem (e.g. a host project's ``assistant.md`` replaces ours).
    """

    def __init__(
        self,
        agents_dir: Path | str,
        workspace_path: Path | str | None = None,
    ) -> None:
        self.dir = Path(agents_dir).expanduser()
        self.workspace_dir: Path | None = None
        if workspace_path:
            ws = Path(workspace_path).expanduser() / ".github" / "agents"
            self.workspace_dir = ws
        self.agents: dict[str, AgentDef] = {}
        self._load()

    def _load(self) -> None:
        # 1. Bundled agents from package's own dir.
        if self.dir.exists():
            self._scan(self.dir)
        else:
            log.warning("agents dir %s missing", self.dir)
        # 2. Workspace overlay (overrides bundled by stem).
        if self.workspace_dir and self.workspace_dir.exists():
            self._scan(self.workspace_dir)
            log.info("agents: merged workspace overlay from %s", self.workspace_dir)

    def _scan(self, directory: Path) -> None:
        for path in sorted(directory.glob("*.md")):
            try:
                # Some workspaces use the convention `<name>.agent.md` —
                # register them under the bare name (e.g. ceo.agent.md → ceo).
                stem = path.stem
                if stem.endswith(".agent"):
                    stem = stem[: -len(".agent")]
                self.agents[stem] = self._parse(path, stem)
            except Exception as e:
                log.error("failed to parse agent %s: %s", path, e)

    def _parse(self, path: Path, fallback_name: str | None = None) -> AgentDef:
        text = path.read_text()
        m = _FRONTMATTER_RE.match(text)
        if not m:
            raise ValueError(f"agent {path.name} missing frontmatter")
        meta = yaml.safe_load(m.group(1)) or {}
        body = m.group(2).strip()

        prompt_inline = meta.get("system_prompt_inline") or ""
        prompt_file = meta.get("system_prompt_file")
        if prompt_file:
            pf = (path.parent / prompt_file).resolve()
            if pf.exists():
                prompt_inline = pf.read_text()

        full_prompt = prompt_inline.strip()
        if body:
            full_prompt = (full_prompt + "\n\n" + body).strip()

        name = meta.get("name") or fallback_name or path.stem
        return AgentDef(
            name=name,
            description=meta.get("description", ""),
            adapter=meta.get("adapter", ""),
            model=meta.get("model"),
            system_prompt=full_prompt,
            tools=list(meta.get("tools") or []),
        )

    def get(self, name: str) -> AgentDef | None:
        return self.agents.get(name)

    def names(self) -> list[str]:
        return list(self.agents)


# ── delegation parsing ──────────────────────────────────────────────────────

_DELEGATE_RE = re.compile(
    r'<delegate\s+to=["\']([^"\']+)["\']\s*>(.*?)</delegate>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class DelegateCall:
    agent: str
    prompt: str
    span: tuple[int, int]  # (start, end) in source text


def extract_delegations(text: str) -> list[DelegateCall]:
    out: list[DelegateCall] = []
    for m in _DELEGATE_RE.finditer(text):
        out.append(
            DelegateCall(
                agent=m.group(1).strip(),
                prompt=m.group(2).strip(),
                span=m.span(),
            )
        )
    return out


def strip_delegations(text: str) -> str:
    return _DELEGATE_RE.sub("", text).strip()
