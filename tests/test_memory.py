"""Memory store + remember protocol tests."""

from __future__ import annotations

from ivo.memory import MemoryStore, extract_remember


def test_append_and_read_roundtrip(tmp_path):
    m = MemoryStore(tmp_path)
    m.append("assistant", "user prefers concise replies")
    m.append("assistant", "lives in Lisbon")
    text = m.read("assistant")
    assert "concise replies" in text
    assert "Lisbon" in text


def test_max_chars_returns_tail(tmp_path):
    m = MemoryStore(tmp_path, max_chars=50)
    m.append("a", "x" * 100)
    text = m.read("a")
    assert len(text) <= 50


def test_replace_section_inserts_then_replaces(tmp_path):
    m = MemoryStore(tmp_path)
    m.replace_section("a", "Preferences", "likes tea")
    assert "Preferences" in m.read("a")
    assert "likes tea" in m.read("a")
    m.replace_section("a", "Preferences", "likes coffee now")
    text = m.read("a")
    assert "likes coffee now" in text
    assert "likes tea" not in text


def test_extract_remember_strips_blocks_and_returns_facts():
    raw = "hello\n<remember>user is in Lisbon</remember>\nbye <remember>cat named Mochi</remember>"
    clean, facts = extract_remember(raw)
    assert facts == ["user is in Lisbon", "cat named Mochi"]
    assert "<remember>" not in clean
    assert "hello" in clean and "bye" in clean


def test_extract_remember_handles_no_blocks():
    clean, facts = extract_remember("just a normal reply")
    assert facts == []
    assert clean == "just a normal reply"


def test_unsafe_persona_name_is_sanitized(tmp_path):
    m = MemoryStore(tmp_path)
    m.append("../evil", "should not escape dir")
    # File should live inside tmp_path, not above it.
    files = list(tmp_path.iterdir())
    assert files
    for f in files:
        assert tmp_path in f.parents or f.parent == tmp_path


def test_workspace_path_redirects_memory_dir(tmp_path):
    """When use_workspace=True + workspace_path set, files land in <ws>/.ivo/memory/."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    m = MemoryStore(
        bundled,
        workspace_path=workspace,
        use_workspace=True,
    )
    m.append("ceo", "fact in workspace")
    expected = workspace / ".ivo" / "memory" / "ceo.md"
    assert expected.exists()
    assert "fact in workspace" in expected.read_text()
    # bundled dir should be empty of memory writes
    assert not list(bundled.glob("*.md"))


def test_workspace_path_ignored_when_flag_false(tmp_path):
    """workspace_path alone (use_workspace=False) keeps memory in the bundled dir."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    m = MemoryStore(bundled, workspace_path=workspace, use_workspace=False)
    m.append("ceo", "stays bundled")
    assert (bundled / "ceo.md").exists()
    assert not (workspace / ".ivo" / "memory" / "ceo.md").exists()
