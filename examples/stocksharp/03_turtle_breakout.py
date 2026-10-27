"""stocksharp/03_turtle_breakout: Turtle breakout system (port of StockSharp turtle sample).

Strategy: classical Donchian-channel breakout — buy when price breaks the
20-day high, sell when it breaks the 20-day low (the 55-day channel is the
System-2 alternative, selectable with --entry-window 55 --exit-window 20).
Risk per unit is ``N`` = the 20-day ATR; stops are ``2N`` trailing from the
entry extreme, and positions pyramid up to 4 units, adding one unit each time
price moves 1N in the trade's favour.

Runs offline and deterministic on synthetic bars (an oscillator with drift and
seeded noise, so both breakout directions and trend reversals occur).  Sizes are
abstract units — no lot size, margin or freeze limit is applied.  No
``Strategy`` subclass: a plain bar-by-bar simulation like the options examples.

Run::

    python examples/stocksharp/03_turtle_breakout.py --entry-window 20 --exit-window 10 --bars 200
    python examples/stocksharp/03_turtle_breakout.py --entry-window 55 --exit-window 20 --atr-period 20 --seed 7 --out turtle.json

Pitfalls::

    - Units are abstract (1 unit per signal); lot size, margin and freeze
      quantities are not applied — real turtle sizing risks 1% of equity per
      unit and caps total exposure across correlated markets.
    - Entries trigger on the close breakout and fill at the next bar's close, a
      full-bar lag; real breakout algorithms attempt intrabar fills at the
      extreme.
    - The 2N trailing stop ratchets only in the trade's favour and whipsaws in
      sideways regimes; ATR itself lags volatility regime changes.
    - Pyramiding adds exposure exactly as the trend extends — profitable in a
      sustained trend, costly in a reversal — and there is no correlation cap
      when trading many markets.
    - The synthetic oscillator is mean-reverting with drift, so breakout
      profitability here is path-dependent; real edge needs trending regimes.
"""

from __future__ import annotations

import random
import sys
from typing import Any

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]

MAX_UNITS = 4


def _simulate_bars(
    bars: int,
    seed: int = 42,
    base: float = 2000.0,
    trend1: float = 1.5,  # points per bar in regime 1 (up trend)
    trend2: float = -2.0,  # points per bar in regime 2 (down trend)
    noise: float = 0.75,
    regime1_bars: int = 90,
    regime2_bars: int = 60,
) -> list[dict[str, float]]:
    """Three-regime path: up trend -> down trend -> chop.

    Designed so a 20-day Donchian breakout fires in regime 1, pyramids as price
    advances, then the 2N trailing stop exits in regime 2. Fully deterministic
    given ``seed``.
    """
    rng = random.Random(seed)
    out: list[dict[str, float]] = []
    prev_close = base
    for i in range(bars):
        if i < regime1_bars:
            step = trend1
        elif i < regime1_bars + regime2_bars:
            step = trend2
        else:
            step = rng.choice([-0.6, 0.0, 0.6])  # chop
        close = round(prev_close + step + rng.gauss(0.0, noise), 2)
        high = max(prev_close, close) + abs(rng.gauss(0.0, 1.0))
        low = min(prev_close, close) - abs(rng.gauss(0.0, 1.0))
        out.append({"open": prev_close, "high": high, "low": low, "close": close, "volume": 1e6})
        prev_close = close
    return out


def _donchian(closes: list[float], index: int, window: int) -> tuple[float, float]:
    """Highest high / lowest low of closes over the trailing ``window`` bars."""
    window_closes = closes[max(0, index - window + 1) : index + 1]
    return max(window_closes), min(window_closes)


