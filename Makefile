.PHONY: install run test lint format clean openapi

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	@bash scripts/install.sh

run:
	$(PY) -m ivo --config config.yaml

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check ivo tests

format:
	$(PY) -m ruff format ivo tests

openapi:
	@$(PY) -c "from ivo.config import load_config; from ivo.api.server import build_app; import json, os, types; ctx = types.SimpleNamespace(config=load_config('config.example.yaml')); app = build_app(ctx); os.makedirs('docs', exist_ok=True); open('docs/openapi.json','w').write(json.dumps(app.openapi(), indent=2)); print('wrote docs/openapi.json')"

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__ *.egg-info build dist
