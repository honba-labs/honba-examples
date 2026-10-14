"""backtesting/08_sip_buy_and_hold: systematic investment plan (SIP) backtest on a single instrument.

Simulates recurring buy-and-hold investing (monthly or weekly SIP) on an equity
or ETF (default: ``ALPHAETF:NSE``), with optional initial lump-sum corpus and
transaction fee modeling. Supports automated data gap fetching via Honba's
``DataService``, time-weighted returns, max drawdown, and exact annualized XIRR.

Run::

    python backtesting/08_sip_buy_and_hold.py --symbol ALPHAETF --frequency monthly --sip-amount 5000
    python backtesting/08_sip_buy_and_hold.py --symbol ALPHAETF --frequency weekly --sip-amount 1000
    python backtesting/08_sip_buy_and_hold.py --symbol ALPHAETF --download
    python backtesting/08_sip_buy_and_hold.py --out sip_report.json

Pitfalls::

    - Mixed fill prices: the initial lump sum buys at the first session's open, each SIP instalment
      at that session's close. Dates are fixed in advance so there is no signal lookahead, but the
      two prices are inconsistent and the close fill assumes you can trade at the close.
    - Costs: fee_rate is a flat 0.05 percent of notional per buy, not the statutory legs shown in
      02_cost_modeling. Units are whole shares (int division), so small SIP amounts against a high
      price leave idle cash, visible as cash_remaining.
    - Start-date and end-point luck: the default window is 2025-01-01 to today, and XIRR over a
      short span is annualised from a few cash flows, so it swings with the first and last day and
      with --sip-day. Test several start months and report the range.
    - Selection and survivorship: one instrument (ALPHAETF by default) that exists today. Choose it
      by a rule fixed before looking, and remember funds and stocks that closed never appear in a
      hand-picked list.
    - Data quality: bars are read as stored; two bars on one date collapse to the last, and a
      missing session pushes that instalment to the next available day (td >= target). Check
      adjustments and gaps before trusting XIRR or drawdown.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import DateInterval
from honba.screener.store import ParquetBarStore, find_data_root

from honba_examples.base import REPO_ROOT
from honba_examples.cli import make_parser
from honba_examples.metrics import curve_metrics
from honba_examples.sip_plan import calculate_xirr, ensure_instrument_data, sip_target_dates
from honba_examples.sip_report import add_sip_args, emit_sip_report

__all__ = ["calculate_xirr", "ensure_instrument_data", "main", "run"]


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
        e_date = end or (dt.datetime.now(tz=dt.timezone.utc).date() + dt.timedelta(days=1))
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
    target_dates = sip_target_dates(
        frequency, first_session, last_session, sip_day=sip_day, sip_weekday=sip_weekday
    )

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
            installments.append(
                {
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
                }
            )

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
                installments.append(
                    {
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
                    }
                )

        portfolio_val = shares * bar.close + cash
        curve.append(
            {
                "date": d.isoformat(),
                "value": round(portfolio_val, 2),
                "cash": round(cash, 2),
                "shares": shares,
                "close": round(bar.close, 4),
            }
        )

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

    avg_price = (sum(i["units"] * i["price"] for i in installments) / shares) if shares > 0 else 0.0

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
    parser = make_parser(__doc__)
    add_sip_args(parser)
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
    emit_sip_report(result, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
