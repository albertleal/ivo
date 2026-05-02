"""Per-user session state (current model + chat history).

Three backends:
  - memory: in-process dict, lost on restart. Default for tests.
  - json:   one JSON file at `path`, written on every change.
  - sqlite: minimal sqlite3 store keyed by user_id.
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from ..adapters import Message


@dataclass
class Session:
    user_id: int
    adapter: str
    model: str
    workspace: str = "default"
    agent: str = ""
    voice_reply: bool = False
    history: list[Message] = field(default_factory=list)
    # Per-adapter remembered model: when the user switches adapter, we restore
    # the last model they used on it. Avoids carrying a stale model belonging
    # to a different adapter (which crashed downstream chats).
    last_models: dict[str, str] = field(default_factory=dict)


class SessionStore:
    """Pluggable session store. Synchronous; calls are quick.

    Persistence note: only *state* (adapter / model / agent / voice_reply)
    is persisted to disk. Chat history is kept in process memory and is
    intentionally lost on restart — the conversation context lives in the
    underlying provider (e.g. Copilot CLI sessions), so re-persisting it
    here would just bloat the DB.
    """

    def __init__(
        self,
        backend: str = "memory",
        path: str | None = None,
        history_limit: int = 20,
        default_adapter: str = "copilot",
        default_model: str = "",
        default_workspace: str = "default",
    ) -> None:
        self.backend = backend
        self.path = path
        self.history_limit = history_limit
        self.default_adapter = default_adapter
        self.default_model = default_model
        self.default_workspace = default_workspace
        self._workspace = default_workspace
        self._lock = Lock()
        self._mem: dict[tuple[str, int], Session] = {}

        if backend == "sqlite":
            assert path, "sqlite backend requires a path"
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS session_states ("
                "workspace TEXT NOT NULL, user_id INTEGER NOT NULL, "
                "adapter TEXT, model TEXT, "
                "agent TEXT DEFAULT '', voice_reply INTEGER DEFAULT 0, "
                "last_models TEXT DEFAULT '{}', "
                "PRIMARY KEY (workspace, user_id))"
            )
            # Legacy table (pre-workspace namespacing). Keep for bootstrap reads.
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "user_id INTEGER PRIMARY KEY, adapter TEXT, model TEXT, "
                "agent TEXT DEFAULT '', voice_reply INTEGER DEFAULT 0, "
                "last_models TEXT DEFAULT '{}')"
            )
            # Lightweight migration for older DBs that had `history` and/or
            # were missing agent / voice_reply / last_models.
            cols = {
                row[1]
                for row in self._db.execute("PRAGMA table_info(sessions)")
            }
            if "agent" not in cols:
                self._db.execute("ALTER TABLE sessions ADD COLUMN agent TEXT DEFAULT ''")
            if "voice_reply" not in cols:
                self._db.execute("ALTER TABLE sessions ADD COLUMN voice_reply INTEGER DEFAULT 0")
            if "last_models" not in cols:
                self._db.execute(
                    "ALTER TABLE sessions ADD COLUMN last_models TEXT DEFAULT '{}'"
                )
            self._db.commit()
        elif backend == "json":
            assert path, "json backend requires a path"
            self._json_path = Path(path)
            self._json_path.parent.mkdir(parents=True, exist_ok=True)
            if self._json_path.exists():
                self._mem = self._load_json()
        elif backend == "memory":
            pass
        else:
            raise ValueError(f"unknown session backend: {backend!r}")

    # ── public API ───────────────────────────────────────────────────────────

    def set_workspace(self, workspace: str) -> None:
        with self._lock:
            ws = (workspace or self.default_workspace).strip() or self.default_workspace
            self._workspace = ws

    def get_workspace(self) -> str:
        with self._lock:
            return self._workspace

    def get(self, user_id: int) -> Session:
        with self._lock:
            ws = self._workspace
            sess = self._read(user_id, ws)
            if sess is None:
                sess = Session(
                    user_id=user_id,
                    workspace=ws,
                    adapter=self.default_adapter,
                    model=self.default_model,
                )
                self._write(sess)
            return sess

    def set_model(self, user_id: int, adapter: str, model: str) -> Session:
        with self._lock:
            ws = self._workspace
            sess = self._read(user_id, ws) or Session(user_id=user_id, workspace=ws, adapter=adapter, model=model)
            changed = sess.adapter != adapter or sess.model != model
            sess.adapter = adapter
            sess.model = model
            if model:
                sess.last_models[adapter] = model
            if changed:
                sess.history = []
            self._write(sess)
            return sess

    def set_adapter(self, user_id: int, adapter: str) -> Session:
        with self._lock:
            ws = self._workspace
            sess = self._read(user_id, ws) or Session(
                user_id=user_id,
                workspace=ws,
                adapter=adapter,
                model=self.default_model,
            )
            changed = sess.adapter != adapter
            # Remember the model the user had on the *previous* adapter so we
            # can restore it if they come back.
            if sess.adapter and sess.model:
                sess.last_models[sess.adapter] = sess.model
            sess.adapter = adapter
            # Restore the last model used on this adapter, if any. Otherwise
            # clear it so the bot prompts the user to pick one — sending the
            # previous adapter's model id to a different backend used to crash.
            sess.model = sess.last_models.get(adapter, "")
            if changed:
                sess.history = []
            self._write(sess)
            return sess

    def set_agent(self, user_id: int, agent: str) -> Session:
        with self._lock:
            ws = self._workspace
            sess = self._read(user_id, ws) or Session(
                user_id=user_id,
                workspace=ws,
                adapter=self.default_adapter,
                model=self.default_model,
            )
            changed = sess.agent != agent
            sess.agent = agent
            if changed:
                sess.history = []
            self._write(sess)
            return sess

    def set_voice_reply(self, user_id: int, on: bool) -> Session:
        with self._lock:
            ws = self._workspace
            sess = self._read(user_id, ws) or Session(
                user_id=user_id,
                workspace=ws,
                adapter=self.default_adapter,
                model=self.default_model,
            )
            sess.voice_reply = bool(on)
            self._write(sess)
            return sess

    def append(self, user_id: int, msg: Message) -> Session:
        with self._lock:
            ws = self._workspace
            sess = self._read(user_id, ws) or Session(
                user_id=user_id,
                workspace=ws,
                adapter=self.default_adapter,
                model=self.default_model,
            )
            sess.history.append(msg)
            # Trim from the head, keep the tail.
            if len(sess.history) > self.history_limit:
                sess.history = list(deque(sess.history, maxlen=self.history_limit))
            self._write(sess)
            return sess

    def reset(self, user_id: int) -> None:
        with self._lock:
            sess = self._read(user_id, self._workspace)
            if sess is not None:
                sess.history = []
                self._write(sess)

    # ── backend dispatch ─────────────────────────────────────────────────────

    def _read(self, user_id: int, workspace: str) -> Session | None:
        if self.backend == "sqlite":
            row = self._db.execute(
                "SELECT adapter, model, agent, voice_reply, last_models "
                "FROM session_states WHERE workspace = ? AND user_id = ?",
                (workspace, user_id),
            ).fetchone()

            # Backward compatibility: if the namespace row doesn't exist yet,
            # bootstrap from the legacy single-workspace table.
            if row is None and workspace == self.default_workspace:
                row = self._db.execute(
                "SELECT adapter, model, agent, voice_reply, last_models "
                "FROM sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            adapter, model, agent, voice_reply, last_models_raw = row
            try:
                last_models = json.loads(last_models_raw or "{}")
                if not isinstance(last_models, dict):
                    last_models = {}
            except (json.JSONDecodeError, TypeError):
                last_models = {}
            # History is in-memory only; preserve any existing buffer.
            existing = self._mem.get((workspace, user_id))
            history = existing.history if existing else []
            sess = Session(
                user_id=user_id,
                workspace=workspace,
                adapter=adapter,
                model=model,
                agent=agent or "",
                voice_reply=bool(voice_reply),
                history=history,
                last_models=last_models,
            )
            self._mem[(workspace, user_id)] = sess
            return sess
        return self._mem.get((workspace, user_id))

    def _write(self, sess: Session) -> None:
        # Always update the in-memory buffer so history survives the call.
        self._mem[(sess.workspace, sess.user_id)] = sess
        if self.backend == "sqlite":
            self._db.execute(
                "INSERT OR REPLACE INTO session_states"
                "(workspace, user_id, adapter, model, agent, voice_reply, last_models) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    sess.workspace,
                    sess.user_id,
                    sess.adapter,
                    sess.model,
                    sess.agent,
                    int(sess.voice_reply),
                    json.dumps(sess.last_models or {}),
                ),
            )
            self._db.commit()
            return
        if self.backend == "json":
            self._dump_json()

    # ── json helpers ─────────────────────────────────────────────────────────

    def _dump_json(self) -> None:
        # State only — history is in-memory, never persisted.
        data: dict[str, dict[str, dict]] = {}
        for (workspace, uid), s in self._mem.items():
            ws = data.setdefault(workspace, {})
            ws[str(uid)] = {
                "adapter": s.adapter,
                "model": s.model,
                "agent": s.agent,
                "voice_reply": s.voice_reply,
                "last_models": s.last_models,
            }
        self._json_path.write_text(json.dumps(data, indent=2))

    def _load_json(self) -> dict[tuple[str, int], Session]:
        raw = json.loads(self._json_path.read_text())
        out: dict[tuple[str, int], Session] = {}

        # Legacy shape: {"<uid>": {...state...}}. Load it as default workspace.
        if raw and all(isinstance(v, dict) and "adapter" in v for v in raw.values()):
            raw = {self.default_workspace: raw}

        for workspace, users in raw.items():
            if not isinstance(users, dict):
                continue
            for uid, s in users.items():
                out[(workspace, int(uid))] = Session(
                    user_id=int(uid),
                    workspace=workspace,
                    adapter=s["adapter"],
                    model=s["model"],
                    agent=s.get("agent", ""),
                    voice_reply=bool(s.get("voice_reply", False)),
                    history=[],  # never restored from disk
                    last_models=dict(s.get("last_models", {}) or {}),
                )
        return out