def _atr(bars: list[dict[str, float]], index: int, period: int) -> float:
    """Wilder-style ATR (simple average of true range over trailing ``period``)."""
    start = max(1, index - period + 1)
    trs = []
    for t in range(start, index + 1):
        b = bars[t]
        prev_close = bars[t - 1]["close"]
        tr = max(
            b["high"] - b["low"],
            abs(b["high"] - prev_close),
            abs(b["low"] - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def run(
    bars: int = 200,
    seed: int = 42,
    entry_window: int = 20,
    exit_window: int = 10,
    atr_period: int = 20,
    risk_per_unit: float = 0.01,
) -> dict[str, Any]:
    """Turtle breakout on synthetic bars; returns entries, adds, exits and P&L."""
    bars_list = _simulate_bars(bars, seed=seed)
    closes = [b["close"] for b in bars_list]

    position = 0  # units: positive long, negative short
    units = 0
    entry_price = 0.0
    add_levels: list[float] = []  # prices at which units were added
    stop_price: float | None = None
    highest_since_entry: float | None = None
    lowest_since_entry: float | None = None

    events: list[dict[str, Any]] = []
    pnl = 0.0

    for t in range(atr_period, bars):
        b = bars_list[t]
        # Channels are built from closes strictly before bar t: a breakout is the
        # current close exceeding the past window's extreme (never the close
        # itself).
        high_channel, low_channel = _donchian(closes, t - 1, entry_window)
        exit_high, exit_low = _donchian(closes, t - 1, exit_window)
        n = _atr(bars_list, t, atr_period)
        close = b["close"]

        if position == 0:
            # Breakout entry on close beyond the channel.
            if close > high_channel:
                position = 1
                entry_price = close
                units = 1
                stop_price = close - 2.0 * n
                highest_since_entry = close
                add_levels = [close]
                events.append(
                    {
                        "bar": t,
                        "close": close,
                        "side": "enter_long",
                        "channel_high": high_channel,
                        "units": units,
                        "stop": stop_price,
                    }
                )
            elif close < low_channel:
                position = -1
                entry_price = close
                units = 1
                stop_price = close + 2.0 * n
                lowest_since_entry = close
                add_levels = [close]
                events.append(
                    {
                        "bar": t,
                        "close": close,
                        "side": "enter_short",
                        "channel_low": low_channel,
                        "units": units,
                        "stop": stop_price,
                    }
                )
        else:
            if position > 0:
                highest_since_entry = max(highest_since_entry, close)
                # Pyramid: add one unit per 1N favourable move, capped at MAX_UNITS.
                next_add = add_levels[-1] + n
                if units < MAX_UNITS and close >= next_add:
                    units += 1
                    add_levels.append(close)
                    events.append(
                        {
                            "bar": t,
                            "close": close,
                            "side": "add_long",
                            "units": units,
                        }
                    )
                # Trailing stop: 2N below the highest close since entry.
                stop_price = max(stop_price or float("-inf"), highest_since_entry - 2.0 * n)
                if close <= stop_price or close < exit_low:
                    pnl += units * (close - entry_price)
                    events.append(
                        {
                            "bar": t,
                            "close": close,
                            "side": "exit_long",
                            "units": units,
                            "reason": "stop" if close <= stop_price else "channel",
                            "trade_pnl": units * (close - entry_price),
                        }
                    )
                    position = 0
                    units = 0
                    entry_price = 0.0
                    stop_price = None
                    highest_since_entry = None
                    add_levels = []
            else:
                lowest_since_entry = min(lowest_since_entry, close)
                next_add = add_levels[-1] - n
                if units < MAX_UNITS and close <= next_add:
                    units += 1
                    add_levels.append(close)
                    events.append(
                        {
                            "bar": t,
                            "close": close,
                            "side": "add_short",
                            "units": units,
                        }
                    )
                stop_price = min(stop_price or float("inf"), lowest_since_entry + 2.0 * n)
                if close >= stop_price or close > exit_high:
                    pnl += units * (entry_price - close)
                    events.append(
                        {
                            "bar": t,
                            "close": close,
                            "side": "exit_short",
                            "units": units,
                            "reason": "stop" if close >= stop_price else "channel",
                            "trade_pnl": units * (entry_price - close),
                        }
                    )
                    position = 0
                    units = 0
                    entry_price = 0.0
                    stop_price = None
                    lowest_since_entry = None
                    add_levels = []

    # Mark-to-market any open position at the last close.
    unrealised = 0.0
    if position > 0:
        unrealised = units * (closes[-1] - entry_price)
    elif position < 0:
        unrealised = units * (entry_price - closes[-1])

    return {
        "config": {
            "bars": bars,
            "seed": seed,
            "entry_window": entry_window,
            "exit_window": exit_window,
            "atr_period": atr_period,
            "max_units": MAX_UNITS,
        },
        "summary": {
            "events": events,
            "event_count": len(events),
            "realised_pnl": pnl,
            "unrealised_pnl": unrealised,
            "total_pnl": pnl + unrealised,
            "final_position": position,
            "final_units": units,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--bars", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--entry-window", type=int, default=20)
    parser.add_argument("--exit-window", type=int, default=10)
    parser.add_argument("--atr-period", type=int, default=20)
    parser.add_argument("--risk-per-unit", type=float, default=0.01)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(
        bars=args.bars,
        seed=args.seed,
        entry_window=args.entry_window,
        exit_window=args.exit_window,
        atr_period=args.atr_period,
        risk_per_unit=args.risk_per_unit,
    )

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
