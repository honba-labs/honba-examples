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

1. [`basic/`](basic/) — minimal backtest
2. [`candles/`](candles/) — bar construction
3. [`storage/`](storage/) — persistence
4. [`universes/`](universes/) — instrument universes
5. [`strategies/`](strategies/) — reference strategies
6. [`backtesting/`](backtesting/) — end-to-end runs
7. [`mutual_funds/`](mutual_funds/) — NAV / MF handling
8. [`options/`](options/) — options chain
9. [`ai_research/`](ai_research/) — research loop
10. [`production/`](production/) — deployment
