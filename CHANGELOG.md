# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-04-28

Initial scaffold.

### Core

- Unified asyncio process running long-poll Telegram bot + FastAPI HTTP API
  in the same loop (`ivo.app`).
- `Adapter` ABC (`ivo.adapters.base.Adapter`) with first-class
  implementations:
  - **Copilot** — shells out to `copilot` CLI; model list seeded from config
    aliases (CLI does not expose a stable list endpoint). `--allow-all` is
    always passed (autopilot for the agent loop).
  - **Ollama** — `httpx` client against `/api/tags` for discovery and
    `/api/chat` for streaming. When `aliases` is non-empty in config, only
    those models are exposed (the user pins cloud vs. local ids by hand).
- Auto-discovery of models on startup; bot fails fast if no adapter
  discovers any model.
- Dynamic `/<alias>` slash-command registration per discovered model.
- Built-in commands: `/start`, `/models`, `/agents`, `/voice`,
  `/stop`, `/clear`, `/<alias>`.
- Per-user session store (SQLite or JSON). Chat history is kept in process
  memory only — Copilot CLI sessions own the conversation context.
- Pydantic config layer (`config.yaml` + `.env` substitution).
- `Makefile`, `scripts/install.sh`, `pyproject.toml`, ruff + pytest config.

### HTTP API

- FastAPI surface: `GET /health`, `GET /models`, `POST /send`.
- Auto-generated OpenAPI schema at `/openapi.json`, Swagger UI at `/docs`,
  ReDoc at `/redoc`. Static copy committed at `docs/openapi.json` —
  regenerate with `make openapi`.
- Loopback-only by default. `api.allowed_ips` whitelist controls access;
  empty list disables the gate (open to anyone reachable on the LAN).

### Voice

- Voice in: `whisper-cli` (whisper.cpp) for OGG → text.
- Voice out: in-process Kokoro ONNX TTS, bilingual ES/EN auto-detect, no
  audio leaves the host.
- `kokoro-onnx` and `soundfile` are declared as runtime dependencies, so
  voice replies work out of the box once the model files are reachable.

### Image / file ingestion

- Photos and image documents (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`)
  sent to the bot are downloaded to `UPLOADS_DIR` (default
  `${TMPDIR}/ivo/`) with a deterministic filename derived
  from the Telegram `file_id`.
- The orchestrator receives a prompt that quotes the saved absolute path
  and the user's caption, instructing the agent to use its `view_image`
  tool. End-to-end test in `tests/test_images.py`.

### Orchestration

- **Skills** (`.github/skills/` + `SkillManager`). Markdown files indexed
  by `meta.yaml`. Auto-load defaults plus trigger-conditional skills
  (`chat`, `voice`, `on_command:<name>`). Ships with `personality`,
  `humanize`, `telegram-style`, `voice-style`, `attachments`,
  `memory-maintenance`.
- **Memory** (`.ivo/memory/` + `MemoryStore`). Per-persona markdown
  files with atomic writes. The LLM persists facts via inline
  `<remember>…</remember>` blocks; the orchestrator strips them from the
  reply and appends them.
- **Agents** (`.github/agents/` + `AgentRegistry`). Markdown agent
  definitions with YAML frontmatter (name, adapter, model, system prompt,
  tools).
  - Ships **`chat.agent.md`** — the default generic front-door agent. It
    loads automatically when no workspace agent overrides it, so the bot
    is useful immediately on a clean install.
- **Delegation protocol**: `<delegate to="agent">prompt</delegate>`
  triggers recursive sub-agent calls. Depth-capped (default 3),
  loop-guarded, results spliced or replaced per config.

### Workspace mode

- `agents.workspace_path` defaults to `~` (user home), so a personal
  `~/.github/agents/` is auto-discovered. Set to a project root to bind
  the bot to that repo, or `null` for bundled-only.
- Workspace agents merged on top of bundled ones; same-stem files win.
- `memory.use_workspace: true` redirects memory writes to
  `<workspace_path>/.ivo/memory/` so the bot shares persistent state
  with whatever lives there.
- `~` is expanded everywhere paths are accepted (skills, memory, agents,
  workspace).

### Layout

All bundled persona/skills/agents live under `.github/` — same shape the
bot looks for inside any host workspace. Per-deployment memory lives
separately under `.ivo/memory/` so it never collides with a host
project's own `.github/`.

```
.github/
├── agents/chat.agent.md
└── skills/{meta.yaml,personality.md,humanize.md,…}
.ivo/
└── memory/.gitkeep
```

### Tests

- 32 passing. Coverage: agents (registry + workspace overlay + `~`
  expansion), skills (loader + triggers + auto_load override), memory
  (atomic writes + workspace redirect + `<remember>` parsing), config
  (env substitution), API (CRUD + IP gate + OpenAPI schema), images
  (download + path-aware orchestrator handoff, end-to-end).

### Interrupts

- `/stop` cancels the current turn (kills the underlying `copilot`
  subprocess, no orphan work). Sending a new message while a turn is in
  flight auto-cancels the previous one — same UX as VS Code's stop button.
  The user always receives a clear "🛑 stopped — what's next?" notice
  (text only, no empty voice notes).

### Stubbed / out of scope

- Token-streamed Telegram edits: `chat()` is async-iterable, but the bot
  currently stitches the full reply before sending.
- Copilot CLI true model auto-discovery: the CLI does not expose a stable
  `--list-models`, so the adapter relies on the configured alias map.
