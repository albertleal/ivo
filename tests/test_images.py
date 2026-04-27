"""End-to-end test for image ingestion: Telegram photo → disk → orchestrator.

Validates the full pipeline without hitting Telegram or any LLM:
  1. `save_telegram_file` downloads a Telegram file_id and writes it to disk
     with a deterministic name under `uploads_dir`.
  2. The poller's `_handle_image` builds a prompt referencing that path and
     forwards it to `_process_text`, which goes through the orchestrator.
  3. The reply is sent back to the user.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ivo.bot import images as IMG
from ivo.bot import poller as P


@pytest.mark.asyncio
async def test_save_telegram_file_writes_image_to_disk(tmp_path: Path) -> None:
    payload = b"\x89PNG\r\n\x1a\nFAKE_IMAGE_BYTES"
    file_id = "AgACAgIAAxkBAAIBexyz" + "0" * 40

    async def _download(target_path: str) -> None:
        Path(target_path).write_bytes(payload)

    tg_file = MagicMock()
    tg_file.download_to_drive = AsyncMock(side_effect=_download)
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=tg_file)

    saved = await IMG.save_telegram_file(bot, file_id, ".png", tmp_path)

    saved_path = Path(saved)
    assert saved_path.parent == tmp_path
    assert saved_path.suffix == ".png"
    assert file_id[:32] in saved_path.name
    assert saved_path.read_bytes() == payload
    bot.get_file.assert_awaited_once_with(file_id)


@pytest.mark.asyncio
async def test_image_pipeline_end_to_end(tmp_path, monkeypatch) -> None:
    """Photo arrives → saved → orchestrator receives a prompt with the path
    → reply flows back to the user."""

    # Route uploads to a temp dir.
    monkeypatch.setattr(P, "_UPLOADS_DIR", str(tmp_path))

    # Capture what the orchestrator was asked to handle.
    received_prompts: list[str] = []

    async def fake_handle_message(ctx, user_id, text, status_cb=None):
        received_prompts.append(text)
        if status_cb:
            await status_cb("👀 inspecting image")
        return "Looks like a tiny PNG. Cool."

    monkeypatch.setattr(P.H, "handle_message", fake_handle_message)

    # Stub voice/photo/document senders so _send_reply doesn't blow up.
    sent_replies: list[str] = []

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()

    payload = b"\x89PNG\r\n\x1a\nDATA"
    tg_file = MagicMock()
    tg_file.download_to_drive = AsyncMock(
        side_effect=lambda p: Path(p).write_bytes(payload)
    )
    bot.get_file = AsyncMock(return_value=tg_file)

    chat = MagicMock()
    chat.id = 7777
    chat.send_action = AsyncMock()

    msg = MagicMock()
    msg.message_id = 100
    msg.caption = "what's this?"
    msg.photo = [MagicMock(file_id="A" * 50)]
    msg.reply_text = AsyncMock(side_effect=lambda t, **kw: sent_replies.append(t))

    user = MagicMock()
    user.id = 4242

    update = MagicMock()
    update.effective_chat = chat
    update.effective_message = msg
    update.effective_user = user

    c = MagicMock()
    c.bot = bot

    # Minimal BotContext — only fields touched by the image path / handlers.
    sess = MagicMock()
    sess.voice_reply = False
    sessions = MagicMock()
    sessions.get = MagicMock(return_value=sess)

    cfg = MagicMock()
    cfg.agents.front_door = "chat"

    ctx = MagicMock()
    ctx.config = cfg
    ctx.sessions = sessions

    # Run the same _handle_image inline (mirrors the closure in build_application).
    photo = msg.photo[-1]
    saved = await IMG.save_telegram_file(c.bot, photo.file_id, ".jpg", P._UPLOADS_DIR)
    assert Path(saved).read_bytes() == payload
    await msg.reply_text(f"📸 saved → {saved}")
    caption = (msg.caption or "").strip() or "Have a look at this image."
    prompt = (
        f"{caption}\n\n"
        f"(An image was just shared via Telegram and saved to {saved}. "
        f"Use your `view_image` tool on that exact path to inspect it.)"
    )
    await P._process_text(ctx, update, c.bot, user.id, prompt)

    # Orchestrator received a prompt that references the saved path verbatim.
    assert received_prompts, "orchestrator was never called"
    assert saved in received_prompts[0]
    assert "what's this?" in received_prompts[0]

    # The user got both the "saved" notice and the assistant reply.
    assert any("📸 saved" in r for r in sent_replies)
    assert any("Looks like a tiny PNG" in r for r in sent_replies)


@pytest.mark.asyncio
async def test_save_telegram_file_unknown_ext_falls_back_to_jpg(tmp_path: Path) -> None:
    file_id = "X" * 50
    tg_file = MagicMock()
    tg_file.download_to_drive = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"x"))
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=tg_file)

    saved = await IMG.save_telegram_file(bot, file_id, ".weird", tmp_path)

    assert Path(saved).suffix == ".jpg"
