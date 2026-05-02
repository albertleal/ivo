"""Telegram long-poll loop.

Wraps the pure handlers in `handlers.py` with a python-telegram-bot
Application. This is the only module that imports from `telegram.ext`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import tempfile
from pathlib import Path

from telegram import Bot, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import handlers as H
from . import images as IMG
from . import voice as VOICE
from .handlers import BotContext

log = logging.getLogger("bot.poller")


# Telegram clears the "typing" action after ~5s, so we refresh under that.
_TYPING_INTERVAL = 4.0

# Where downloaded photos are saved. Default = OS tmpdir/ivo.
# Override with UPLOADS_DIR env var.
_UPLOADS_DIR = os.getenv(
    "UPLOADS_DIR",
    str(Path(tempfile.gettempdir()) / "ivo"),
)


# ── live status helpers ─────────────────────────────────────────────────────


async def _typing_loop(chat, stop: asyncio.Event) -> None:
    """Continuously send 'typing' action until `stop` is set."""
    while not stop.is_set():
        try:
            await chat.send_action("typing")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=_TYPING_INTERVAL)
        except TimeoutError:
            pass


class _LiveStatus:
    """A single editable status message that gets updated as the agent works."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._message_id: int | None = None
        self._last_text = ""
        self._lock = asyncio.Lock()

    async def update(self, text: str) -> None:
        async with self._lock:
            if text == self._last_text:
                return
            self._last_text = text
            try:
                if self._message_id is None:
                    msg = await self._bot.send_message(chat_id=self._chat_id, text=text)
                    self._message_id = msg.message_id
                else:
                    await self._bot.edit_message_text(
                        chat_id=self._chat_id,
                        message_id=self._message_id,
                        text=text,
                    )
            except Exception as e:
                log.debug("status update failed: %s", e)

    async def close(self) -> None:
        async with self._lock:
            if self._message_id is None:
                return
            try:
                await self._bot.delete_message(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                )
            except Exception:
                pass
            self._message_id = None


_ATTACH_BLOCK_RE = re.compile(
    r"<attachments>\s*(.*?)\s*</attachments>\s*$",
    re.IGNORECASE | re.DOTALL,
)
_PHOTO_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _parse_attachments(text: str) -> tuple[str, list[str]]:
    """Strip the trailing <attachments> protocol block.

    The chat layer asks agents to declare files for delivery using:

        <attachments>
        /abs/path/file.png
        /abs/path/other.pdf
        </attachments>

    Returns (clean_text, [existing_paths]). The block is removed from the
    text so the user never sees the raw paths.
    """
    if not text:
        return text, []
    m = _ATTACH_BLOCK_RE.search(text)
    if not m:
        return text, []
    body = m.group(1)
    clean = (text[: m.start()]).rstrip()
    paths: list[str] = []
    seen: set[str] = set()
    for raw in body.splitlines():
        p = raw.strip().strip("`'\"")
        if not p or p in seen:
            continue
        if not os.path.isabs(p):
            continue
        if not os.path.isfile(p):
            log.warning("attachment not found on disk: %s", p)
            continue
        seen.add(p)
        paths.append(p)
    return clean, paths


