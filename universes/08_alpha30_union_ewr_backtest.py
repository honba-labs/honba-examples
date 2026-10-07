"""Example 08: Equal-weight rebalancer over Alpha-30 universe.

Portfolio rebalancing strategy that maintains equal weights across the Alpha-30
basket on a fixed calendar schedule. Runs over NSE daily bars from the Parquet store.

* Cash pool          one shared pool across the basket
* Session calendar   ``day0`` = first session on/after ``--start``. Day 0 buys the
  basket. Then every ``rebalance_days`` *calendar* days from ``--start`` (rolled
  forward to the first session on/after the scheduled date) rebalances.
* Equal weight       ``pool = cash + sum(qty * close)`` over the names that printed
  that session; ``target = pool / n``; ``desired = floor(target / close)``. Whole
  shares only, so a name whose single share costs more than its target is simply
  never held and its money stays as cash.
* Order of trades    all sells first (they raise the cash), then buys sorted by
  largest rupee shortfall first. Each buy is capped at
  ``floor(cash / (close * (1 + fee)))``, so cash never goes negative.
* Costs              ``fee`` fraction of traded notional per side, deducted from
  cash. No slippage, no brokerage/STT - an illustrative model, not real CNC costs.
* Filling            at that session's close.

Run::

    python 08_alpha30_union_ewr_backtest.py
    python 08_alpha30_union_ewr_backtest.py --start 2026-01-01 --end 2026-09-23
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import DateInterval

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.base import HonbaExample, ts_to_date
from honba_examples.metrics import curve_metrics, exposure_metrics
from honba_examples.output import (
    OutputOptions,
    print_changes,
    print_data_notes,
    print_equity_summary,
    print_holdings,
    print_metrics,
)

# ---------------------------------------------------------------------------
# Basket: Alpha-30 universe constituents
# ---------------------------------------------------------------------------
BASKET: tuple[str, ...] = (
    "ABCAPITAL",
    "ADANIENSOL",
    "ADANIGREEN",
    "ADANIPOWER",
    "ASHOKLEY",
    "AUBANK",
    "AUROPHARMA",
    "BHARATFORG",
    "BHEL",
    "BSE",
    "CUMMINSIND",
    "FEDERALBNK",
    "GVT&D",
    "HINDALCO",
    "IDEA",
    "INDIANB",
    "LAURUSLABS",
    "LTF",
    "MAHABANK",
    "MCX",
    "MOTHERSON",
    "NATIONALUM",
    "NYKAA",
    "PAYTM",
    "POLYCAB",
    "POWERINDIA",
    "SAIL",
    "SHRIRAMFIN",
    "UNIONBANK",
    "VEDL",
)

STARTING_CAPITAL = 1_000_000.0
FEE_RATE = 0.001
REBALANCE_DAYS = 15


@dataclass
class SimResult:
    """Outcome of one equal-weight rebalance simulation."""

    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    rebalances: list[dict[str, Any]] = field(default_factory=list)
    rebalance_changes: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    final_holdings: dict[str, int] = field(default_factory=dict)
    never_held: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)


def rebalance_orders(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[float, list[dict[str, Any]]]:
    """Bring a basket back to equal weight against one shared cash pool.

    Port of ``jesse.research.portfolio_rebalance.rebalance_orders``. ``qty`` is
    mutated in place to the new positions.

    ``closes`` holds only the symbols that printed this session. A symbol held but
    absent from ``closes`` is left untouched and excluded from both the pool and the
    target: its last close is stale, so trading against it would be guesswork. A
    symbol in ``closes`` but not yet held counts as 0 shares.

    Sells run before buys so the cash they raise funds the buys, whole shares only,
    largest rupee shortfall first. A buy is capped at what cash allows once the fee
    is included, so a short buy simply leaves the remainder as cash.
    """
    active = sorted(closes)
    if not active:
        return cash, []

    for s in active:
        qty.setdefault(s, 0)

    pool = cash + sum(qty[s] * closes[s] for s in active)
    target = pool / len(active)
    desired = {s: math.floor(target / closes[s]) for s in active}

    trades: list[dict[str, Any]] = []
    for s in active:
        sells, cash = _sell_excess(s, qty, desired, closes, cash, fee)
        trades.extend(sells)

    for s in active:
        buys, cash = _buy_shortfall(s, qty, desired, closes, cash, fee)
        trades.extend(buys)

    # Guard against float dust from repeated fee arithmetic.
    return max(cash, 0.0), trades


def sell_excess(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[float, list[dict[str, Any]]]:
    """Sell-only leg: trim every holding that sits above its equal-weight target.

    Split out from :func:`rebalance_orders` so the T+2 path can run the sell leg on
    the rebalance session and defer the buy leg until proceeds settle.
    """
    active = sorted(closes)
    if not active:
        return cash, []
    pool = cash + sum(qty.get(s, 0) * closes[s] for s in active)
    desired = {s: math.floor((pool / len(active)) / closes[s]) for s in active}

    trades: list[dict[str, Any]] = []
    for s in active:
        sells, cash = _sell_excess(s, qty, desired, closes, cash, fee)
        trades.extend(sells)
    return max(cash, 0.0), trades


def buy_shortfall(
    qty: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[float, list[dict[str, Any]]]:
    """Buy-only leg: top up holdings below target from cash that is already usable.

    Target is recomputed from the pool at *this* session's closes, which is what
    makes the deferred leg self-correcting: a T+2 gap means prices have moved, and
    sizing against the stale target would leave the basket off equal weight.
    """
    active = sorted(closes)
    if not active:
        return cash, []
    pool = cash + sum(qty.get(s, 0) * closes[s] for s in active)
    desired = {s: math.floor((pool / len(active)) / closes[s]) for s in active}

    trades: list[dict[str, Any]] = []
    shortfalls = sorted(
        (s for s in active if desired[s] > qty.get(s, 0)),
        key=lambda s: (-(desired[s] - qty.get(s, 0)) * closes[s], s),
    )
    for s in shortfalls:
        buys, cash = _buy_shortfall(s, qty, desired, closes, cash, fee)
        trades.extend(buys)
    return max(cash, 0.0), trades


def _sell_excess(
    s: str,
    qty: dict[str, int],
    desired: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[list[dict[str, Any]], float]:
    held = qty.get(s, 0)
    if held <= desired[s]:
        return [], cash
    n = held - desired[s]
    notional = n * closes[s]
    qty[s] = held - n
    return [_trade(s, "sell", n, closes[s], fee)], cash + notional - notional * fee


def _buy_shortfall(
    s: str,
    qty: dict[str, int],
    desired: dict[str, int],
    closes: dict[str, float],
    cash: float,
    fee: float,
) -> tuple[list[dict[str, Any]], float]:
    held = qty.get(s, 0)
    if desired[s] <= held:
        return [], cash
    # Cash cap: a short buy just leaves the remainder as cash.
    affordable = math.floor(cash / (closes[s] * (1 + fee)))
    n = min(desired[s] - held, affordable)
    if n <= 0:
        return [], cash
    notional = n * closes[s]
    qty[s] = held + n
    return [_trade(s, "buy", n, closes[s], fee)], cash - notional - notional * fee


def _trade(symbol: str, side: str, n: int, price: float, fee: float) -> dict[str, Any]:
    notional = n * price
    return {
        "symbol": symbol,
        "side": side,
        "qty": n,
        "price": price,
        "notional": notional,
        "fee": notional * fee,
    }


def trade_changes(trades: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    """Net share change per symbol from fills: ``(adds, sells)``, each ``{symbol: qty}``."""
    net: dict[str, int] = {}
    for t in trades:
        signed = t["qty"] if t["side"] == "buy" else -t["qty"]
        net[t["symbol"]] = net.get(t["symbol"], 0) + signed
    adds = {s: q for s, q in sorted(net.items()) if q > 0}
    sells = {s: -q for s, q in sorted(net.items()) if q < 0}
    return adds, sells


def format_changes(adds: dict[str, int], sells: dict[str, int]) -> str:
    """``adds [AAA+3, ZED+10]  sells [MID-5]``: symbols sorted, ``[]`` when empty."""
    add_text = ", ".join(f"{s}+{q}" for s, q in sorted(adds.items()))
    sell_text = ", ".join(f"{s}-{q}" for s, q in sorted(sells.items()))
    return f"adds [{add_text}]  sells [{sell_text}]"


def rebalance_changes(rebalances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per rebalance, from its fills. A settlement-delayed buy leg is folded
    into the rebalance whose sell leg funded it."""
    groups: list[dict[str, Any]] = []
    for r in rebalances:
        if r.get("leg") == "buy" and groups:
            groups[-1]["trades"].extend(r["trades"])
        else:
            groups.append({"date": r["date"], "trades": list(r["trades"])})
    out = []
    for g in groups:
        adds, sells = trade_changes(g["trades"])
        out.append({"date": g["date"], "adds": adds, "sells": sells})
    return out


