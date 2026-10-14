"""production/03_multi_account: Multiple accounts with isolated capital and aggregated P&L.

Strategy: inline `SmaMulti`, a 10/30 SMA crossover (`--strategy sma_crossover`), run
once per account. Each account keeps its own capital, risk limits and position
tracking; results are aggregated into a firm-wide P&L. Teaches account-level
isolation and aggregation; runs are offline and deterministic on synthetic bars.

Run::

    python production/03_multi_account.py --accounts config/accounts.yaml
    python production/03_multi_account.py  # demo with synthetic accounts

Pitfalls::

    - Isolation is only logical: each account is a separate replay with its own capital. They share
      no broker session, margin, rate limit or aggregate exposure, so two accounts on one symbol
      would double the real-market footprint and breach any combined limit this script never checks.
      No per-account risk limit is accepted or enforced.
    - Aggregation is a plain sum of final equity over summed capital, not time-weighted, and it
      ignores each account's brokerage, taxes and cash flows. Every account trades the same
      synthetic uptrend one share at a time, so the figures show the arithmetic, not any edge.
    - --accounts YAML is not validated: a missing key is a KeyError, and two entries with the same
      id are silently both counted. A file that does not exist is an error (it used to fall back to
      the demo accounts). Keep per-account credentials out of that file.
    - After a restart each account's position must be re-synced from its own broker account; the in-
      process fills computed here are not a ledger.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.domain.instrument import InstrumentId
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import add_out_arg, emit_json, make_parser
from honba_examples.metrics import fills_equity_curve
from honba_examples.synthetic import bar as synth_bar
from honba_examples.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]


class SmaMulti(Strategy):
    """SMA crossover for multi-account demo."""

    name = "sma_multi"
    warmup_bars = 30

    def __init__(self, fast: int = 10, slow: int = 30, symbol: str = "RELIANCE") -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.symbol = symbol
        self.iid = InstrumentId(symbol=symbol, exchange="NSE")

    def on_bar(self, bar) -> None:
        if bar.instrument_id != self.iid:
            return

        fast_val = self.fast.update(bar.close)
        slow_val = self.slow.update(bar.close)

        if fast_val is None or slow_val is None:
            return

        position = self.position(self.iid)
        if fast_val > slow_val and position <= 0:
            self.buy(self.iid, 1)
        elif fast_val < slow_val and position >= 0 and position > 0:
            self.sell(self.iid, position)


def _run_one_account(
    account_id: str, capital: float, symbol: str, fast: int, slow: int, bars: int
) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = [
        synth_bar(symbol, d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))
    ]

    from honba.strategies.testing import replay

    strat = SmaMulti(fast=fast, slow=slow, symbol=symbol)
    result = replay(strat, bars_list, fill_delay=1)

    equity_curve = fills_equity_curve(capital, result.fills, bars_list)

    final = equity_curve[-1] if equity_curve else capital
    return {
        "account_id": account_id,
        "capital": capital,
        "symbol": symbol,
        "fills": len(result.fills),
        "final_equity": final,
        "total_return": final - capital,
        "return_pct": (final - capital) / capital * 100,
        "equity_curve": equity_curve,
    }


def run(
    accounts: list[dict[str, Any]] | None = None,
    bars: int = 100,
) -> dict[str, Any]:
    accounts = accounts or [
        {"id": "acc_primary", "capital": 100_000.0, "symbol": "RELIANCE", "fast": 10, "slow": 30},
        {"id": "acc_secondary", "capital": 50_000.0, "symbol": "INFY", "fast": 5, "slow": 20},
        {"id": "acc_small", "capital": 25_000.0, "symbol": "TCS", "fast": 15, "slow": 40},
    ]

    results = []
    total_capital = 0.0
    total_final = 0.0
    total_return = 0.0

    for acc in accounts:
        res = _run_one_account(
            acc["id"], acc["capital"], acc["symbol"], acc["fast"], acc["slow"], bars
        )
        results.append(res)
        total_capital += res["capital"]
        total_final += res["final_equity"]
        total_return += res["total_return"]

    overall_return_pct = (total_return / total_capital * 100) if total_capital else 0.0

    return {
        "config": {"accounts": accounts, "bars": bars},
        "accounts": [{k: v for k, v in r.items() if k != "equity_curve"} for r in results],
        "aggregated": {
            "total_capital": total_capital,
            "total_final": total_final,
            "total_return": total_return,
            "overall_return_pct": overall_return_pct,
            "account_count": len(results),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--accounts", type=Path, default=None, help="YAML with account list")
    parser.add_argument("--bars", type=int, default=100)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    accounts = None
    if args.accounts is not None:
        if not args.accounts.is_file():
            parser.error(f"--accounts file not found: {args.accounts}")
        import yaml  # pyright: ignore[reportMissingModuleSource]

        accounts = yaml.safe_load(args.accounts.read_text())

    result = run(accounts=accounts, bars=args.bars)

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
