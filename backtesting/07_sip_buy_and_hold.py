"""backtesting/07_sip_buy_and_hold: systematic investment plan (SIP) backtest on a single instrument.

Simulates recurring buy-and-hold investing (monthly or weekly SIP) on an equity
or ETF (default: ``ALPHAETF:NSE``), with optional initial lump-sum corpus and
transaction fee modeling. Supports automated data gap fetching via Honba's
``DataService``, time-weighted returns, max drawdown, and exact annualized XIRR.

Run::

    python backtesting/07_sip_buy_and_hold.py --symbol ALPHAETF --frequency monthly --sip-amount 5000
    python backtesting/07_sip_buy_and_hold.py --symbol ALPHAETF --frequency weekly --sip-amount 1000
    python backtesting/07_sip_buy_and_hold.py --symbol ALPHAETF --download
    python backtesting/07_sip_buy_and_hold.py --out sip_report.json
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import DateInterval
from honba.screener.store import ParquetBarStore, find_data_root

from honba_examples.base import REPO_ROOT
from honba_examples.jsonable import jsonable
from honba_examples.metrics import curve_metrics

__all__ = ["calculate_xirr", "ensure_instrument_data", "main", "run"]


def _monthly_dates(start: dt.date, end: dt.date, sip_day: int = 1) -> list[dt.date]:
    """Generate target calendar dates for a monthly SIP."""
    dates: list[dt.date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last_d = calendar.monthrange(y, m)[1]
        target = dt.date(y, m, min(max(1, sip_day), last_d))
        if start <= target <= end:
            dates.append(target)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return dates


def _weekly_dates(start: dt.date, end: dt.date, sip_weekday: int = 0) -> list[dt.date]:
    """Generate target calendar dates for a weekly SIP."""
    dates: list[dt.date] = []
    curr = start
    while curr.weekday() != sip_weekday:
        curr += dt.timedelta(days=1)
    while curr <= end:
        dates.append(curr)
        curr += dt.timedelta(days=7)
    return dates


def calculate_xirr(
    cash_flows: Sequence[tuple[dt.date, float]],
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """Calculate the exact annualized Internal Rate of Return (XIRR).

    cash_flows: Sequence of (date, amount), where outflows (investments) are
    negative and the terminal portfolio value is positive.
    Returns the annualized rate as a percentage, or None if it cannot converge.
    """
    if len(cash_flows) < 2:
        return None
    amounts = [a for _, a in cash_flows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None

    d0 = cash_flows[0][0]
    fractions = [(d - d0).days / 365.25 for d, _ in cash_flows]

    def xnpv(r: float) -> float:
        return sum(a / ((1.0 + r) ** t) for t, a in zip(fractions, amounts))

    def dxnpv(r: float) -> float:
        return sum(-t * a / ((1.0 + r) ** (t + 1.0)) for t, a in zip(fractions, amounts))

    # Newton-Raphson method
    r = 0.1
    for _ in range(max_iter):
        f = xnpv(r)
        df = dxnpv(r)
        if abs(df) < 1e-12:
            break
        r_next = r - f / df
        if abs(r_next - r) < tol:
            return r_next * 100.0
        r = r_next
        if r <= -0.99:
            r = -0.9

    # Bisection fallback
    low, high = -0.99, 5.0
    f_low, f_high = xnpv(low), xnpv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(max_iter):
        mid = (low + high) / 2.0
        f_mid = xnpv(mid)
        if abs(f_mid) < tol or (high - low) / 2.0 < tol:
            return mid * 100.0
        if f_low * f_mid < 0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid

    return mid * 100.0


def ensure_instrument_data(
    symbol: str = "ALPHAETF",
    exchange: str = "NSE",
    start: dt.date | None = None,
    end: dt.date | None = None,
    timeframe: str = "1D",
    store: ParquetBarStore | None = None,
) -> bool:
    """Check for missing date gaps and download data into the catalog via DataService."""
    if store is None:
        store = ParquetBarStore(find_data_root(REPO_ROOT))

    start = start or dt.date(2022, 1, 1)
    end = end or (dt.date.today() + dt.timedelta(days=1))
    interval = DateInterval(start, end)
    inst = InstrumentId(symbol, exchange)

    try:
        from honba.data.loaders.yfinance import YFinanceProvider
        from honba.research.data_loader.nse import NseBhavcopyProvider
        from honba.screener.service import DataService

        bhav_cache = store.data_dir / "cache" / "bhavcopy"
        bhav_cache.mkdir(parents=True, exist_ok=True)
        nse_prov = NseBhavcopyProvider(cache_dir=bhav_cache)
        yf_prov = YFinanceProvider()
        svc = DataService(store=store, providers=[nse_prov, yf_prov])

        plan = svc.plan([inst], timeframe, interval)
        gaps = plan.gaps_by_instrument.get(inst, [])
        if not gaps:
            print(f"[Data] {symbol}.{exchange} is already complete in {store.data_dir}.")
            return True

        print(f"[Data] Fetching {len(gaps)} missing gap(s) for {symbol}.{exchange}...")
        res = svc.ensure(plan)
        if res.success:
            print(f"[Data] Successfully fetched and stored bars for {symbol}.{exchange}.")
            return True
        print(f"[Data] Warning: gap fetch had warnings: {res.warnings}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[Data] Unable to fetch data automatically: {e}", file=sys.stderr)
        return False


def run(
    symbol: str = "ALPHAETF",
    exchange: str = "NSE",
    frequency: str = "monthly",
    sip_amount: float = 5000.0,
    initial_corpus: float = 0.0,
    sip_day: int = 1,
    sip_weekday: int = 0,
    sip_count: int | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    fee_rate: float = 0.0005,
    bars: Sequence[Bar] | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute a buy-and-hold SIP backtest."""
    inst = InstrumentId(symbol, exchange)

    if bars is None:
        root = data_dir if data_dir is not None else find_data_root(REPO_ROOT)
        store = ParquetBarStore(root)
        s_date = start or dt.date(2025, 1, 1)
        e_date = end or (dt.date.today() + dt.timedelta(days=1))
        bars_loaded = store.read(inst, "1D", DateInterval(s_date, e_date))
    else:
        bars_loaded = list(bars)

    if not bars_loaded:
        raise ValueError(f"No bars found for {symbol}.{exchange}. Consider using --download.")

    # Sort bars ascending by timestamp
    bars_sorted = sorted(bars_loaded, key=lambda b: b.ts)
    bars_by_date: dict[dt.date, Bar] = {}
    for b in bars_sorted:
        d = dt.datetime.fromtimestamp(b.ts / 1e9, tz=dt.timezone.utc).date()
        bars_by_date[d] = b

    trading_days = sorted(bars_by_date.keys())
    first_session = trading_days[0]
    last_session = trading_days[-1]

    # Generate SIP target dates
    freq_clean = frequency.lower().strip()
    if freq_clean == "monthly":
        target_dates = _monthly_dates(first_session, last_session, sip_day=sip_day)
    elif freq_clean == "weekly":
        target_dates = _weekly_dates(first_session, last_session, sip_weekday=sip_weekday)
    else:
        raise ValueError(f"Unsupported frequency: {frequency!r} (must be 'monthly' or 'weekly')")

    if sip_count is not None and sip_count > 0:
        target_dates = target_dates[:sip_count]

    # Map each target date to the next available trading day
    sip_schedule: list[tuple[int, dt.date, dt.date]] = []
    used_sessions: set[dt.date] = set()

    for idx, target in enumerate(target_dates, start=1):
        for td in trading_days:
            if td >= target and td not in used_sessions:
                sip_schedule.append((idx, target, td))
                used_sessions.add(td)
                break

    sip_by_session = {td: (idx, target) for idx, target, td in sip_schedule}

    cash = 0.0
    shares = 0.0
    total_invested = 0.0
    total_fees = 0.0
    inflows: dict[dt.date, float] = {}
    installments: list[dict[str, Any]] = []

    # 1. Process initial lump sum if specified
    if initial_corpus > 0.0:
        d0 = first_session
        bar0 = bars_by_date[d0]
        cash += initial_corpus
        total_invested += initial_corpus
        inflows[d0] = initial_corpus

        units = int(cash / (bar0.open * (1.0 + fee_rate)))
        if units > 0:
            cost = units * bar0.open
            fee = cost * fee_rate
            cash -= cost + fee
            shares += units
            total_fees += fee
            installments.append({
                "installment": 0,
                "type": "lump_sum",
                "target_date": d0.isoformat(),
                "execution_date": d0.isoformat(),
                "price": round(bar0.open, 4),
                "units": units,
                "invested": initial_corpus,
                "fee": round(fee, 4),
                "shares_held": shares,
                "cash_remaining": round(cash, 2),
            })

    # 2. Iterate through all trading days
    curve: list[dict[str, Any]] = []
    for d in trading_days:
        bar = bars_by_date[d]

        # Trigger SIP on scheduled sessions
        if d in sip_by_session:
            idx, target = sip_by_session[d]
            cash += sip_amount
            total_invested += sip_amount
            inflows[d] = inflows.get(d, 0.0) + sip_amount

            units = int(cash / (bar.close * (1.0 + fee_rate)))
            if units > 0:
                cost = units * bar.close
                fee = cost * fee_rate
                cash -= cost + fee
                shares += units
                total_fees += fee
                installments.append({
                    "installment": idx,
                    "type": "sip",
                    "target_date": target.isoformat(),
                    "execution_date": d.isoformat(),
                    "price": round(bar.close, 4),
                    "units": units,
                    "invested": sip_amount,
                    "fee": round(fee, 4),
                    "shares_held": shares,
                    "cash_remaining": round(cash, 2),
                })

        portfolio_val = shares * bar.close + cash
        curve.append({
            "date": d.isoformat(),
            "value": round(portfolio_val, 2),
            "cash": round(cash, 2),
            "shares": shares,
            "close": round(bar.close, 4),
        })

    # 3. Compute summary and return metrics
    final_bar = bars_by_date[last_session]
    final_price = final_bar.close
    final_value = curve[-1]["value"]
    net_pnl = final_value - total_invested
    total_return_pct = (net_pnl / total_invested * 100.0) if total_invested > 0 else 0.0

    # Calculate XIRR cash flows
    cash_flows: list[tuple[dt.date, float]] = []
    for d_flow, amt in inflows.items():
        cash_flows.append((d_flow, -amt))
    if final_value > 0:
        cash_flows.append((last_session, final_value))

    xirr_pct = calculate_xirr(cash_flows)

    # Calculate curve drawdown, TWR CAGR, and Sharpe
    c_metrics = curve_metrics(
        curve,
        capital=total_invested,
        cash_inflows=inflows,
        total_invested=total_invested,
    )

    avg_price = (
        (sum(i["units"] * i["price"] for i in installments) / shares)
        if shares > 0
        else 0.0
    )

    summary = {
        "symbol": symbol,
        "exchange": exchange,
        "first_session": first_session.isoformat(),
        "last_session": last_session.isoformat(),
        "sessions_count": len(trading_days),
        "initial_corpus": initial_corpus,
        "sip_amount": sip_amount,
        "frequency": freq_clean,
        "installments_count": len([i for i in installments if i["type"] == "sip"]),
        "total_invested": round(total_invested, 2),
        "total_units": shares,
        "avg_purchase_price": round(avg_price, 4),
        "final_market_price": round(final_price, 4),
        "final_portfolio_value": round(final_value, 2),
        "cash_balance": round(cash, 2),
        "total_fees_paid": round(total_fees, 2),
        "net_pnl": round(net_pnl, 2),
        "total_return_pct": round(total_return_pct, 2),
        "cagr_pct": round(c_metrics.get("cagr_pct", 0.0), 2),
        "xirr_pct": round(xirr_pct, 2) if xirr_pct is not None else None,
        "max_drawdown_pct": round(c_metrics.get("max_drawdown_pct", 0.0), 2),
        "sharpe": round(c_metrics.get("sharpe", 0.0), 2),
    }

    return {
        "config": {
            "symbol": symbol,
            "exchange": exchange,
            "frequency": freq_clean,
            "sip_amount": sip_amount,
            "initial_corpus": initial_corpus,
            "sip_day": sip_day,
            "sip_weekday": sip_weekday,
            "sip_count": sip_count,
            "fee_rate": fee_rate,
        },
        "summary": summary,
        "installments": installments,
        "curve": curve,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--symbol", "--instrument", dest="symbol", default="ALPHAETF", help="Instrument symbol (default: ALPHAETF)"
    )
    parser.add_argument("--exchange", default="NSE", help="Exchange code (default: NSE)")
    parser.add_argument(
        "--frequency", choices=["monthly", "weekly"], default="monthly", help="SIP frequency: monthly or weekly"
    )
    parser.add_argument(
        "--sip-amount", type=float, default=5000.0, help="SIP installment amount in INR (default: 5000.0)"
    )
    parser.add_argument(
        "--initial-corpus", type=float, default=0.0, help="Initial lump sum corpus invested on day 1 (default: 0.0)"
    )
    parser.add_argument(
        "--sip-day", type=int, default=1, help="Day of month for monthly SIP (1-28, default: 1)"
    )
    parser.add_argument(
        "--sip-weekday", type=int, default=0, help="Weekday for weekly SIP (0=Mon..4=Fri, default: 0)"
    )
    parser.add_argument(
        "--sip-count", "--no-of-sip", dest="sip_count", type=int, default=None, help="Maximum number of installments"
    )
    parser.add_argument(
        "--start", type=lambda s: dt.date.fromisoformat(s), default=None, help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end", type=lambda s: dt.date.fromisoformat(s), default=None, help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--fee-rate", type=float, default=0.0005, help="Transaction fee rate (default: 0.0005)"
    )
    parser.add_argument(
        "--download", action="store_true", help="Download/fetch missing gaps before running"
    )
    parser.add_argument("--out", type=Path, default=None, help="Output path for JSON report")
    parser.add_argument(
        "--format", choices=["table", "json"], default="table", help="Output format (default: table)"
    )

    args = parser.parse_args(argv)

    if args.download:
        ensure_instrument_data(
            symbol=args.symbol,
            exchange=args.exchange,
            start=args.start,
            end=args.end,
        )

    result = run(
        symbol=args.symbol,
        exchange=args.exchange,
        frequency=args.frequency,
        sip_amount=args.sip_amount,
        initial_corpus=args.initial_corpus,
        sip_day=args.sip_day,
        sip_weekday=args.sip_weekday,
        sip_count=args.sip_count,
        start=args.start,
        end=args.end,
        fee_rate=args.fee_rate,
    )

    s = result["summary"]
    json_text = json.dumps(jsonable(result), indent=2)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json_text + "\n", encoding="utf-8")
        print(f"[Output] Wrote full report to {args.out}")

    if args.format == "json":
        print(json_text)
    else:
        # Formatted human table summary
        xirr_str = f"{s['xirr_pct']:+.2f}%" if s["xirr_pct"] is not None else "N/A"
        print("=" * 64)
        print(f"       SIP Backtest: {s['symbol']}.{s['exchange']} ({s['frequency'].capitalize()})")
        print("=" * 64)
        print(f"  Date Range          : {s['first_session']} to {s['last_session']} ({s['sessions_count']} sessions)")
        print(f"  SIP Frequency       : {s['frequency'].capitalize()}")
        print(f"  SIP Installment     : ₹{s['sip_amount']:,.2f}")
        print(f"  Initial Corpus      : ₹{s['initial_corpus']:,.2f}")
        print(f"  Installments Paid   : {s['installments_count']}")
        print("-" * 64)
        print(f"  Total Invested      : ₹{s['total_invested']:,.2f}")
        print(f"  Total Units Held    : {s['total_units']:,.0f}")
        print(f"  Avg Purchase Price  : ₹{s['avg_purchase_price']:,.2f}")
        print(f"  Final Market Price  : ₹{s['final_market_price']:,.2f}")
        print(f"  Final Portfolio Val : ₹{s['final_portfolio_value']:,.2f}")
        print(f"  Net Profit / Loss   : ₹{s['net_pnl']:+,.2f} ({s['total_return_pct']:+.2f}%)")
        print("-" * 64)
        print(f"  CAGR (Time-Weighted): {s['cagr_pct']:+.2f}%")
        print(f"  XIRR (Annualized)   : {xirr_str}")
        print(f"  Max Drawdown        : {s['max_drawdown_pct']:.2f}%")
        print(f"  Transaction Fees    : ₹{s['total_fees_paid']:,.2f}")
        print("=" * 64)

    return 0


if __name__ == "__main__":
    sys.exit(main())
