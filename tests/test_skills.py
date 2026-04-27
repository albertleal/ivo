"""Skill loader tests."""

from __future__ import annotations

from pathlib import Path

from ivo.skills import SkillManager

SKILLS_DIR = Path(__file__).resolve().parent.parent / ".github" / "skills"


def test_skills_dir_loads_all_shipped_skills():
    sm = SkillManager(SKILLS_DIR)
    names = set(sm.skills)
    assert {"personality", "humanize", "telegram-style", "voice-style"} <= names


def test_auto_load_default_includes_personality_and_humanize_only():
    sm = SkillManager(SKILLS_DIR)
    out = sm.load(triggers=[])
    assert "Skill: personality" in out
    assert "Skill: humanize" in out
    # telegram-style + voice-style are trigger-conditional, not auto-load.
    assert "Skill: telegram-style" not in out
    assert "Skill: voice-style" not in out


def test_chat_trigger_adds_telegram_style():
    sm = SkillManager(SKILLS_DIR)
    out = sm.load(triggers=["chat"])
    assert "Skill: telegram-style" in out
    assert "Skill: voice-style" not in out


def test_voice_trigger_swaps_in_voice_style():
    sm = SkillManager(SKILLS_DIR)
    out = sm.load(triggers=["voice"])
    assert "Skill: voice-style" in out
    assert "Skill: telegram-style" not in out


def test_auto_load_override_takes_precedence(tmp_path):
    # Build a tiny custom skills dir
    (tmp_path / "a.md").write_text("body A")
    (tmp_path / "b.md").write_text("body B")
    (tmp_path / "meta.yaml").write_text(
        "skills:\n"
        "  - {name: a, path: a.md, auto_load: true, triggers: []}\n"
        "  - {name: b, path: b.md, auto_load: true, triggers: []}\n"
    )
    sm = SkillManager(tmp_path, auto_load=["a"])  # only a
    out = sm.load()
    assert "body A" in out
    assert "body B" not in out
