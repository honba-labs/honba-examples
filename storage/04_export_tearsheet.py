"""04_export_tearsheet: run a deterministic backtest and export it as JSON and Markdown.

A research artifact has two audiences: a machine (a validation pipeline, an LLM
agent) and a human. The run below is fully synthetic - the bars come from
``tests/synthetic.py``, not from a Parquet store - so the example needs no
market data, no network and no clock. A minimal ``Strategy`` buys one position
on the first test-window session and exits the session before the last; fills
land at the next session's open and ``run_portfolio_backtest`` does the
accounting.

``--out-dir`` receives two files: ``tearsheet.json`` (schema
``honba-examples/tearsheet/v1`` wrapping the run's ``to_dict()`` plus the
``canonical_hash`` of that payload, so a consumer can verify the document it
re-read) and ``tearsheet.md``, a short human summary of the same numbers. Both
are byte-identical between two runs over the same inputs.

Run::

    python storage/04_export_tearsheet.py
    python storage/04_export_tearsheet.py --out-dir /tmp/tearsheet --out report.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from honba.domain.bar import Bar
from honba.strategies.base import Strategy

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.backtest import BacktestRun, canonical_hash, run_portfolio_backtest
from honba_examples.base import ts_to_date
from honba_examples.jsonable import jsonable
from tests.synthetic import bar, weekdays

SCHEMA = "honba-examples/tearsheet/v1"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "output" / "04_export_tearsheet"

SYMBOL = "DEMO"
EXCHANGE = "NSE"
QUANTITY = 10.0
CAPITAL_MINOR = 1_000_000  # 10,000 rupees in paise
SETTLEMENT_DAYS = 2  # explicit what-if; the core default for 2026 sessions is T+1

DAYS = weekdays(dt.date(2026, 6, 1), 10)
TEST_START = DAYS[3]
TEST_END = DAYS[-1]
SELL_DAY = DAYS[-2]  # an intent on the last session would never fill: orders fill next open


class BuyFirstSellLast(Strategy):
    """Buy on the first test-window session and exit the session before the last.

    Warm-up sessions are ignored rather than traded (the runner's warm-up gate would only
    release those orders), and the exit is scheduled one session early because
    orders fill at the *next* session's open.
    """

    name = "buy_first_sell_last"

    def __init__(self, test_start: dt.date, sell_day: dt.date) -> None:
        self.test_start = test_start
        self.sell_day = sell_day
        self.entered = False
        self.exited = False

    def on_bar(self, bar: Bar) -> None:
        day = ts_to_date(bar.ts)
        if day < self.test_start or self.exited or self.busy(bar.instrument_id):
            return
        if not self.entered:
            self.buy(bar.instrument_id, QUANTITY)
            self.entered = True
        elif day >= self.sell_day:
            quantity = self.position(bar.instrument_id)
            if quantity > 0:
                self.sell(bar.instrument_id, quantity)
                self.exited = True


def synthetic_bars() -> list[Bar]:
    """Ten rising weekday sessions; ``bar()`` stamps each at its session open."""
    return [bar(SYMBOL, day, 100.0 + i, close=100.0 + i + 0.5) for i, day in enumerate(DAYS)]


def run_backtest() -> BacktestRun:
    """One deterministic run: synthetic bars, one buy, one sell, T+2 settlement."""
    return run_portfolio_backtest(
        BuyFirstSellLast(TEST_START, SELL_DAY),
        synthetic_bars(),
        test_start=TEST_START,
        test_end=TEST_END,
        capital_minor=CAPITAL_MINOR,
        settlement_days=SETTLEMENT_DAYS,
    )


def render_markdown(doc: dict[str, Any]) -> str:
    """Human half of the export: title, metrics table, fill count, equity endpoints."""
    run = doc["run"]
    metrics = run["metrics"]
    curve = run["equity_curve"]
    lines = [
        f"# Tearsheet: {BuyFirstSellLast.name} ({SYMBOL})",
        "",
        f"Schema `{SCHEMA}` - hash `{doc['canonical_hash']}`",
        (
            f"Test window {curve[0]['date']} to {curve[-1]['date']} - {len(curve)} sessions "
            f"- {metrics['n_fills']} fills"
        ),
        "",
        f"Fills: {metrics['n_fills']}",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
        *(f"| {name} | {value} |" for name, value in metrics.items()),
        "",
        "## Equity curve (first and last session)",
        "",
        "| date | equity_minor | cash_minor | positions_value_minor |",
        "| --- | --- | --- | --- |",
        *(
            f"| {p['date']} | {p['equity_minor']} | {p['cash_minor']} | "
            f"{p['positions_value_minor']} |"
            for p in (curve[0], curve[-1])
        ),
        "",
    ]
    return "\n".join(lines)


def run(out_dir: str | Path | None = None) -> dict[str, Any]:
    """Backtest, write ``tearsheet.json`` and ``tearsheet.md``, and report what landed.

    ``out_dir`` defaults to ``<repo>/output/04_export_tearsheet``; tests pass a temp
    directory, so nothing outside this repo is ever written.
    """
    out = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    result = run_backtest()
    payload = result.to_dict()
    doc = {"schema": SCHEMA, "run": payload, "canonical_hash": canonical_hash(payload)}
    (out / "tearsheet.json").write_text(
        json.dumps(jsonable(doc), indent=2) + "\n", encoding="utf-8"
    )
    (out / "tearsheet.md").write_text(render_markdown(doc), encoding="utf-8")

    files = [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in (out / "tearsheet.json", out / "tearsheet.md")
    ]
    return jsonable(
        {
            "out_dir": str(out),
            "files": files,
            "metrics": result.metrics(),
            "hash": doc["canonical_hash"],
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="tearsheet directory (default: <repo>/output/04_export_tearsheet)",
    )
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON report here")
    args = parser.parse_args(argv)

    try:
        result = run(args.out_dir)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))

    text = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
