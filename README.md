# IVO

> **I**ntelligent **V**irtual **O**perator. A small, fast Telegram front-end for the GitHub Copilot CLI — chat with your projects (and your machine) from your phone, with the tools Copilot already ships.
>
> Repo: <https://github.com/albertleal/ivo>

## The problem it solves

You want to **drive your code and your machine from a phone**. Not just "ask
an LLM a question" — actually have it **read your repo, edit files, run
commands, search the web, and do real work** while you're away from the
laptop.

There are excellent terminal AI agents already, but most ship as **large,
multi-package projects** that bundle their own tool runtime, plugin system,
LSP layer, sub-agent framework, sandboxing, TUI, desktop apps, telemetry, and
weeks of release cadence — a lot of which **the GitHub Copilot CLI already
does** (file/edit/bash/grep/web/task tools, permissioning, MCP, model
management, multi-language support).

**IVO** does the opposite: it's a **thin shell** that pipes Telegram messages
into `copilot` (or `ollama launch copilot` for cloud Ollama models) and pipes
the streamed reply back. ~1k lines of Python total. No tool re-implementation,
no plugin marketplace, no daemon stack — just the parts the Copilot CLI
doesn't give you for free:

- a **Telegram surface** with slash-command model switching
- **voice in / voice out** (Whisper + Kokoro)
- a tiny **orchestrator** for skills + per-agent memory + sub-agent delegation
- a small **HTTP API** so other processes can push notifications through the bot

That's it. Everything heavy — the tools, the model gateway, the sandbox, the
safety prompts — stays inside the Copilot CLI process you already trust. If
GitHub ships a new tool tomorrow, you get it for free, with zero code changes
here.

Because Copilot can edit files, the system can also **build its own helpers**
on the fly — drop a script into the workspace and it's available next turn.

```
You: /opus  audit my dotfiles for anything sensitive that's not gitignored
Bot: 👀 ~/.config/git/config
     👀 ~/.bashrc
     ⚙️ git ls-files --others ~/code/*
     ✏️ ~/.gitignore
     Found 3 leaks. Patched .gitignore, added them to git-secrets baseline.

You: /qwen  read README.md and tell me which sections need updating
Bot: ⚙️ ls
     👀 README.md
     Sections X, Y, and Z reference deprecated paths…   (cloud Qwen via Ollama,
                                                          same Copilot toolbox)

You: /gema  summarize what this code does in plain Spanish
Bot: (streamed reply — pure chat, no tools needed)
```

## Why

- **Lightweight by design.** Single asyncio Python process, ~1k LOC. No
  bundled tool runtime, no plugin store, no TUI, no daemon, no telemetry.
  The Copilot CLI does the heavy lifting; ivo just routes.
- **Free upgrades.** When the Copilot CLI gains a new tool, model, or
  permission feature, ivo gets it next launch — no release here required.
- **One process, two surfaces.** Long-poll Telegram bot **and** outbound
  FastAPI (`POST /send`) in the same asyncio loop.
- **Auto-discovery.** On startup the bot asks each enabled adapter for its
  models and registers `/<alias>` commands dynamically.
- **Config-driven.** YAML for behavior, `.env` for secrets.
- **Voice in / voice out.** Whisper STT (via `whisper-cli`) and Kokoro TTS
  ship pre-wired; bilingual auto-detect ES/EN.
- **Skills + memory + sub-agents.** Optional thin orchestration layer that
  composes a system prompt from markdown skills, persists facts, and
  delegates to named sub-agents.
- **Workspace-aware.** Drop the bot into any project and it picks up
  `<workspace>/.github/agents/`, `<workspace>/.ivo/memory/`, and merges
  them on top of the bundled defaults.
- **Tools for every backend.** Copilot CLI models get tools natively; cloud
  Ollama models get them via `ollama launch copilot` (set
  `adapters.ollama.via_copilot: true`). One unified UX, two backends.

## Non-goals

To stay small and useful, ivo intentionally does **not** ship:

- its own tool runtime, sandbox, or permission UI — use Copilot's
- a plugin/marketplace system — add a markdown skill or sub-agent file instead
- a TUI or desktop app — the surfaces are Telegram and a small HTTP API
- model billing / proxying — you authenticate with Copilot / Ollama directly

## Install

Requires Python 3.11+ and (for voice) `whisper-cli` on `$PATH` plus the
Whisper / Kokoro model files.

