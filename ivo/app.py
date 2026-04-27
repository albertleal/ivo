"""Unified entrypoint: bot + API in one asyncio loop."""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from .adapters import Adapter, ModelInfo, build_adapters
from .agents import AgentRegistry
from .bot import BotContext, build_catalog
from .bot.poller import run_polling
from .config import Config
from .memory import MemoryStore
from .orchestrator import Orchestrator, OrchestratorConfig
from .session import SessionStore
from .skills import SkillManager

log = logging.getLogger("app")


async def _discover_all(adapters: dict[str, Adapter]) -> dict[str, list[ModelInfo]]:
    """Run discover_models() on every adapter concurrently."""
    names = list(adapters)
    results = await asyncio.gather(
        *(adapters[n].discover_models() for n in names),
        return_exceptions=True,
    )
    out: dict[str, list[ModelInfo]] = {}
    for name, r in zip(names, results, strict=True):
        if isinstance(r, BaseException):
            log.error("discovery failed for %s: %s", name, r)
            out[name] = []
        else:
            out[name] = r
            log.info("%s discovered %d models", name, len(r))
    return out


def _resolve_default_model(
    cfg: Config,
    catalog: dict,
) -> str:
    """Pick an initial model id for new sessions."""
    # Prefer the first model from the configured default adapter.
    for cmd in catalog.values():
        if cmd.provider == cfg.defaults.adapter:
            return cmd.model_id
    # Last resort: the first thing we discovered.
    if catalog:
        return next(iter(catalog.values())).model_id
    return ""


async def run(cfg: Config) -> None:
    """Boot adapters, build context, run bot + api together."""
    # 1. Build adapters from config.
    adapters = build_adapters(cfg.adapters)
    if not adapters:
        raise SystemExit("error: no adapters enabled in config.adapters")

    # 2. Discover models.
    discovered = await _discover_all(adapters)
    total = sum(len(v) for v in discovered.values())
    if total == 0:
        raise SystemExit(
            "error: no models discovered from any enabled adapter. "
            "Check copilot binary on PATH and/or Ollama at the configured host."
        )
    log.info("discovered %d models total", total)

    # 3. Build catalog + session store.
    catalog = build_catalog(adapters, discovered)
    sessions = SessionStore(
        backend=cfg.session.backend,
        path=cfg.session.path if cfg.session.backend != "memory" else None,
        default_adapter=cfg.defaults.adapter,
        default_model=_resolve_default_model(cfg, catalog),
    )
    ctx = BotContext(config=cfg, adapters=adapters, catalog=catalog, sessions=sessions)

    # 4. Build the orchestration layer (skills + memory + agents).
    try:
        skills = SkillManager(
            cfg.skills.dir,
            auto_load=cfg.skills.auto_load or None,
        )
        memory = MemoryStore(
            cfg.memory.dir,
            max_chars=cfg.memory.max_chars,
            workspace_path=cfg.agents.workspace_path,
            use_workspace=cfg.memory.use_workspace,
        )
        registry = AgentRegistry(
            cfg.agents.dir,
            workspace_path=cfg.agents.workspace_path,
        )
        ctx.agent_names = list(registry.agents)
        orch_cfg = OrchestratorConfig(
            front_door_agent=cfg.agents.front_door,
            max_delegation_depth=cfg.agents.max_delegation_depth,
            delegation_mode=cfg.agents.delegation_mode,
            per_user_memory=cfg.memory.per_user,
        )
        ctx.orchestrator = Orchestrator(
            adapters=adapters,
            sessions=sessions,
            skills=skills,
            memory=memory,
            registry=registry,
            cfg=orch_cfg,
        )
        log.info(
            "orchestrator ready: %d skills, %d agents, front_door=%s",
            len(skills.skills),
            len(registry.agents),
            cfg.agents.front_door,
        )
    except Exception as e:
        log.warning("orchestrator unavailable, falling back to direct adapter: %s", e)
        ctx.orchestrator = None

    # 5. Optional admin notification — same content as /start.
    if cfg.telegram.admin_chat_id:
        try:
            from telegram import Bot

            from .bot.handlers import handle_start
            boot_text = await handle_start(ctx, cfg.telegram.admin_chat_id)
            bot = Bot(cfg.telegram.token)
            await bot.send_message(
                chat_id=cfg.telegram.admin_chat_id,
                text=boot_text,
            )
        except Exception as e:
            log.warning("admin notification failed: %s", e)

    # 6. Run bot + (optionally) api together.
    tasks: list[asyncio.Task] = [asyncio.create_task(run_polling(ctx), name="bot")]

    if cfg.api.enable:
        from .api import build_app
        api = build_app(ctx)
        ucfg = uvicorn.Config(
            api,
            host=cfg.api.host,
            port=cfg.api.port,
            log_level=cfg.logging.level.lower(),
        )
        server = uvicorn.Server(ucfg)
        tasks.append(asyncio.create_task(server.serve(), name="api"))

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