def rebalance_sessions(
    calendar: list[dt.date], start: dt.date, rebalance_days: int
) -> set[dt.date]:
    """Map each ``start + k*rebalance_days`` (k >= 1) to the first session on/after it.

    Port of ``portfolio_rebalance._rebalance_sessions``. Several scheduled dates
    landing on one session rebalance only once, and none may coincide with day 0.
    """
    sessions: set[dt.date] = set()
    scheduled = start + dt.timedelta(days=rebalance_days)
    i = 0
    while scheduled <= calendar[-1]:
        while calendar[i] < scheduled:
            i += 1
        if calendar[i] != calendar[0]:
            sessions.add(calendar[i])
        scheduled += dt.timedelta(days=rebalance_days)
    return sessions


def simulate(
    bars: list[Bar],
    symbols: list[str],
    *,
    start: dt.date,
    capital: float = STARTING_CAPITAL,
    rebalance_days: int = REBALANCE_DAYS,
    fee: float = FEE_RATE,
    settlement_days: int = 0,
) -> SimResult:
    """Run the equal-weight rebalance over Parquet-store bars.

    Port of ``portfolio_rebalance.simulate``, driven by ``{symbol: {date: close}}``
    instead of Jesse's candle database.

    ``settlement_days`` selects the execution sequence:

    * ``0`` - Jesse's model: one session does the sell leg and the buy leg back to
      back, spending the proceeds immediately.
    * ``2`` - Indian delivery settlement (T+2): the rebalance session sells only, and
      the buy leg runs ``settlement_days`` sessions later, when the proceeds have
      actually settled. Cash from an unsettled sale is not spendable, so the buy leg
      is capped by real cash plus the settled amount.
    """
    if capital <= 0:
        raise ValueError("capital must be positive")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be at least 1")
    if settlement_days < 0:
        raise ValueError("settlement_days must be >= 0")
    if not 0 <= fee < 1:
        raise ValueError("fee must be in [0, 1)")

    # closes[symbol] = {session_date: close}, only on/after `start`, like Jesse's
    # `closes = {s: {d: c for d, c in series if d >= start_date}}`. Every requested
    # symbol is seeded up front so the membership test below is a real filter
    # rather than a growing-key lookup on a defaultdict.
    wanted = set(symbols)
    closes: dict[str, dict[dt.date, float]] = {s: {} for s in wanted}
    for bar in bars:
        sym = bar.instrument_id.symbol
        if sym not in wanted:
            continue
        day = ts_to_date(bar.ts)
        if day >= start:
            closes[sym][day] = float(bar.close)

    symbols = [s for s in symbols if closes[s]]
    result = SimResult()
    if not symbols:
        result.missing_data = [s for s in symbols if not closes[s]]
        return result

    calendar = sorted({d for by_date in closes.values() for d in by_date})
    day0 = calendar[0]
    sessions = rebalance_sessions(calendar, start, rebalance_days)

    qty: dict[str, int] = {s: 0 for s in symbols}
    last_close: dict[str, float] = {}
    first_price: dict[str, float] = {}
    cash = float(capital)
    ever_held: set[str] = set()

    # T+2: a buy leg queued on this index executes `settlement_days` sessions later,
    # out of proceeds that are only spendable once they have settled.
    pending_buys: dict[int, bool] = {}

    for idx, session in enumerate(calendar):
        today = {s: closes[s][session] for s in symbols if session in closes[s]}
        last_close.update(today)
        for s, p in today.items():
            first_price.setdefault(s, p)

        # Settle any buy leg whose T+2 window has closed, before deciding anything new.
        if settlement_days and pending_buys.pop(idx - settlement_days, False) and today:
            cash, buys = buy_shortfall(qty, today, cash, fee)
            if buys:
                result.rebalances.append(
                    {
                        "date": session.isoformat(),
                        "value_before": None,
                        "trades": buys,
                        "cash_after": cash,
                        "leg": "buy",
                    }
                )

        if session == day0 or session in sessions:
            value_before = cash + sum(qty[s] * last_close[s] for s in qty if s in last_close)
            # Day 0 is an opening purchase funded by capital already in hand, so
            # there is no sale to settle and both legs run together.
            if settlement_days and session != day0:
                # Sell leg now; queue the buy leg for T+2.
                cash, trades = sell_excess(qty, today, cash, fee)
                pending_buys[idx + settlement_days] = True
                leg = "sell"
            else:
                cash, trades = rebalance_orders(qty, today, cash, fee)
                leg = "both"
            result.rebalances.append(
                {
                    "date": session.isoformat(),
                    "value_before": value_before,
                    "trades": trades,
                    "cash_after": cash,
                    "leg": leg,
                }
            )

        ever_held.update(s for s, q in qty.items() if q > 0)
        value = cash + sum(q * last_close[s] for s, q in qty.items() if q)
        result.equity_curve.append({"date": session.isoformat(), "value": value, "cash": cash})

    result.rebalance_changes = rebalance_changes(result.rebalances)
    traded_notional = sum(t["notional"] for r in result.rebalances for t in r["trades"])
    total_fees = sum(t["fee"] for r in result.rebalances for t in r["trades"])

    n_rebalances = len({r["date"] for r in result.rebalances if r.get("leg") != "buy"}) - 1
    exposure = exposure_metrics(result.equity_curve, traded_notional)
    result.metrics = {
        **curve_metrics(result.equity_curve, capital),
        "n_rebalances": n_rebalances,
        "total_fees": total_fees,
        "turnover": exposure["turnover"],
        "avg_cash_pct": exposure["avg_cash_pct"],
        "n_fills": sum(len(r["trades"]) for r in result.rebalances),
    }
    result.final_holdings = {s: q for s, q in qty.items() if q}
    result.never_held = [s for s in sorted(symbols) if s not in ever_held and s in first_price]
    return result


