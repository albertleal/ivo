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
    agent: str = ""
    voice_reply: bool = False
    history: list[Message] = field(default_factory=list)


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
    ) -> None:
        self.backend = backend
        self.path = path
        self.history_limit = history_limit
        self.default_adapter = default_adapter
        self.default_model = default_model
        self._lock = Lock()
        self._mem: dict[int, Session] = {}

        if backend == "sqlite":
            assert path, "sqlite backend requires a path"
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "user_id INTEGER PRIMARY KEY, adapter TEXT, model TEXT, "
                "agent TEXT DEFAULT '', voice_reply INTEGER DEFAULT 0)"
            )
            # Lightweight migration for older DBs that had `history` and/or
            # were missing agent / voice_reply.
            cols = {
                row[1]
                for row in self._db.execute("PRAGMA table_info(sessions)")
            }
            if "agent" not in cols:
                self._db.execute("ALTER TABLE sessions ADD COLUMN agent TEXT DEFAULT ''")
            if "voice_reply" not in cols:
                self._db.execute("ALTER TABLE sessions ADD COLUMN voice_reply INTEGER DEFAULT 0")
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

    def get(self, user_id: int) -> Session:
        with self._lock:
            sess = self._read(user_id)
            if sess is None:
                sess = Session(
                    user_id=user_id,
                    adapter=self.default_adapter,
                    model=self.default_model,
                )
                self._write(sess)
            return sess

    def set_model(self, user_id: int, adapter: str, model: str) -> Session:
        with self._lock:
            sess = self._read(user_id) or Session(user_id, adapter, model)
            changed = sess.adapter != adapter or sess.model != model
            sess.adapter = adapter
            sess.model = model
            if changed:
                sess.history = []
            self._write(sess)
            return sess

    def set_adapter(self, user_id: int, adapter: str) -> Session:
        with self._lock:
            sess = self._read(user_id) or Session(
                user_id=user_id,
                adapter=adapter,
                model=self.default_model,
            )
            changed = sess.adapter != adapter
            sess.adapter = adapter
            if changed:
                sess.history = []
            self._write(sess)
            return sess

    def set_agent(self, user_id: int, agent: str) -> Session:
        with self._lock:
            sess = self._read(user_id) or Session(
                user_id=user_id,
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
            sess = self._read(user_id) or Session(
                user_id=user_id,
                adapter=self.default_adapter,
                model=self.default_model,
            )
            sess.voice_reply = bool(on)
            self._write(sess)
            return sess

    def append(self, user_id: int, msg: Message) -> Session:
        with self._lock:
            sess = self._read(user_id) or Session(
                user_id=user_id,
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
            sess = self._read(user_id)
            if sess is not None:
                sess.history = []
                self._write(sess)

    # ── backend dispatch ─────────────────────────────────────────────────────

    def _read(self, user_id: int) -> Session | None:
        if self.backend == "sqlite":
            row = self._db.execute(
                "SELECT adapter, model, agent, voice_reply FROM sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            adapter, model, agent, voice_reply = row
            # History is in-memory only; preserve any existing buffer.
            existing = self._mem.get(user_id)
            history = existing.history if existing else []
            sess = Session(
                user_id=user_id,
                adapter=adapter,
                model=model,
                agent=agent or "",
                voice_reply=bool(voice_reply),
                history=history,
            )
            self._mem[user_id] = sess
            return sess
        return self._mem.get(user_id)

    def _write(self, sess: Session) -> None:
        # Always update the in-memory buffer so history survives the call.
        self._mem[sess.user_id] = sess
        if self.backend == "sqlite":
            self._db.execute(
                "INSERT OR REPLACE INTO sessions(user_id, adapter, model, agent, voice_reply) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    sess.user_id,
                    sess.adapter,
                    sess.model,
                    sess.agent,
                    int(sess.voice_reply),
                ),
            )
            self._db.commit()
            return
        if self.backend == "json":
            self._dump_json()

    # ── json helpers ─────────────────────────────────────────────────────────

    def _dump_json(self) -> None:
        # State only — history is in-memory, never persisted.
        data = {
            str(uid): {
                "adapter": s.adapter,
                "model": s.model,
                "agent": s.agent,
                "voice_reply": s.voice_reply,
            }
            for uid, s in self._mem.items()
        }
        self._json_path.write_text(json.dumps(data, indent=2))

    def _load_json(self) -> dict[int, Session]:
        raw = json.loads(self._json_path.read_text())
        out: dict[int, Session] = {}
        for uid, s in raw.items():
            out[int(uid)] = Session(
                user_id=int(uid),
                adapter=s["adapter"],
                model=s["model"],
                agent=s.get("agent", ""),
                voice_reply=bool(s.get("voice_reply", False)),
                history=[],  # never restored from disk
            )
        return out
