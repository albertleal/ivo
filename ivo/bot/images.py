"""Photo download helper.

Saves incoming Telegram photos under <uploads_dir>/ with a name that includes
the file_id, so the agent can refer to it from disk. Returns the saved path.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

from telegram import Bot

log = logging.getLogger("bot.images")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


async def save_telegram_file(
    bot: Bot,
    file_id: str,
    suggested_ext: str,
    uploads_dir: str | Path,
) -> str:
    """Download a Telegram file_id and save it to `uploads_dir`. Return path."""
    ext = suggested_ext.lower()
    if ext not in IMAGE_EXTS:
        ext = ".jpg"

    uploads = Path(uploads_dir)
    uploads.mkdir(parents=True, exist_ok=True)

    filename = f"{int(time.time())}_{file_id[:32]}{ext}"
    target = uploads / filename

    tg_file = await bot.get_file(file_id)
    # python-telegram-bot writes to a temp path, we move into place.
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp.close()
    try:
        await tg_file.download_to_drive(tmp.name)
        os.replace(tmp.name, target)
    finally:
        if os.path.exists(tmp.name):
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    log.info("saved image %s (%d bytes)", target, target.stat().st_size)
    return str(target)