def print_summary(result: SimResult, opts: OutputOptions | None = None) -> None:
    """Print the simulation summary through the shared example output presets."""
    opts = opts or OutputOptions()
    metrics = dict(result.metrics)
    metrics["traded_notional"] = sum(t["notional"] for r in result.rebalances for t in r["trades"])
    print_metrics(metrics, opts)
    print_changes(result.rebalances, opts)
    print_holdings(result.final_holdings, opts)
    print_equity_summary(result.equity_curve, opts)
    print_data_notes(opts, missing_data=result.missing_data, never_held=result.never_held)


# ---------------------------------------------------------------------------
# Example entry point
# ---------------------------------------------------------------------------
class Alpha30EWRExample(HonbaExample):
    """Equal-weight rebalance over the Alpha-30 universe."""

    universe_name: str = "nifty50"  # unused; the basket is pinned to BASKET
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date = dt.date(2026, 1, 1)
    end_date: dt.date = dt.date(2026, 9, 23)
    initial_capital: float = STARTING_CAPITAL
    warmup_days: int = 0  # the strategy has no indicators, so no warmup is needed
    fee: float = FEE_RATE
    rebalance_days: int = REBALANCE_DAYS
    settlement_days: int | None = None

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--fee", type=float, default=self.fee, help="Fraction of traded notional, per side"
        )
        parser.add_argument(
            "--rebalance-days",
            type=int,
            default=self.rebalance_days,
            help="Calendar days between rebalances",
        )
        parser.add_argument(
            "--settlement-days",
            type=int,
            default=None,
            help="Sessions between a sell and the buys it funds. Omit to use the core "
            "market pack's cycle as of --start (NSE: T+2 before 2023-01-27, T+1 from then); "
            "an explicit value always wins (pass 2 for the old fixed T+2, 0 for the "
            "same-session model)",
        )

    @staticmethod
    def engine_settlement_days(exchange: str, as_of: dt.date | None = None) -> int:
        """Settlement days for the exchange on ``as_of``, from the core market pack.

        ``honba.markets.india.settlement`` is date-aware: NSE/BSE equities settle T+2
        before 2023-01-27 and T+1 from then. ``--settlement-days`` overrides it.
        """
        from honba.markets.india.settlement import settlement_days_for

        return settlement_days_for(exchange, as_of=as_of)

    def resolve_settlement_days(self) -> int:
        """The explicit ``--settlement-days`` if given, else the cycle as of ``start_date``."""
        if self.settlement_days is not None:
            return self.settlement_days
        return self.engine_settlement_days(self.exchange, as_of=self.start_date)

    def run(self) -> SimResult:
        instruments = [InstrumentId(sym, self.exchange) for sym in BASKET]
        interval = DateInterval(self.start_date, self.end_date + dt.timedelta(days=1))

        bars: list[Bar] = []
        available: set[str] = set()
        for iid in instruments:
            series = self.store.read(iid, self.timeframe, interval)
            bars.extend(series)
            if series:
                available.add(iid.symbol)

        bars.sort(key=lambda b: (b.ts, b.instrument_id.symbol))
        missing = [s for s in BASKET if s not in available]
        print(
            f"[Bars] {len(bars):,} bars across {len(available)}/{len(BASKET)} symbols, "
            f"{self.start_date} → {self.end_date}"
        )
        if missing:
            print(f"[Data] missing bars for {len(missing)}: {', '.join(missing)}")

        settlement_days = self.resolve_settlement_days()
        result = simulate(
            bars,
            list(BASKET),
            start=self.start_date,
            capital=self.initial_capital,
            rebalance_days=self.rebalance_days,
            fee=self.fee,
            settlement_days=settlement_days,
        )
        result.missing_data = missing
        print(
            f"[Model] settlement T+{settlement_days} "
            + (
                f"(core default for {self.exchange} as of {self.start_date})"
                if self.settlement_days is None
                else f"(sells fund buys {settlement_days} sessions later)"
                if settlement_days
                else "(same-session sell and buy)"
            )
        )
        print_summary(result, self.output)
        return result


def main() -> None:
    Alpha30EWRExample().main()


if __name__ == "__main__":
    main()
