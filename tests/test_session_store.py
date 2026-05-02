"""Session store tests."""

from __future__ import annotations

from ivo.session import SessionStore


def test_workspace_namespace_switch_restores_last_state_memory_backend():
    s = SessionStore(
        backend="memory",
        default_adapter="copilot",
        default_model="model-a",
        default_workspace="ivo",
    )

    s.set_workspace("ivo")
    s.set_model(1, "copilot", "model-ivo")

    s.set_workspace("eltomatic")
    fresh = s.get(1)
    assert fresh.model == "model-a"
    s.set_model(1, "copilot", "model-eltomatic")

    s.set_workspace("ivo")
    restored_ivo = s.get(1)
    assert restored_ivo.model == "model-ivo"

    s.set_workspace("eltomatic")
    restored_eltomatic = s.get(1)
    assert restored_eltomatic.model == "model-eltomatic"


def test_workspace_namespace_switch_restores_last_state_sqlite_backend(tmp_path):
    db = tmp_path / "sessions.db"
    s = SessionStore(
        backend="sqlite",
        path=str(db),
        default_adapter="copilot",
        default_model="model-a",
        default_workspace="ivo",
    )

    s.set_workspace("ivo")
    s.set_model(1, "copilot", "model-ivo")

    s.set_workspace("root")
    s.set_model(1, "copilot", "model-root")

    # Re-open store to confirm persistence by workspace namespace.
    s2 = SessionStore(
        backend="sqlite",
        path=str(db),
        default_adapter="copilot",
        default_model="model-a",
        default_workspace="ivo",
    )
    s2.set_workspace("ivo")
    assert s2.get(1).model == "model-ivo"
    s2.set_workspace("root")
    assert s2.get(1).model == "model-root"
