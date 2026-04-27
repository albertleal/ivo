"""Ollama adapter.

Two operating modes:

1. **Direct** (default) — talks to a local/remote Ollama daemon via HTTP:
     - `GET  /api/tags`  → discover installed models
     - `POST /api/chat`  → stream a chat response (NDJSON)
   Plain chat only; no tools.

2. **via_copilot** — runs `ollama launch copilot --model <id> --yes -- …`
   under the hood. This gives the Ollama model GitHub Copilot CLI's full
   tool runtime (file/bash/grep/web/task) and emits the same JSONL event
   stream we already parse in the Copilot adapter, including live status
   events for `status_cb`.
   See https://docs.ollama.com/integrations/copilot-cli
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import Adapter, Message, ModelInfo, StatusCb
from .copilot import STATUS_COOLDOWN, _extract_text, _format_status

log = logging.getLogger("adapter.ollama")


def _alias_for(model_id: str) -> str:
    """Default alias = first word of id, lower-snake-case."""
    head = model_id.split(":", 1)[0]
    head = re.sub(r"[^a-z0-9]+", "_", head.lower()).strip("_")
    return head or model_id.replace(":", "_")


class OllamaAdapter(Adapter):
    name = "ollama"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.host: str = self.options.get("host", "http://localhost:11434").rstrip("/")
        self.aliases: dict[str, str] = dict(self.options.get("aliases", {}))
        self._timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
        # When True, route chat() through `ollama launch copilot --model …`
        # so the model gets Copilot CLI's tools (file/bash/web/grep/task).
        self.via_copilot: bool = bool(self.options.get("via_copilot", False))
        self.ollama_binary: str = self.options.get("ollama_binary", "ollama")
        # Optional cwd for the `ollama launch copilot` subprocess (so the
        # CLI loads a specific repo's context, mirroring CopilotAdapter.cwd).
        self.cwd: str | None = self.options.get("cwd") or None

    # ── discovery ────────────────────────────────────────────────────────────

    async def discover_models(self) -> list[ModelInfo]:
        # via_copilot path: trust the alias map (cloud ids may not be in
        # /api/tags yet — `ollama launch copilot --yes` auto-pulls them).
        if self.via_copilot and self.aliases:
            return [
                ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    slash_alias=alias,
                    provider=self.name,
                )
                for alias, model_id in self.aliases.items()
            ]

        models: list[ModelInfo] = []
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(f"{self.host}/api/tags")
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            log.error("ollama discovery failed against %s: %s", self.host, e)
            return []

        # Reverse map: model_id -> alias. When `aliases` is non-empty, ONLY
        # models listed in the alias map are exposed — the user controls
        # cloud vs. local by which ids they pin in config.
        override = {v: k for k, v in self.aliases.items()}
        gated = bool(self.aliases)

        for item in data.get("models", []):
            model_id = item.get("name") or item.get("model")
            if not model_id:
                continue
            if gated and model_id not in override:
                continue
            alias = override.get(model_id) or _alias_for(model_id)
            models.append(
                ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    slash_alias=alias,
                    provider=self.name,
                )
            )
        return models

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.get(f"{self.host}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    # ── chat ─────────────────────────────────────────────────────────────────

    async def chat(
        self,
        model: str,
        messages: list[Message],
        status_cb: StatusCb = None,
    ) -> AsyncIterator[str]:
        if self.via_copilot:
            async for chunk in self._chat_via_copilot(model, messages, status_cb):
                yield chunk
            return

        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", f"{self.host}/api/chat", json=payload) as r:
                    if r.status_code >= 400:
                        body_bytes = b""
                        try:
                            async for chunk in r.aiter_bytes():
                                body_bytes += chunk
                                if len(body_bytes) > 4096:
                                    break
                        except Exception:
                            pass
                        body = body_bytes.decode("utf-8", errors="replace").strip()
                        log.error(
                            "ollama %s for model=%s body=%s", r.status_code, model, body
                        )
                        # Friendly hint for the known Gemma-cloud failure mode.
                        hint = ""
                        if r.status_code == 500 and "gemma" in model.lower():
                            hint = (
                                " — Gemma cloud sometimes 500s on long system prompts."
                                " Try /gpt, /qwen, or /deepseek, or shorten memory."
                            )
                        yield f"[ollama {r.status_code}] {body or 'no body'}{hint}"
                        return
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        chunk = evt.get("message", {}).get("content")
                        if chunk:
                            yield chunk
                        if evt.get("done"):
                            break
        except Exception as e:
            log.error("ollama chat failed: %s", e)
            yield f"[ollama error] {e}"

    # ── chat (via copilot CLI) ───────────────────────────────────────────────

    async def _chat_via_copilot(
        self,
        model: str,
        messages: list[Message],
        status_cb: StatusCb,
    ) -> AsyncIterator[str]:
        """Run `ollama launch copilot --model <id> --yes -- … -p <prompt>`.

        Mirrors CopilotAdapter.chat() — parses the same JSONL event stream,
        forwards `tool.execution_start` events to ``status_cb``, and yields
        the final assistant text once the subprocess exits.
        """
        if shutil.which(self.ollama_binary) is None:
            yield f"[ollama error] {self.ollama_binary!r} not on PATH"
            return

        prompt = self._render_prompt_for_copilot(messages)
        cmd = [
            self.ollama_binary, "launch", "copilot",
            "--model", model,
            "--yes",
            "--",
            "--output-format", "json",
            "--allow-all",
            "-p", prompt,
        ]
        log.debug("ollama launch copilot cmd: %s (cwd=%s)", cmd, self.cwd)

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
                    log.debug("ollama-copilot status_cb failed", exc_info=True)
        except asyncio.CancelledError:
            log.info("ollama-copilot run cancelled — killing subprocess")
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
            yield f"[ollama-copilot error rc={proc.returncode}] {err}"
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
    def _render_prompt_for_copilot(messages: list[Message]) -> str:
        parts: list[str] = []
        for m in messages:
            if m.role == "system":
                parts.append(f"[system] {m.content}")
            elif m.role == "user":
                parts.append(f"[user] {m.content}")
            else:
                parts.append(f"[assistant] {m.content}")
        return "\n\n".join(parts)
