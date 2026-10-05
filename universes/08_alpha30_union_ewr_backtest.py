"""Example 08: Jesse-parity equal-weight rebalancer (compare against Jesse directly).

Reproduces the Jesse run in
``jesse.git/.jesse-project/out/ewr-500000/{equity,trades}.csv`` inside Honba so the
two engines can be diffed session by session.

The Jesse artefact was produced by ``jesse/research/portfolio_rebalance.py`` (driven
by ``jesse/docs/examples/equal_weight_rebalance.py``). This example is a deliberate
port of ``simulate()`` / ``rebalance_orders()``, so the rules are copied rather than
re-derived:

* Cash pool          one shared pool across the basket. Jesse's engine runs one
  strategy per route with its own balance, which cannot express a shared pool, so
  it simulates the portfolio directly on daily closes.
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

Honba-side differences, all deliberate:

* Bars come from the Parquet store, Jesse's from its candle database. Four basket
  members (AUROPHARMA, MAHABANK, MOTHERSON, UNIONBANK) have no bars in the Parquet
  store, so they cannot be traded here. Jesse has them; that alone shifts the fill
  count and the equal-weight target.
* Jesse prices the portfolio at each session's close, so a rebalance is decided
  *after* the session is complete. A bar-driven engine sees one bar at a time, so
  this triggers on the first bar of the *next* session and submits limit orders at
  the completed session's closes. Same prices, same quantities; the trade is
  timestamped with the session it belongs to rather than the next one.

Run::

    python 08_alpha30_union_ewr_backtest.py
    python 08_alpha30_union_ewr_backtest.py --start 2026-01-01 --finish 2026-09-23
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import DateInterval

try:
    from honba_examples.base import HonbaExample, ts_to_date
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from honba_examples.base import HonbaExample, ts_to_date

# ---------------------------------------------------------------------------
# Basket, matched to the symbols present in Jesse's trades.csv
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
SESSIONS_PER_YEAR = 252

JESSE_TRADES_CSV = (
    Path(__file__).resolve().parents[3]
    / "jesse.git"
    / ".jesse-project"
    / "out"
    / "ewr-500000"
    / "trades.csv"
)


@dataclass
class SimResult:
    """Outcome of one equal-weight rebalance simulation."""

    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    rebalances: list[dict[str, Any]] = field(default_factory=list)
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


def rebalance_sessions(calendar: list[dt.date], start: dt.date, rebalance_days: int) -> set[dt.date]:
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


def curve_metrics(curve: list[dict[str, Any]], capital: float) -> dict[str, float]:
    """Return / CAGR / max drawdown / Sharpe for an equity curve.

    Mirrors ``portfolio_rebalance._curve_metrics``, including the 252-session
    annualisation and the >= 1 day floor that keeps CAGR finite on a 1-session run.
    """
    values = [p["value"] for p in curve]
    first = dt.date.fromisoformat(curve[0]["date"])
    last = dt.date.fromisoformat(curve[-1]["date"])
    years = max((last - first).days, 1) / 365.25

    rets = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))
            if values[i - 1] != 0]
    n = len(rets)
    if n > 1:
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        std = math.sqrt(var)
        sharpe = (mean / std) * math.sqrt(SESSIONS_PER_YEAR) if std > 0 else 0.0
    else:
        sharpe = 0.0

    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak:
            max_dd = min(max_dd, v / peak - 1.0)

    return {
        "final_value": values[-1],
        "total_return_pct": 100 * (values[-1] / capital - 1),
        "cagr_pct": 100 * ((values[-1] / capital) ** (1 / years) - 1),
        "max_drawdown_pct": 100 * max_dd,
        "sharpe": sharpe,
    }


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
                result.rebalances.append({
                    "date": session.isoformat(),
                    "value_before": None,
                    "trades": buys,
                    "cash_after": cash,
                    "leg": "buy",
                })

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
            result.rebalances.append({
                "date": session.isoformat(),
                "value_before": value_before,
                "trades": trades,
                "cash_after": cash,
                "leg": leg,
            })

        ever_held.update(s for s, q in qty.items() if q > 0)
        value = cash + sum(q * last_close[s] for s, q in qty.items() if q)
        result.equity_curve.append({"date": session.isoformat(), "value": value, "cash": cash})

    traded_notional = sum(t["notional"] for r in result.rebalances for t in r["trades"])
    total_fees = sum(t["fee"] for r in result.rebalances for t in r["trades"])
    mean_value = sum(p["value"] for p in result.equity_curve) / len(result.equity_curve)

    n_rebalances = len({r["date"] for r in result.rebalances if r.get("leg") != "buy"}) - 1
    result.metrics = {
        **curve_metrics(result.equity_curve, capital),
        "n_rebalances": n_rebalances,
        "total_fees": total_fees,
        "turnover": traded_notional / mean_value,
        "avg_cash_pct": 100 * sum(p["cash"] / p["value"] for p in result.equity_curve)
        / len(result.equity_curve),
        "n_fills": sum(len(r["trades"]) for r in result.rebalances),
    }
    result.final_holdings = {s: q for s, q in qty.items() if q}
    result.never_held = [s for s in sorted(symbols) if s not in ever_held and s in first_price]
    return result


# ---------------------------------------------------------------------------
# Comparison against the Jesse artefact
# ---------------------------------------------------------------------------
def load_jesse(path: Path) -> dict[str, Any]:
    """Summarise a Jesse ``trades.csv``; ``{}`` when the file is absent."""
    if not path.exists():
        return {}
    per_date: dict[str, int] = defaultdict(int)
    symbols: set[str] = set()
    fees = 0.0
    with path.open() as fh:
        for row in csv.DictReader(fh):
            per_date[row["date"]] += 1
            fees += float(row["fee"])
            symbols.add(row["symbol"].replace("-INR", ""))
    return {
        "n_fills": sum(per_date.values()),
        "n_sessions": len(per_date),
        "symbols": sorted(symbols),
        "fills_per_date": dict(sorted(per_date.items())),
        "fees": fees,
    }


# Reference figures from jesse's out/ewr-500000/equity.csv (capital 1,000,000).
JESSE_FINAL_VALUE = 1_250_202.66
JESSE_TOTAL_RETURN_PCT = 25.02
JESSE_CAGR_PCT = 36.04


def print_comparison(result: SimResult, jesse_path: Path) -> None:
    """Print Honba beside Jesse, session by session."""
    m = result.metrics
    jesse = load_jesse(jesse_path)

    print("=" * 74)
    print(f"{'metric':<28}{'HONBA':>15}{'JESSE':>15}{'delta':>14}")
    print("=" * 74)

    def row(label: str, mine: float, theirs: float | None) -> None:
        if theirs is None:
            print(f"{label:<28}{mine:>15,.2f}{'-':>15}{'-':>14}")
        else:
            print(f"{label:<28}{mine:>15,.2f}{theirs:>15,.2f}{mine - theirs:>14,.2f}")

    row("Final value", m["final_value"], JESSE_FINAL_VALUE)
    row("Total return %", m["total_return_pct"], JESSE_TOTAL_RETURN_PCT)
    row("CAGR %", m["cagr_pct"], JESSE_CAGR_PCT)
    row("Max drawdown %", m["max_drawdown_pct"], None)
    row("Sharpe", m["sharpe"], None)
    row("Total fees", m["total_fees"], jesse.get("fees"))
    row("Turnover", m["turnover"], None)
    row("Fills", float(m["n_fills"]), float(jesse["n_fills"]) if jesse else None)
    # jesse["n_sessions"] counts every trading date in trades.csv, which includes
    # the day-0 opening purchase; n_rebalances excludes it, so drop one to compare.
    row("Rebalances", float(m["n_rebalances"]),
        float(jesse["n_sessions"] - 1) if jesse else None)
    print("=" * 74)

    if result.missing_data:
        print(f"\nNo Parquet bars for {len(result.missing_data)} basket members. Jesse has")
        print("these, so they are missing here and every target is sized over a smaller")
        print("basket than Jesse's:")
        print("  " + ", ".join(result.missing_data))
    if result.never_held:
        print(f"\nNever held (1 share costs more than its target): {', '.join(result.never_held)}")
    if not jesse:
        print(f"\n(no Jesse trades.csv at {jesse_path})")
        return

    print("\nFills per session (honba | jesse):")
    per_date: dict[str, int] = defaultdict(int)
    for r in result.rebalances:
        per_date[r["date"]] = len(r["trades"])
    jd = jesse["fills_per_date"]
    for d in sorted(set(per_date) | set(jd)):
        h, j = per_date.get(d, 0), jd.get(d, 0)
        print(f"  {d}  {h:>4} | {j:>4}{'' if h == j else '   <-- differs'}")


# ---------------------------------------------------------------------------
# Example entry point
# ---------------------------------------------------------------------------
class JesseParityExample(HonbaExample):
    """Equal-weight rebalance over the Jesse Alpha-30 basket."""

    universe_name: str = "nifty50"  # unused; the basket is pinned to BASKET
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date = dt.date(2026, 1, 1)
    end_date: dt.date = dt.date(2026, 9, 23)  # Jesse's equity.csv stops here
    initial_capital: float = STARTING_CAPITAL
    warmup_days: int = 0  # the strategy has no indicators, so no warmup is needed
    fee: float = FEE_RATE
    rebalance_days: int = REBALANCE_DAYS
    # None = query the engine (India T+2 via honba-market; see below).
    # 0 = Jesse's model: sell and buy in the same session.
    settlement_days: int | None = None
    jesse_trades: Path = JESSE_TRADES_CSV

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--fee", type=float, default=self.fee,
                            help="Fraction of traded notional, per side")
        parser.add_argument("--rebalance-days", type=int, default=self.rebalance_days,
                            help="Calendar days between rebalances")
        parser.add_argument("--settlement-days", type=int, default=None,
                            help="Sessions between a sell and the buys it funds. Omit to use "
                                 "the engine default (India T+2 via honba-market); pass 0 for "
                                 "Jesse's same-session model")
        parser.add_argument("--jesse-trades", type=Path, default=self.jesse_trades,
                            help="Jesse trades.csv to diff against")

    @staticmethod
    def engine_settlement_days(exchange: str) -> int:
        """Settlement days for the exchange, sourced from the engine's market pack.

        Country+exchange is the driver: ``honba.markets.india.settlement`` resolves
        the cycle from ``honba-market`` (currently T+2 for NSE equities), whose value
        the Rust tests pin. ``--settlement-days`` overrides it for what-if runs.
        """
        from honba.markets.india.settlement import settlement_days_for
        return settlement_days_for(exchange)

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
        print(f"[Bars] {len(bars):,} bars across {len(available)}/{len(BASKET)} symbols, "
              f"{self.start_date} → {self.end_date}")
        if missing:
            print(f"[Data] missing bars for {len(missing)}: {', '.join(missing)}")

        settlement_days = (
            self.engine_settlement_days(self.exchange)
            if self.settlement_days is None
            else self.settlement_days
        )
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
        print(f"[Model] settlement T+{settlement_days} "
              + (f"(engine default for {self.exchange})"
                 if self.settlement_days is None
                 else "(sells fund buys two sessions later)"
                 if settlement_days else "(Jesse parity: sell and buy same session)"))
        print_comparison(result, self.jesse_trades)
        return result


def main() -> None:
    JesseParityExample().main()


if __name__ == "__main__":
    main()
