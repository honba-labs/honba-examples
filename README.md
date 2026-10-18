# honba-examples

Runnable learning-path examples for [Honba](../honba-tamashii). Every numbered script is
offline and deterministic by default — the `fake` adapter plus synthetic bars — and each one
teaches a single concept end to end: broker adapters, bars, storage, universes, strategies,
backtesting, mutual funds, options, AI research and production.

```
.
├── examples/        # all runnable learning material (see examples/README.md)
├── src/
│   └── honba_examples/   # shared helper package (base classes, CLI, output, pipeline, ...)
├── docs/            # conceptual notes, the chaining roadmap, screening research
├── tests/           # unit, integration, snapshots (see tests/README.md)
└── pyproject.toml   # single source of truth for dependencies
```

## Install

This repo uses a `src/` layout: `honba_examples` must be installed (editable is fine) before
running anything. There is no `sys.path` fallback for a plain, uninstalled checkout.

```bash
uv pip install -e ../honba-tamashii    # core (or however you install honba)
uv pip install -e ".[dev]"             # this repo: honba_examples + pytest/ruff
```

(`pip install -e ...` works the same way instead of `uv pip install -e ...` if your environment
doesn't have `uv`.)

Some examples load catalog strategies from the sibling `honba-strategies` checkout. They look
for it at `--strategies-dir`, then `$HONBA_STRATEGIES_DIR`, then `../honba-strategies` next to
this repo.

## Quickstart

```bash
python examples/basic/01_connect_dhan.py
python examples/screening/01_multibagger_screener.py --format symbols
pytest -q                 # unit + integration tests (no network, synthetic data)
ruff check . && ruff format --check .
```

Or, with the included Makefile: `make install`, `make test`, `make lint`, `make test-all`.

## Learn more

- [`examples/README.md`](examples/README.md) — every topic directory, with the full
  script/strategy tables and run commands
- [`examples/screening/README.md`](examples/screening/README.md) — screener CLI detail and
  chaining
- [`docs/`](docs/) — conceptual notes, the chaining roadmap, and screening research
- [`tests/README.md`](tests/README.md) — how the test suite is organized and run

All examples default to the `fake` adapter, so they run with no credentials. Swap
`--adapter dhan` (or any registered adapter) with real credentials for live broker
connectivity.
