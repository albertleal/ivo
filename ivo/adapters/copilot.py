"""Copilot CLI adapter.

Shells out to the `copilot` binary. Auto-discovery is best-effort: the CLI
does not expose a stable `--list-models` endpoint, so we seed the model list
from the `aliases` section of the config (overridable per release).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator
from typing import Any

from .base import Adapter, Message, ModelInfo, StatusCb

log = logging.getLogger("adapter.copilot")


# Throttle status callbacks so we don't spam Telegram editMessage.
STATUS_COOLDOWN = 1.5


def _format_status(data: dict) -> str | None:
    """Convert a `tool.execution_start` event into a short status string."""
    tool_name = data.get("toolName", "")
    description = data.get("description", "")
    args = data.get("arguments", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    if tool_name == "report_intent":
        intent = args.get("intent", "")
        return f"🤖 {intent}" if intent else None

    if tool_name in ("sql", "task_complete"):
        return None

    icons = {
        "view": "👀",
        "edit": "✏️",
        "create": "📝",
        "bash": "⚙️",
        "grep": "🔍",
        "glob": "🔍",
        "task": "🤖",
    }
    icon = icons.get(tool_name, "⚙️")

    if tool_name == "bash":
        desc = args.get("description", description)
        return f"{icon} {desc}" if desc else f"{icon} running command"
    if tool_name == "task":
        desc = args.get("description", description)
        return f"{icon} {desc}" if desc else f"{icon} sub-task"

    path = args.get("path", "")
    if path:
        return f"{icon} {os.path.basename(path)}"

    return f"{icon} {description or tool_name}"


def _extract_text(events: list[dict]) -> str:
    """Stitch the final assistant text out of a parsed JSONL event stream."""
    messages: list[str] = []
    reasoning_fallback: list[str] = []
    summary: str | None = None
    for ev in events:
        etype = ev.get("type", "")
        data = ev.get("data", {})
        if etype == "assistant.message":
            content = data.get("content", "")
            if content and content.strip():
                messages.append(content.strip())
            else:
                # Cloud models (qwen-cloud, deepseek-cloud) sometimes put the
                # reply in reasoningText/encryptedContent. Keep these aside
                # and only use them if no real `content` event ever shows up;
                # otherwise we'd leak chain-of-thought from reasoning models
                # like Claude/Opus that emit a separate reasoning event.
                rt = data.get("reasoningText", "") or data.get("encryptedContent", "")
                if rt and rt.strip():
                    reasoning_fallback.append(rt.strip())
        elif etype == "session.task_complete":
            s = data.get("summary", "")
            if s and s.strip():
                summary = s.strip()
    if not messages and reasoning_fallback:
        messages = reasoning_fallback
    # Drop short intermediate "status" messages the Copilot CLI emits between
    # tool calls (e.g. "Working on it…"), but only when there is at least one
    # substantive message to keep. Otherwise a legitimately short final reply
    # like "Hola" or "Hello!" (common from ollama-copilot one-shots) would be
    # silently swallowed and surfaced as an empty reply.
    long_parts = [m for m in messages if len(m) > 20]
    parts = long_parts if long_parts else list(messages)
    if summary and (not parts or parts[-1] != summary):
        parts.append(summary)
    return "\n\n".join(parts)


class CopilotAdapter(Adapter):
    name = "copilot"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.binary: str = self.options.get("binary", "copilot")
        self.default_model: str = self.options.get("default_model", "")
        self.aliases: dict[str, str] = dict(self.options.get("aliases", {}))
        # Optional working directory for the `copilot` subprocess. Useful so
        # the CLI loads a specific repo's context (.github/agents, files…).
        self.cwd: str | None = self.options.get("cwd") or None

    # ── discovery ────────────────────────────────────────────────────────────

    async def discover_models(self) -> list[ModelInfo]:
        if not self._binary_available():
            log.warning("copilot binary %r not on PATH; reporting configured aliases anyway", self.binary)
        models: list[ModelInfo] = []
        for alias, model_id in self.aliases.items():
            models.append(
                ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    slash_alias=alias,
                    provider=self.name,
                )
            )
        return models

    def _binary_available(self) -> bool:
        return shutil.which(self.binary) is not None

    async def health(self) -> bool:
        return self._binary_available()

    # ── chat ─────────────────────────────────────────────────────────────────

    async def chat(
        self,
        model: str,
        messages: list[Message],
        status_cb: StatusCb = None,
    ) -> AsyncIterator[str]:
        """Run a copilot prompt and stream tool-use status + final reply.

        Uses `--output-format json` so we can surface live status updates
        (👀 reading, ✏️ editing, ⚙️ running command…) via ``status_cb`` while
        the CLI works. The full assistant text is yielded once at the end.
        """
        prompt = self._render_prompt(messages)
        cmd = [
            self.binary,
            "--model", model,
            "--output-format", "json",
            # Autopilot: skip per-call permission prompts. The CLI's
            # default sandbox/credential model still applies.
            "--allow-all",
            "-p", prompt,
        ]
        log.debug("copilot cmd: %s (cwd=%s)", cmd, self.cwd)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
        )

        raw_lines: list[str] = []
        last_status_at = 0.0
        assert proc.stdout is not None
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line:
                    continue
                raw_lines.append(line)
                if not status_cb:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "tool.execution_start":
                    continue
                now = time.monotonic()
                if (now - last_status_at) < STATUS_COOLDOWN:
                    continue
                msg = _format_status(ev.get("data", {}))
                if not msg:
                    continue
                last_status_at = now
                try:
                    await status_cb(msg)
                except Exception:
                    log.debug("copilot status_cb failed", exc_info=True)
        except asyncio.CancelledError:
            # User stopped the turn (or sent a new message). Kill the CLI
            # subprocess so it does not keep working in the background.
            log.info("copilot run cancelled — killing subprocess")
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            with contextlib.suppress(Exception):
                await proc.wait()
            raise
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            try:
                _, stderr = await proc.communicate()
            except Exception:
                stderr = b""

        if proc.returncode and proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            yield f"[copilot error rc={proc.returncode}] {err}"
            return

        events: list[dict] = []
        for line in raw_lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        text = _extract_text(events)
        if text:
            yield text

    @staticmethod
    def _render_prompt(messages: list[Message]) -> str:
        parts: list[str] = []
        for m in messages:
            if m.role == "system":
                parts.append(f"[system] {m.content}")
            elif m.role == "user":
                parts.append(f"[user] {m.content}")
            else:
                parts.append(f"[assistant] {m.content}")
        return "\n\n".join(parts)

