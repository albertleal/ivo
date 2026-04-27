"""Atomic smoke test: ollama via_copilot really gets Copilot CLI tools.

Skipped unless RUN_OLLAMA_COPILOT=1 (it spawns a real subprocess and hits
Ollama cloud). Pick the model with OLLAMA_COPILOT_MODEL (default kimi-k2.5:cloud).

Usage:
    RUN_OLLAMA_COPILOT=1 pytest tests/test_ollama_via_copilot.py -s
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from ivo.adapters import Message
from ivo.adapters.ollama import OllamaAdapter

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_COPILOT") != "1",
    reason="opt-in: set RUN_OLLAMA_COPILOT=1 to run (spawns real ollama+copilot)",
)
@pytest.mark.skipif(
    shutil.which("ollama") is None or shutil.which("copilot") is None,
    reason="needs both `ollama` and `copilot` on PATH",
)
def test_via_copilot_lists_repo_files() -> None:
    """Model must use the file/bash tool to list the repo root."""
    model = os.environ.get("OLLAMA_COPILOT_MODEL", "kimi-k2.5:cloud")

    adapter = OllamaAdapter(options={
        "via_copilot": True,
        "cwd": str(REPO_ROOT),
        "aliases": {"kimi": model},
    })

    statuses: list[str] = []

    async def status_cb(s: str) -> None:
        statuses.append(s)
        print(f"  [status] {s}")

    prompt = (
        "List the top-level files and folders in the current working "
        "directory by running an actual tool (ls or equivalent). "
        "Then reply with ONLY a comma-separated list of names you actually "
        "saw. Do not invent any."
    )

    async def run() -> str:
        chunks: list[str] = []
        async for c in adapter.chat(
            model=model,
            messages=[Message(role="user", content=prompt)],
            status_cb=status_cb,
        ):
            chunks.append(c)
        return "".join(chunks)

    reply = asyncio.run(run())
    print("\n=== reply ===\n", reply, "\n=============")
    print(f"=== {len(statuses)} status events ===")

    # Real artifacts that exist at repo root.
    expected_any = ("pyproject.toml", "ivo", "Makefile", "README.md")
    hits = [name for name in expected_any if name in reply]
    assert hits, (
        f"reply did not mention any real repo file from {expected_any!r}; "
        f"model likely had no tool access. reply={reply!r}"
    )
    # If status_cb fired at least once, tools were actively used.
    # (Not strictly required — a single bash listing may finish under the
    # 1.5s STATUS_COOLDOWN window.)
    print(f"OK: matched {hits}, statuses={statuses}")
