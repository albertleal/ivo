"""File-based memory store.

One markdown file per persona under `memory/<name>.md`. Read before each turn,
appended to via the `<remember>...</remember>` protocol the LLM emits inline.

Atomic writes: write to a temp file in the same dir, then rename.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from threading import Lock

_REMEMBER_RE = re.compile(r"<remember>(.*?)</remember>", re.DOTALL | re.IGNORECASE)


class MemoryStore:
    """Per-persona markdown memory with atomic writes.

    If ``workspace_path`` is provided and ``use_workspace`` is True, memory
    lives in ``<workspace_path>/.ivo/memory/`` instead of ``memory_dir``.
    Lets a host project share its own memory dir with the bot.
    """

    def __init__(
        self,
        memory_dir: Path | str,
        max_chars: int = 4000,
        *,
        workspace_path: Path | str | None = None,
        use_workspace: bool = False,
    ) -> None:
        if use_workspace and workspace_path:
            self.dir = Path(workspace_path).expanduser() / ".ivo" / "memory"
        else:
            self.dir = Path(memory_dir).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_chars = max_chars
        self._lock = Lock()

    # ── path helpers ─────────────────────────────────────────────────────────

    def _path(self, name: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        return self.dir / f"{safe}.md"

    # ── public api ───────────────────────────────────────────────────────────

    def read(self, name: str) -> str:
        path = self._path(name)
        if not path.exists():
            return ""
        text = path.read_text()
        if len(text) > self.max_chars:
            # keep the tail (most recent)
            text = text[-self.max_chars :]
        return text

    def append(self, name: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            existing = ""
            path = self._path(name)
            if path.exists():
                existing = path.read_text()
            joined = (existing.rstrip() + "\n- " + text + "\n") if existing else f"- {text}\n"
            self._atomic_write(path, joined)

    def replace_section(self, name: str, heading: str, content: str) -> None:
        """Replace (or insert) a `## heading` section in the persona memory."""
        with self._lock:
            path = self._path(name)
            existing = path.read_text() if path.exists() else ""
            new_section = f"## {heading}\n{content.strip()}\n"

            pattern = re.compile(
                rf"^##\s+{re.escape(heading)}\s*\n.*?(?=^##\s|\Z)",
                re.DOTALL | re.MULTILINE,
            )
            if pattern.search(existing):
                updated = pattern.sub(new_section, existing)
            else:
                updated = (existing.rstrip() + "\n\n" + new_section) if existing else new_section
            self._atomic_write(path, updated)

    def _atomic_write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".memtmp.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ── protocol parsing ────────────────────────────────────────────────────────


def extract_remember(text: str) -> tuple[str, list[str]]:
    """Strip <remember>…</remember> blocks from text, return (clean, facts)."""
    facts = [m.strip() for m in _REMEMBER_RE.findall(text) if m.strip()]
    clean = _REMEMBER_RE.sub("", text).strip()
    # collapse double-blank lines created by removal
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean, facts
