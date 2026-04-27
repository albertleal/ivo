# Contributing to ivo

Thanks for considering a contribution! This project aims to be a small,
focused, well-tested chat layer between Telegram and any LLM. Please keep
PRs scoped and add tests.

## Development setup

```bash
make install        # creates .venv, installs runtime + dev deps
make test           # pytest
make lint           # ruff check
make format         # ruff format
```

Python 3.11+ is required.

## Adding an adapter

Adapters live under `ivo/adapters/`. The contract is in
[`adapters/base.py`](ivo/adapters/base.py):

```python
class Adapter(ABC):
    name: str

    async def discover_models(self) -> list[ModelInfo]: ...
    async def chat(self, model: str, messages: list[Message]) -> AsyncIterator[str]: ...
    async def health(self) -> bool: ...
```

Steps:

1. Create `ivo/adapters/<provider>.py` with a subclass of
   `Adapter`. Constructor takes the adapter section from the YAML config as
   a dict.
2. Register it in `ivo/adapters/__init__.py` in the `REGISTRY`
   dict.
3. Add a default config block to `config.example.yaml` with `enabled: false`.
4. Add tests under `tests/test_adapters.py` covering `discover_models()` and
   `chat()`. Mock all network/subprocess I/O — no live calls in CI.

## Code style

- Type hints on every public function.
- `ruff` clean (line length 100). `make format` then `make lint`.
- No secrets in code, fixtures, or commit messages.
- Keep modules under ~300 lines; split when growing.

## Tests

- `pytest` is the runner. `pytest-asyncio` is configured.
- Mock all external I/O (`subprocess`, `httpx`, Telegram API).
- Aim for one test per public behavior, not per line.

## Releasing

Maintainer-only. Bump `pyproject.toml`, update `CHANGELOG.md`, tag.

## Inspiration / credits

The skills + memory + sub-agent orchestration pattern was extracted from
internal tooling and generalized for OSS use. No domain-specific code or
content shipped over.
