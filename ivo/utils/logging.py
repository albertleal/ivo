"""Logging setup.

A single console (stderr) handler is installed at boot so that INFO,
WARNING and ERROR records from every ivo module — adapters, orchestrator,
bot handlers — appear together in the terminal / pm2 / journalctl logs.

The ``LOG_LEVEL`` environment variable (e.g. ``LOG_LEVEL=DEBUG``) overrides
the level coming from ``config.yaml`` without requiring a config edit, which
is handy for one-off debug sessions (e.g. inspecting why a specific model
returned an error).
"""

from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Third-party libs that flood DEBUG/INFO with low-signal noise. We pin them
# to WARNING so ivo's own logs stay readable. Bump them up manually when
# debugging that specific layer.
_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "hpack",
    "telegram",
    "telegram.ext",
    "telegram.bot",
    "asyncio",
    "uvicorn.access",
)


def setup_logging(level: str = "INFO") -> None:
    env_level = os.environ.get("LOG_LEVEL")
    effective = (env_level or level or "INFO").upper()
    numeric = getattr(logging, effective, logging.INFO)

    # Wipe any pre-existing handlers (e.g. uvicorn / pytest installed one)
    # so we always end up with exactly one console handler in our format.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(numeric)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(handler)
    root.setLevel(numeric)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("ivo").info(
        "logging initialised: level=%s (LOG_LEVEL env=%s)",
        effective,
        env_level or "<unset>",
    )
