"""production/02_live_trading_dhan: Live trading with the Dhan adapter.

Strategy: inline `SmaLive`, a 10/30 SMA crossover (`--strategy sma_crossover`).
Sketches how a live session would connect to Dhan and place orders. It is a template:
dry-run (the default) only echoes the config, and with `--no-dry-run` the connection
and session code is still commented out, so nothing connects or trades. It takes
Dhan credentials (client_id, access_token) as arguments.

Run::

    python examples/production/02_live_trading_dhan.py \
        --client-id XXX --access-token YYY --strategy sma_crossover

Pitfalls::

    - Template, not a trader: --no-dry-run returns status "template" and the adapter, session and
      risk limit code is commented out, so no connection is made, and max_position_pct and
      max_daily_loss are not enforced anywhere. There is no kill switch, no reconciliation and no
      market-hours guard here.
    - Secrets: --access-token on the command line lands in shell history and the process list, and
      the first four characters of the client id are echoed. Read credentials from the environment
      or a secret store, and expect tokens to expire and need rotation during a long run.
    - Order lifecycle: a timeout or reject is not proof an order did not reach the exchange. Without
      a client_order_id on every order a retry can double-send, so look up the broker's order book
      before resending, and expect partial fills, so a position can differ from the intent.
    - Clock and restarts: trading hours are 09:15 to 15:30 IST, so use timezone-aware IST rather
      than the server's local clock, and send nothing outside the session. The strategy starts flat
      after a restart even if the broker still holds shares, so sync positions before the first
      signal.
"""

from __future__ import annotations

import sys
from typing import Any, ClassVar

from honba.domain.instrument import InstrumentId
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]


class SmaLive(Strategy):
    """SMA crossover for live trading."""

    name: ClassVar[str] = "sma_live"
    warmup_bars: ClassVar[int] = 30

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
        elif fast_val < slow_val and position > 0:
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
            "status": "dry_run",
            "message": "Set --no-dry-run to connect to real Dhan (requires valid credentials)",
            "config": {
                "client_id": client_id[:4] + "****",
                "strategy": strategy,
                "capital": capital,
                "fast": fast,
                "slow": slow,
                "symbol": symbol,
            },
        }

    # Real live trading setup (commented for safety)
    # adapter = AdapterRegistry.get("dhan")
    # adapter.connect({"client_id": client_id, "access_token": access_token})
    #
    # config = SessionConfig(
    #     adapter="dhan",
    #     adapter_config={"client_id": client_id, "access_token": access_token},
    #     strategy=SmaLive(fast=fast, slow=slow, symbol=symbol),
    #     risk_limits={"max_position_pct": max_position_pct, "max_daily_loss": max_daily_loss},
    #     capital=capital,
    # )
    #
    # session = Session(config)
    # session.start()
    # return {"status": "started", "session_id": session.session_id}

    return {
        "status": "template",
        "message": "Live trading implementation requires honba.session.Session with real adapter",
        "config": {
            "client_id": client_id[:4] + "****",
            "strategy": strategy,
            "capital": capital,
            "fast": fast,
            "slow": slow,
            "symbol": symbol,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--client-id", type=str, required=True, help="Dhan client ID")
    parser.add_argument("--access-token", type=str, required=True, help="Dhan access token")
    parser.add_argument("--strategy", type=str, default="sma_crossover")
    parser.add_argument(
        "--initial-capital",
        "--capital",
        dest="capital",
        type=float,
        default=100_000.0,
        help="Initial capital in INR (default: 100000.0)",
    )
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--symbol", type=str, default="RELIANCE")
    parser.add_argument("--max-position-pct", type=float, default=0.1)
    parser.add_argument("--max-daily-loss", type=float, default=50_000.0)
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually connect to Dhan (requires real credentials)",
    )
    add_out_arg(parser)
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

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
