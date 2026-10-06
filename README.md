# honba-examples

## Setup

The examples import `honba` (the core repo) and a small shared helper package,
`honba_examples`, that lives in this repo.

```bash
pip install -e ../honba/python      # core (or however you install honba)
pip install -e ".[dev]"             # this repo: honba_examples + pytest/ruff
```

Scripts also run from a plain checkout without the editable install: those that
need `honba_examples` add the repo root to `sys.path` when the package is not
installed.

Some examples load strategies from the sibling `honba-strategies` catalog. They
look for it at `--strategies-dir`, then `$HONBA_STRATEGIES_DIR`, then
`../honba-strategies` next to this repo.

```bash
pytest -q                 # unit + integration tests (no network, synthetic data)
ruff check . && ruff format --check honba_examples tests
```

## Examples

1. [`basic/`](basic/) — broker adapters (01–05) + README
2. [`candles/`](candles/) — bar construction (01–04) + README
3. [`storage/`](storage/) — persistence (01–04) + README
4. [`universes/`](universes/) — instrument universes (01–08) + README
5. [`strategies/`](strategies/) — reference strategies (01–05) + README
6. [`backtesting/`](backtesting/) — end-to-end runs (01–06) + README
7. [`mutual_funds/`](mutual_funds/) — NAV / MF handling (01–04) + README
8. [`options/`](options/) — options chain (01–05) + README
9. [`ai_research/`](ai_research/) — research loop (01, 05 runnable) + README
10. [`production/`](production/) — deployment (01–05) + README

All examples use the `fake` adapter by default (offline, deterministic). Swap
`--adapter dhan` (or any registered adapter) with real credentials for live
broker connectivity.