async def _send_reply(update: Update, ctx_bot: Bot, reply: str, voice_reply: bool) -> None:
    """Send the assistant's reply as text, attachments and (optionally) voice."""
    chat = update.effective_chat

    # Parse the structured <attachments> block. The agent declares files
    # explicitly there; everything else stays prose.
    clean_text, attachments = _parse_attachments(reply)

    for path in attachments:
        ext = os.path.splitext(path)[1].lower()
        try:
            with open(path, "rb") as f:
                if ext in _PHOTO_EXTS:
                    await ctx_bot.send_photo(chat_id=chat.id, photo=f)
                else:
                    await ctx_bot.send_document(chat_id=chat.id, document=f)
        except Exception as e:
            log.warning("send attachment failed for %s: %s", path, e)

    # Telegram caps a single message at 4096 chars — chunk if needed.
    if clean_text:
        for i in range(0, len(clean_text), 4000):
            await update.effective_message.reply_text(clean_text[i : i + 4000])

    if not voice_reply:
        return

    chat = update.effective_chat
    # Show "recording audio" while kokoro + ffmpeg run, refreshed every 4s.
    stop_recording = asyncio.Event()

    async def _recording_loop() -> None:
        while not stop_recording.is_set():
            try:
                await chat.send_action("record_voice")
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_recording.wait(), timeout=_TYPING_INTERVAL)
            except TimeoutError:
                pass

    rec_task = asyncio.create_task(_recording_loop())
    ogg: str | None = None
    try:
        ogg = await VOICE.synthesize(reply)
    except Exception as e:
        log.warning("voice synthesis failed: %s", e)
        stop_recording.set()
        try:
            await rec_task
        except Exception:
            pass
        return
    stop_recording.set()
    try:
        await rec_task
    except Exception:
        pass

    try:
        with open(ogg, "rb") as f:
            await ctx_bot.send_voice(chat_id=chat.id, voice=f)
    finally:
        try:
            os.unlink(ogg)
        except OSError:
            pass


async def _process_text(
    ctx: BotContext,
    update: Update,
    bot: Bot,
    user_id: int,
    text: str,
) -> None:
    """Run a text turn through the orchestrator with live status + typing.

    Only one turn per user runs at a time. If a new turn arrives while
    another is in flight (either a fresh message or `/stop`), the previous
    one is cancelled — same UX as VS Code's Copilot stop button. The
    underlying CLI subprocess is killed by the adapter's CancelledError
    handler, so it does not keep working in the background.
    """
    chat = update.effective_chat
    chat_id = chat.id

    # Cancel any prior in-flight turn for this user.
    await _cancel_user_task(ctx, user_id, reason="superseded by new message")

    status = _LiveStatus(bot, chat_id)
    await status.update("🤖 thinking…")

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing_loop(chat, stop_typing))

    async def _status_cb(msg: str) -> None:
        await status.update(msg)

    async def _run() -> str:
        return await H.handle_message(ctx, user_id, text, status_cb=_status_cb)

    work = asyncio.create_task(_run(), name=f"turn:{user_id}")
    _set_user_task(ctx, user_id, work)

    cancelled = False
    try:
        try:
            reply = await work
        except asyncio.CancelledError:
            cancelled = True
            reply = "🛑 stopped — what's next?"
    finally:
        stop_typing.set()
        try:
            await typing_task
        except Exception:
            pass
        await status.close()
        _clear_user_task(ctx, user_id, work)

    sess = ctx.sessions.get(user_id)
    # Don't speak the "stopped" notice — it's an interrupt acknowledgement,
    # not the agent's reply.
    voice_out = sess.voice_reply and not cancelled
    await _send_reply(update, bot, reply, voice_out)


# ── per-user in-flight task registry ────────────────────────────────────────


def _running(ctx: BotContext) -> dict[int, asyncio.Task]:
    reg = getattr(ctx, "_running_turns", None)
    if reg is None:
        reg = {}
        ctx._running_turns = reg
    return reg


def _set_user_task(ctx: BotContext, user_id: int, task: asyncio.Task) -> None:
    _running(ctx)[user_id] = task


def _clear_user_task(ctx: BotContext, user_id: int, task: asyncio.Task) -> None:
    reg = _running(ctx)
    if reg.get(user_id) is task:
        reg.pop(user_id, None)


async def _cancel_user_task(
    ctx: BotContext, user_id: int, *, reason: str = "cancelled"
) -> bool:
    """Cancel the user's in-flight turn (if any). Returns True if one existed."""
    task = _running(ctx).pop(user_id, None)
    if task is None or task.done():
        return False
    log.info("cancelling turn for user %s: %s", user_id, reason)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    return True


# ── application factory ─────────────────────────────────────────────────────


