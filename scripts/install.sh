#!/usr/bin/env bash
# Idempotent bootstrap for ivo.
# Creates a venv, installs the package + dev deps, copies config templates if
# missing. Safe to re-run.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="${VENV:-.venv}"
PY="${PY:-python3}"

if [ ! -d "$VENV" ]; then
  echo "→ Creating virtualenv at $VENV"
  "$PY" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "→ Upgrading pip"
python -m pip install --quiet --upgrade pip

echo "→ Installing ivo (editable) with dev extras"
python -m pip install --quiet -e ".[dev]"

if [ ! -f .env ] && [ -f .env.example ]; then
  echo "→ Creating .env from .env.example (edit it before running)"
  cp .env.example .env
fi

if [ ! -f config.yaml ] && [ -f config.example.yaml ]; then
  echo "→ Creating config.yaml from config.example.yaml"
  cp config.example.yaml config.yaml
fi

mkdir -p data .github/agents .ivo/memory .github/skills

echo "✓ Install complete. Next: edit .env, then 'make run'."
