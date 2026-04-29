"""Orchestration layer.

The Telegram bot used to call `adapter.chat()` directly. With the orchestrator
in front, every turn becomes:

    user msg ──► Orchestrator.handle()
                 1. Compose system prompt
                    = persona skills + memory snapshot + agent system_prompt
                 2. Append session history
                 3. Stream the adapter
                 4. Parse <remember>…</remember> → memory.append, strip from reply
                 5. Parse <delegate to="x">…</delegate> → recursive sub-agent call
                    (capped depth, loop-guarded)
                 6. Return final user-visible text

The orchestrator is adapter-agnostic — it reuses whatever Adapter the front-door
agent points to (or falls back to the session's active adapter/model).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .adapters import Adapter, Message
from .agents import AgentRegistry, extract_delegations, strip_delegations
from .memory import MemoryStore, extract_remember
from .session import SessionStore
from .skills import SkillManager

StatusCb = Callable[[str], Awaitable[None]] | None

log = logging.getLogger("orchestration")


@dataclass
class OrchestratorConfig:
    front_door_agent: str = "assistant"
    max_delegation_depth: int = 3
    delegation_mode: str = "splice"  # "splice" | "replace"
    per_user_memory: bool = False


class Orchestrator:
    """Coordinates skills + memory + agents + adapters per turn."""

    def __init__(
        self,
        adapters: dict[str, Adapter],
        sessions: SessionStore,
        skills: SkillManager,
        memory: MemoryStore,
        registry: AgentRegistry,
        cfg: OrchestratorConfig,
    ) -> None:
        self.adapters = adapters
        self.sessions = sessions
        self.skills = skills
        self.memory = memory
        self.registry = registry
        self.cfg = cfg

    # ── public api ───────────────────────────────────────────────────────────

    async def handle(
        self,
        user_id: int,
        text: str,
        *,
        triggers: list[str] | None = None,
        status_cb: StatusCb = None,
    ) -> str:
        """Top-level entry point used by the bot's message handler."""
        triggers = triggers or ["chat"]
        # Per-session agent override wins over config front-door.
        sess = self.sessions.get(user_id)
        agent_name = sess.agent or self.cfg.front_door_agent
        agent = self.registry.get(agent_name)
        if agent is None:
            return f"[orchestrator] front-door agent '{agent_name}' not found"

        # Persist user message in session history (so /reset still works).
        self.sessions.append(user_id, Message(role="user", content=text))
        sess = self.sessions.get(user_id)

        memory_key = self._memory_key(agent_name, user_id)
        # Front-door overrides: respect the user's /alias choice over any
        # adapter/model pinned in the agent file.
        override_adapter = sess.adapter or None
        override_model = sess.model or None
        reply = await self._run_agent(
            agent_name=agent_name,
            user_text=text,
            history=list(sess.history[:-1]),  # exclude the just-appended user msg, we pass it as prompt
            triggers=triggers,
            depth=0,
            memory_key=memory_key,
            include_memory=True,
            visited=set(),
            status_cb=status_cb,
            override_adapter=override_adapter,
            override_model=override_model,
        )

        # Append assistant reply to session.
        self.sessions.append(user_id, Message(role="assistant", content=reply))
        return reply

    # ── core ────────────────────────────────────────────────────────────────

    async def _run_agent(
        self,
        agent_name: str,
        user_text: str,
        history: list[Message],
        triggers: list[str],
        depth: int,
        memory_key: str,
        include_memory: bool,
        visited: set[str],
        status_cb: StatusCb = None,
        override_adapter: str | None = None,
        override_model: str | None = None,
    ) -> str:
        async def _emit(msg: str) -> None:
            if status_cb is None:
                return
            try:
                await status_cb(msg)
            except Exception:  # pragma: no cover — never let status break a turn
                log.debug("status_cb failed", exc_info=True)
        agent = self.registry.get(agent_name)
        if agent is None:
            return f"[orchestrator] agent '{agent_name}' not found"

        if depth > self.cfg.max_delegation_depth:
            log.warning("delegation depth cap hit at %s", agent_name)
            return f"[orchestrator] delegation depth cap reached at '{agent_name}'"

        if agent_name in visited:
            log.warning("delegation loop detected on %s", agent_name)
            return f"[orchestrator] loop on agent '{agent_name}'"

        visited = visited | {agent_name}

        # Resolve adapter + model. Caller-supplied overrides win over the
        # agent's pinned values (so /alias takes effect for the front door).
        adapter_name = override_adapter or agent.adapter or next(iter(self.adapters), "")
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            return f"[orchestrator] adapter '{adapter_name}' not loaded"
        model = override_model or agent.model or self._fallback_model(adapter_name)
        if not model:
            return f"[orchestrator] no model resolved for adapter '{adapter_name}'"

        # Compose system prompt.
        skill_block = self.skills.load(triggers=triggers)
        memory_block = ""
        if include_memory:
            mem_parts: list[str] = []
            # Shared context (user identity, channel constraints) — loaded for
            # every agent so personas inherit a single source of truth.
            shared = self.memory.read("chat-context").strip()
            if shared:
                mem_parts.append(shared)
            # Per-agent memory.
            persona = self.memory.read(memory_key).strip()
            if persona:
                mem_parts.append(persona)
            if mem_parts:
                memory_block = "<memory>\n" + "\n\n".join(mem_parts) + "\n</memory>"

        # Runtime identity: tell the model which adapter+model is actually
        # serving this turn, so it doesn't hallucinate (Gemma loves saying
        # "I'm Claude" otherwise).
        runtime_block = (
            f"<runtime>\n"
            f"agent={agent_name}\n"
            f"adapter={adapter_name}\n"
            f"model={model}\n"
            f"If asked which model/agent you are, answer with these values.\n"
            f"</runtime>"
        )

        sys_parts = [
            p
            for p in (runtime_block, skill_block, memory_block, agent.system_prompt.strip())
            if p
        ]
        system_prompt = "\n\n".join(sys_parts)

        messages: list[Message] = []
        if system_prompt:
            messages.append(Message(role="system", content=system_prompt))
        messages.extend(history)
        messages.append(Message(role="user", content=user_text))

        # INFO so each turn's routing (which agent → which adapter/model) is
        # visible in console logs. Useful when debugging why a /alias request
        # ended up on the wrong backend.
        log.info(
            "dispatch agent=%s depth=%d adapter=%s model=%s skills_chars=%d mem_chars=%d",
            agent_name,
            depth,
            adapter_name,
            model,
            len(skill_block),
            len(memory_block),
        )

        await _emit(f"🤖 {agent_name} thinking ({adapter_name}/{model})…")

        chunks: list[str] = []
        try:
            async for chunk in adapter.chat(model, messages, status_cb=_emit):
                chunks.append(chunk)
        except Exception as e:
            log.exception("adapter chat failed for agent %s", agent_name)
            return f"[error from {adapter_name}] {e}"
        raw_reply = "".join(chunks).strip() or "(no reply)"

        # Extract <remember>…</remember> blocks.
        cleaned, facts = extract_remember(raw_reply)
        for fact in facts:
            self.memory.append(memory_key, fact)
            log.info("memory.append key=%s chars=%d", memory_key, len(fact))

        # Extract <delegate>…</delegate> blocks.
        calls = extract_delegations(cleaned)
        if not calls:
            return cleaned

        delegated_results: list[tuple[str, str]] = []
        for call in calls:
            log.info("delegating %s -> %s (depth %d)", agent_name, call.agent, depth + 1)
            await _emit(f"↪ {agent_name} → {call.agent} (depth {depth + 1})…")
            sub_reply = await self._run_agent(
                agent_name=call.agent,
                user_text=call.prompt,
                history=[],  # sub-agents start fresh
                triggers=triggers,
                depth=depth + 1,
                memory_key=memory_key,  # sub-agents share parent memory namespace
                include_memory=False,   # don't bleed memory into specialist sub-agents
                visited=visited,
                status_cb=status_cb,
            )
            delegated_results.append((call.agent, sub_reply))

        body_without_delegates = strip_delegations(cleaned)

        if self.cfg.delegation_mode == "replace":
            # Concatenate sub-agent replies, ignore parent body.
            return "\n\n".join(r for _, r in delegated_results).strip()

        # Default: splice. Append sub-agent results below the parent's prose.
        spliced = body_without_delegates
        for agent_id, sub_reply in delegated_results:
            spliced = (
                f"{spliced}\n\n--- from {agent_id} ---\n{sub_reply}"
                if spliced
                else f"--- from {agent_id} ---\n{sub_reply}"
            )
        return spliced.strip()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _memory_key(self, agent_name: str, user_id: int) -> str:
        if self.cfg.per_user_memory:
            return f"{agent_name}_{user_id}"
        return agent_name

    def _fallback_model(self, adapter_name: str) -> str:
        # Use the session-default model if the agent didn't pin one.
        return self.sessions.default_model or ""