```bash
git clone https://github.com/albertleal/ivo.git
cd ivo
make install            # creates .venv, installs deps, scaffolds .github/{agents,skills} and .ivo/memory
cp .env.example .env    # add TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
make run
```

## Layout

```
.github/
├── agents/           sub-agent definitions (markdown + frontmatter)
│   └── chat.agent.md   ← bundled default front-door agent
└── skills/           persona + style rules (markdown + meta.yaml)

.ivo/
└── memory/           per-agent markdown memory (gitignored, created at install)

ivo/
├── app.py            unified bot+api asyncio entry
├── config.py         pydantic, yaml + env substitution
├── orchestrator.py   skills + memory + agents coordinator
├── skills.py         skill loader
├── memory.py         file-based per-persona memory store
├── agents.py         sub-agent registry
├── bot/              long-poll, handlers, voice, transcribe
├── api/              FastAPI: /send, /health, /models
├── adapters/         base ABC + copilot + ollama
├── session/          per-user history (sqlite or json)
└── utils/            logging, helpers
```

## Configure

Everything that isn't a secret lives in `config.yaml`. See
[`config.example.yaml`](config.example.yaml) for the annotated full reference.
Minimum:

```yaml
telegram:
  token: ${TELEGRAM_BOT_TOKEN}
  admin_chat_id: ${TELEGRAM_CHAT_ID}

adapters:
  copilot: { enabled: true }
  ollama:  { enabled: false }

defaults:
  adapter: copilot
```

Secrets in `.env`:

```
TELEGRAM_BOT_TOKEN=123:abc
TELEGRAM_CHAT_ID=12345678
```

## Run

```bash
python -m ivo --config config.yaml   # plain
make run                                          # via Makefile
pm2 start ecosystem.config.js                     # via PM2 (optional)
```

The process boots, calls `discover_models()` on every enabled adapter,
registers slash commands, and starts both the long-poll loop and the FastAPI
server (port `8085` by default).

## HTTP API

The bot exposes a small outbound surface for other processes to push messages
through it or inspect discovered models. FastAPI auto-generates the schema:

- **Swagger UI**: <http://127.0.0.1:8085/docs>
- **ReDoc**: <http://127.0.0.1:8085/redoc>
- **OpenAPI JSON**: <http://127.0.0.1:8085/openapi.json>
- **Static schema**: [docs/openapi.json](docs/openapi.json) (regenerate with `make openapi`)

Endpoints:

| Method | Path       | Purpose                                          |
| ------ | ---------- | ------------------------------------------------ |
| GET    | `/health`  | Liveness + adapter list                          |
| GET    | `/models`  | All discovered (alias → model) commands          |
| POST   | `/send`    | Send a Telegram message via the bot's token      |

Example:

```bash
curl -s http://127.0.0.1:8085/send \
  -H 'content-type: application/json' \
  -d '{"text": "deploy succeeded"}'
```

### Access control

By default only loopback (`127.0.0.1`, `::1`) clients may call the API. Other
hosts get `403`. Override via `api.allowed_ips` in config:

```yaml
api:
  allowed_ips: ["127.0.0.1", "::1", "10.0.0.42"]   # explicit whitelist
  # allowed_ips: []                                # empty = open (trust your LAN)
```

## Built-in commands

- `/start` — greet, show workspace, agents, current model
- `/models` — list every discovered model grouped by provider
- `/<alias>` — switch model (e.g. `/opus`, `/gema`)
- `/agents` — list available sub-agents
- `/voice` — toggle voice replies
- `/stop` — interrupt the current turn (also auto-fires when you send a new message mid-reply)
- `/clear` — clear chat history for your user

Anything else is treated as chat and routed through the orchestrator.

## Sample tasks

Things you can actually send from Telegram and have it complete unattended:

- **Codebase work** — "audit `src/` for unused imports and open a PR",
  "port `legacy.py` to async", "explain why CI failed on the last commit".
- **Personal sysadmin** — "check disk usage and clean Homebrew caches if
  free space is below 10 GB", "diff my dotfiles vs the last backup",
  "rotate the API key in `~/.env` and update every project that uses it".
- **Research / web** — "summarize the top three results for `ollama tool
  calling` and quote the API shape", "check if package `foo` published a
  new minor version this week".
- **Quick ops** — "deploy is failing — pull the last 200 lines of
  `pm2 logs api` and tell me what's wrong", "run the test suite and report
  back only the failures".

## How it works

```
   Telegram ──► Bot handler ──► Orchestrator ──► Adapter ──► Reply
                                  │
                                  ├── Skills    (persona + telegram-style + humanize + …)
                                  ├── Memory    (file-based, per persona)
                                  └── Agents    (front-door + sub-agents on demand)