def build_application(ctx: BotContext):
    """Construct a python-telegram-bot Application bound to `ctx`."""

    app = ApplicationBuilder().token(ctx.config.telegram.token).build()

    # Used for collision detection between adapter / model / agent commands.
    adapter_aliases: set[str] = set(ctx.adapters)
    model_aliases: set[str] = set(ctx.catalog)
    agent_aliases: set[str] = set(ctx.agent_names or [])
    workspace_shortcuts = H.workspace_shortcuts(ctx)

    async def _gate(update: Update) -> bool:
        user = update.effective_user
        if user is None:
            return False
        if not H.is_allowed(ctx, user.id):
            await update.effective_message.reply_text("Access denied.")
            return False
        return True

    # ── built-in commands ───────────────────────────────────────────────────

    async def cmd_start(update: Update, _c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        msg = await H.handle_start(ctx, update.effective_user.id)
        await update.effective_message.reply_text(msg)

    async def cmd_models(update: Update, _c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        msg = await H.handle_models(ctx)
        await update.effective_message.reply_text(msg)

    async def cmd_agent(update: Update, _c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        msg = await H.handle_agent_list(ctx)
        await update.effective_message.reply_text(msg)

    async def cmd_clear(update: Update, _c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        msg = await H.handle_clear(ctx, update.effective_user.id)
        await update.effective_message.reply_text(msg)

    async def cmd_voice(update: Update, _c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        msg = await H.handle_voice_toggle(ctx, update.effective_user.id)
        await update.effective_message.reply_text(msg)

    async def cmd_stop(update: Update, _c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        cancelled = await _cancel_user_task(
            ctx, update.effective_user.id, reason="user /stop"
        )
        # If a turn was running, _process_text already replied with the
        # stop notice. Otherwise, acknowledge here so the user always gets
        # a clear "you can speak now" signal.
        if not cancelled:
            await update.effective_message.reply_text(
                "🛑 stopped — what's next?"
            )

    async def cmd_workspace(update: Update, c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        if c.args:
            msg = await H.handle_workspace_select(
                ctx,
                update.effective_user.id,
                c.args[0],
            )
        else:
            msg = await H.handle_workspace_list(ctx, update.effective_user.id)
        await update.effective_message.reply_text(msg)

    def make_workspace_handler(workspace_name: str):
        async def _h(update: Update, _c: ContextTypes.DEFAULT_TYPE):
            if not await _gate(update):
                return
            msg = await H.handle_workspace_select(
                ctx,
                update.effective_user.id,
                workspace_name,
            )
            await update.effective_message.reply_text(msg)
        return _h

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("agent", cmd_agent))
    app.add_handler(CommandHandler("agents", cmd_agent))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("reset", cmd_clear))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("workspace", cmd_workspace))
    app.add_handler(CommandHandler("ws", cmd_workspace))
    for cmd, workspace_name in workspace_shortcuts.items():
        if cmd in adapter_aliases or cmd in model_aliases or cmd in agent_aliases:
            log.warning("workspace shortcut /%s shadowed by adapter/model/agent", cmd)
            continue
        app.add_handler(CommandHandler(cmd, make_workspace_handler(workspace_name)))

    # ── dynamic /<adapter>, /<model>, /<agent> ──────────────────────────────

    def make_adapter_handler(name: str):
        async def _h(update: Update, _c: ContextTypes.DEFAULT_TYPE):
            if not await _gate(update):
                return
            msg = await H.handle_select_adapter(ctx, update.effective_user.id, name)
            await update.effective_message.reply_text(msg)
        return _h

    def make_model_handler(alias: str):
        async def _h(update: Update, _c: ContextTypes.DEFAULT_TYPE):
            if not await _gate(update):
                return
            msg = await H.handle_select_model(ctx, update.effective_user.id, alias)
            await update.effective_message.reply_text(msg)
        return _h

    def make_agent_handler(name: str):
        async def _h(update: Update, _c: ContextTypes.DEFAULT_TYPE):
            if not await _gate(update):
                return
            msg = await H.handle_select_agent(ctx, update.effective_user.id, name)
            await update.effective_message.reply_text(msg)
        return _h

    # Register adapters first; then models (skipping any name that collides
    # with an adapter); then agents (skipping any name that collides with
    # adapter or model). This makes /copilot select the adapter, never an
    # alias that happened to share the name.
    for name in adapter_aliases:
        app.add_handler(CommandHandler(name, make_adapter_handler(name)))
    for alias in model_aliases:
        if alias in adapter_aliases:
            log.warning("model alias /%s shadowed by adapter command", alias)
            continue
        app.add_handler(CommandHandler(alias, make_model_handler(alias)))
    for name in agent_aliases:
        if name in adapter_aliases or name in model_aliases:
            log.warning("agent /%s shadowed by adapter/model command", name)
            continue
        app.add_handler(CommandHandler(name, make_agent_handler(name)))

    # ── free text → orchestrator ───────────────────────────────────────────

    async def on_message(update: Update, c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        text = update.effective_message.text or ""
        if not text:
            return
        await _process_text(ctx, update, c.bot, update.effective_user.id, text)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    # ── voice in (transcribe → forward as text) ────────────────────────────

    async def on_voice(update: Update, c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        msg = update.effective_message
        v = msg.voice or msg.audio
        if v is None:
            return
        await msg.reply_text("🎙 transcribing…")
        ogg: str | None = None
        try:
            tg_file = await c.bot.get_file(v.file_id)
            tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
            tmp.close()
            ogg = tmp.name
            await tg_file.download_to_drive(ogg)
            text = await VOICE.transcribe_ogg(ogg)
        except Exception as e:
            log.exception("voice handling failed")
            await msg.reply_text(f"voice processing failed: {e}")
            return
        finally:
            if ogg and os.path.exists(ogg):
                try:
                    os.unlink(ogg)
                except OSError:
                    pass

        if not text:
            await msg.reply_text("(blank audio — nothing to say)")
            return
        await msg.reply_text(f"📝 « {text} »")
        await _process_text(ctx, update, c.bot, update.effective_user.id, text)

    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))

    # ── photo / image-document handling ────────────────────────────────────

    async def _handle_image(
        update: Update, c: ContextTypes.DEFAULT_TYPE, file_id: str, ext: str
    ) -> None:
        msg = update.effective_message
        try:
            saved = await IMG.save_telegram_file(c.bot, file_id, ext, _UPLOADS_DIR)
        except Exception as e:
            log.exception("image save failed")
            await msg.reply_text(f"image save failed: {e}")
            return
        await msg.reply_text(f"📸 saved → {saved}")
        caption = (msg.caption or "").strip() or "Have a look at this image."
        prompt = (
            f"{caption}\n\n"
            f"(An image was just shared via Telegram and saved to {saved}. "
            f"Use your `view_image` tool on that exact path to inspect it.)"
        )
        await _process_text(ctx, update, c.bot, update.effective_user.id, prompt)

    async def on_photo(update: Update, c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        msg = update.effective_message
        if not msg.photo:
            return
        photo = msg.photo[-1]  # highest resolution
        await _handle_image(update, c, photo.file_id, ".jpg")

    async def on_image_document(update: Update, c: ContextTypes.DEFAULT_TYPE):
        if not await _gate(update):
            return
        doc = update.effective_message.document
        if doc is None:
            return
        name = doc.file_name or "image"
        ext = Path(name).suffix or ".jpg"
        await _handle_image(update, c, doc.file_id, ext)

    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, on_image_document))

    return app


async def run_polling(ctx: BotContext) -> None:
    """Run the long-poll loop. Exits when cancelled."""
    app = build_application(ctx)
    log.info(
        "starting telegram long-poll loop "
        "(adapters=%d models=%d agents=%d)",
        len(ctx.adapters),
        len(ctx.catalog),
        len(ctx.agent_names or []),
    )
    ok, missing = VOICE.voice_available()
    if ok:
        log.info("voice subsystem ready (whisper-cli + kokoro-onnx)")
    else:
        log.warning("voice subsystem missing: %s", missing)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(timeout=ctx.config.telegram.long_poll_timeout)
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
