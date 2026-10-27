"""Single-instrument SIP helpers: calendar schedule, session mapping, XIRR, gap download.

Shared by ``backtesting/08_sip_buy_and_hold.py``. The XIRR here is a Newton-Raphson solver
with a bisection fallback; it differs from :func:`honba_examples.sip.calculate_xirr` (pure
bisection), so the two are deliberately kept separate to preserve each script's numbers.
"""

from __future__ import annotations

import calendar
import datetime as dt
import sys
from collections.abc import Sequence

from honba.domain.instrument import InstrumentId
from honba.screener.coverage import DateInterval
from honba.screener.store import ParquetBarStore, find_data_root

from honba_examples.base import REPO_ROOT

__all__ = [
    "calculate_xirr",
    "ensure_instrument_data",
    "monthly_dates",
    "sip_target_dates",
    "weekly_dates",
]


def monthly_dates(start: dt.date, end: dt.date, sip_day: int = 1) -> list[dt.date]:
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


def weekly_dates(start: dt.date, end: dt.date, sip_weekday: int = 0) -> list[dt.date]:
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
    mid = (low + high) / 2.0
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
    end = end or (dt.datetime.now(tz=dt.timezone.utc).date() + dt.timedelta(days=1))
    interval = DateInterval(start, end)
    inst = InstrumentId(symbol, exchange)

    try:
        from honba.data.loaders.nse import NseBhavcopyProvider
        from honba.data.loaders.yfinance import YFinanceProvider
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


def sip_target_dates(
    frequency: str,
    first: dt.date,
    last: dt.date,
    sip_day: int = 1,
    sip_weekday: int = 0,
) -> list[dt.date]:
    """Target calendar dates for ``frequency`` ('monthly' or 'weekly') within [first, last]."""
    freq = frequency.lower().strip()
    if freq == "monthly":
        return monthly_dates(first, last, sip_day=sip_day)
    if freq == "weekly":
        return weekly_dates(first, last, sip_weekday=sip_weekday)
    raise ValueError(f"Unsupported frequency: {frequency!r} (must be 'monthly' or 'weekly')")