```

Every text message goes through `Orchestrator.handle()`:

1. Loads the front-door agent (default: bundled `chat`, override via
   `agents.front_door`).
2. Composes the system prompt: skills + `<memory>…</memory>` + agent prompt.
3. Adds the bounded session history.
4. Streams the adapter's reply.
5. Strips `<remember>fact</remember>` blocks and appends to the memory file.
6. Detects `<delegate to="agent">prompt</delegate>` blocks and recursively
   invokes the named sub-agent, splicing the result back in.

Recursion is depth-capped (default `3`) and loop-guarded.

## Bundled agent

`.github/agents/chat.agent.md` is the default front-door agent — friendly,
concise, plain-text, with the memory and delegation protocols enabled. It's
loaded automatically when no workspace agent overrides it.

## Workspace mode

When `agents.workspace_path` is set, the bot scans that path's
`.github/agents/*.md` and merges those agents on top of the bundled set.
Workspace agents **override** bundled ones with the same stem.

The default value is `~` — your home directory. A personal
`~/.github/agents/` is therefore picked up automatically. Set
`workspace_path` to a project root to bind the bot to that repo, or to
`null` for bundled-only.

When `memory.use_workspace: true` (and `workspace_path` is set), memory
files live in `<workspace_path>/.ivo/memory/` instead of the bundled
`.ivo/memory/`. Useful when the host project already keeps long-form
agent memory there.

## Customize the persona

Edit `.github/skills/personality.md`. That's it. Restart to pick up changes.

The other shipped skills:

- `telegram-style.md` — Telegram-channel formatting rules (chat trigger)
- `voice-style.md` — voice-reply rules (voice trigger)
- `humanize.md` — anti-slop, push-back-when-wrong rules
- `attachments.md` — handling user-attached files
- `memory-maintenance.md` — when/how to write `<remember>` blocks
- `meta.yaml` — index: which skills auto-load, which are
  trigger-conditional (`chat`, `voice`, `on_command:foo`)

## Add a sub-agent

Drop a markdown file in `.github/agents/` (bundled) or in your workspace's
`.github/agents/`:

```markdown
---
name: sql-helper
description: Translates plain English to SQL.
adapter: copilot
model: claude-sonnet-4.6
system_prompt_inline: |
  You take a natural-language query and return a single SQL statement…
tools: []
---
```

Restart. The front-door agent can now write
`<delegate to="sql-helper">give me last week's signups</delegate>` and the
orchestrator will route, run, and splice the result.

> ⚠️ Each delegation is a full LLM round-trip. Keep `max_delegation_depth`
> low (default 3) until you trust the front-door agent to delegate sparingly.

## Memory

File-based, one markdown file per agent under `.ivo/memory/<agent>.md`.
The orchestrator:

- Reads the file before every turn and injects the last `max_chars` (default
  4000) characters as `<memory>…</memory>` in the system prompt.
- Appends each `<remember>…</remember>` block the LLM emits.

To wipe: `rm .ivo/memory/<agent>.md`. With `memory.per_user: true`, the
file becomes `.ivo/memory/<agent>_<chat_id>.md` (one per Telegram user).

The directory is gitignored — only `.gitkeep` is committed.

## Add your own adapter

```python
# ivo/adapters/my_provider.py
from .base import Adapter, ModelInfo, Message

class MyProvider(Adapter):
    name = "myprovider"

    async def discover_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="my-model-1", display_name="My Model 1",
                          slash_alias="mine", provider=self.name)]

    async def chat(self, model, messages):
        yield "hello from my provider"

    async def health(self) -> bool:
        return True
```

Register it in `ivo/adapters/__init__.py` and add a section
under `adapters:` in your config. Done.

## Topics

Suggested GitHub topics (mirror these on a fork to inherit search traffic):

`telegram-bot` · `copilot-cli` · `github-copilot` · `ai-agent` ·
`ai-assistant` · `coding-agent` · `llm` · `claude` · `ollama` · `anthropic` ·
`openai` · `mcp` · `agentic` · `python` · `asyncio` · `self-hosted` ·
`voice-assistant` · `whisper` · `tts` · `developer-tools`

## License

MIT. See [`LICENSE`](LICENSE).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).
