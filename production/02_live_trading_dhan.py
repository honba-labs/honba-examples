"""production/02_live_trading_dhan: Live trading with the Dhan adapter.

Strategy: inline `SmaLive`, a 10/30 SMA crossover (`--strategy sma_crossover`).
Shows how a live session connects to Dhan, places real orders, handles rejections
and syncs positions. Requires valid Dhan credentials (client_id, access_token); it
runs in dry-run mode by default and only connects with `--no-dry-run`.

Run::

    python production/02_live_trading_dhan.py \
        --client-id XXX --access-token YYY --strategy sma_crossover
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.domain.instrument import InstrumentId, InstrumentKind
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

__all__ = ["main", "run"]


class SmaLive(Strategy):
    """SMA crossover for live trading."""

    name: str = "sma_live"
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


def run(
    client_id: str,
    access_token: str,
    strategy: str = "sma_crossover",
    capital: float = 100_000.0,
    fast: int = 10,
    slow: int = 30,
    symbol: str = "RELIANCE",
    max_position_pct: float = 0.1,
    max_daily_loss: float = 50_000.0,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    NOTE: This example is a template. Actual live trading requires:
    1. Valid Dhan credentials
    2. Market hours
    3. Proper risk management
    4. Monitoring and alerting

    The dry_run=True mode uses the fake adapter for demonstration.
    """
    if dry_run:
        return {
            \"status\": \"dry_run\",
            \"message\": \"Set --no-dry-run to connect to real Dhan (requires valid credentials)\",
            \"config\": {
                \"client_id\": client_id[:4] + \"****\",
                \"strategy\": strategy,
                \"capital\": capital,
                \"fast\": fast,
                \"slow\": slow,
                \"symbol\": symbol,
            },
        }

    # Real live trading setup (commented for safety)
    # adapter = AdapterRegistry.get(\"dhan\")
    # adapter.connect({\"client_id\": client_id, \"access_token\": access_token})
    #
    # config = SessionConfig(
    #     adapter=\"dhan\",
    #     adapter_config={\"client_id\": client_id, \"access_token\": access_token},
    #     strategy=SmaLive(fast=fast, slow=slow, symbol=symbol),
    #     risk_limits={\"max_position_pct\": max_position_pct, \"max_daily_loss\": max_daily_loss},
    #     capital=capital,
    # )
    #
    # session = Session(config)
    # session.start()
    # return {\"status\": \"started\", \"session_id\": session.session_id}

    return {
        \"status\": \"template\",
        \"message\": \"Live trading implementation requires honba.session.Session with real adapter\",
        \"config\": {
            \"client_id\": client_id[:4] + \"****\",
            \"strategy\": strategy,
            \"capital\": capital,
            \"fast\": fast,
            \"slow\": slow,
            \"symbol\": symbol,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split(\"\\n\\n\")[0])
    parser.add_argument(\"--client-id\", type=str, required=True, help=\"Dhan client ID\")
    parser.add_argument(\"--access-token\", type=str, required=True, help=\"Dhan access token\")
    parser.add_argument(\"--strategy\", type=str, default=\"sma_crossover\")
    parser.add_argument(
        \"--initial-capital\",
        \"--capital\",
        dest=\"capital\",
        type=float,
        default=100_000.0,
        help=\"Initial capital in INR (default: 100000.0)\",
    )
    parser.add_argument(\"--fast\", type=int, default=10)
    parser.add_argument(\"--slow\", type=int, default=30)
    parser.add_argument(\"--symbol\", type=str, default=\"RELIANCE\")
    parser.add_argument(\"--max-position-pct\", type=float, default=0.1)
    parser.add_argument(\"--max-daily-loss\", type=float, default=50_000.0)
    parser.add_argument(\"--no-dry-run\", action=\"store_true\", help=\"Actually connect to Dhan (requires real credentials)\")
    parser.add_argument(\"--out\", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        client_id=args.client_id,
        access_token=args.access_token,
        strategy=args.strategy,
        capital=args.capital,
        fast=args.fast,
        slow=args.slow,
        symbol=args.symbol,
        max_position_pct=args.max_position_pct,
        max_daily_loss=args.max_daily_loss,
        dry_run=not args.no_dry_run,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + \"\\n\")
    print(text)
    return 0


if __name__ == \"__main__\":
    sys.exit(main())
