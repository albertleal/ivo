"""CLI entrypoint: `python -m ivo`."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .app import run
from .config import load_config
from .utils.logging import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="ivo")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to YAML config file (default: ./config.yaml)",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(
            f"error: config file not found: {args.config}\n"
            f"hint: copy config.example.yaml to {args.config} and edit it.",
            file=sys.stderr,
        )
        return 2

    cfg = load_config(args.config)
    setup_logging(cfg.logging.level)

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
