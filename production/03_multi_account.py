"""production/03_multi_account: Multiple accounts with isolated capital and aggregated P&L.

Strategy: inline `SmaMulti`, a 10/30 SMA crossover (`--strategy sma_crossover`), run
once per account. Each account keeps its own capital, risk limits and position
tracking; results are aggregated into a firm-wide P&L. Teaches account-level
isolation and aggregation; runs are offline and deterministic on synthetic bars.

Run::

    python production/03_multi_account.py --accounts config/accounts.yaml
    python production/03_multi_account.py  # demo with synthetic accounts
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.domain.instrument import InstrumentId, InstrumentKind
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from tests.synthetic import bar as synth_bar
from tests.synthetic import weekdays as weekdays_func

__all__ = ["main", "run"]


class SmaMulti(Strategy):
    """SMA crossover for multi-account demo."""

    name: str = "sma_multi"
    warmup_bars: int = 30

    def __init__(self, fast: int = 10, slow: int = 30, symbol: str = "RELIANCE") -> None:
        super().__init__()
        self.fast = Sma(fast)
        self.slow = Sma(slow)
        self.symbol = symbol
        self.iid = InstrumentId(symbol=symbol, exchange="NSE", kind=InstrumentKind.EQUITY)

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


def _run_one_account(account_id: str, capital: float, symbol: str, fast: int, slow: int, bars: int) -> dict[str, Any]:
    start = dt.date(2026, 1, 1)
    bars_list = [synth_bar(symbol, d, 2500.0 + i * 0.5) for i, d in enumerate(weekdays_func(start, bars))]

    from honba.strategies.testing import replay

    strat = SmaMulti(fast=fast, slow=slow, symbol=symbol)
    result = replay(strat, bars_list, fill_delay=1)

    cash = capital
    position = 0.0
    equity_curve = [capital]
    for fill in result.fills:
        notional = fill.quantity * fill.price
        if fill.side.value == "buy":
            cash -= notional
            position += fill.quantity
        else:
            cash += notional
            position -= fill.quantity
        equity = cash + position * fill.price
        equity_curve.append(equity)

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
        res = _run_one_account(acc["id"], acc["capital"], acc["symbol"], acc["fast"], acc["slow"], bars)
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
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--accounts", type=Path, default=None, help="YAML with account list")
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    accounts = None
    if args.accounts and args.accounts.exists():
        import yaml

        accounts = yaml.safe_load(args.accounts.read_text())

    result = run(accounts=accounts, bars=args.bars)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())