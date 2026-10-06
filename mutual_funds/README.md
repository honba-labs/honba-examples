# 07 Mutual Funds – NAV handling and SIP backtests

Mutual fund examples use the AMFI NAV feed (`honba_examples.amfi`) and Honba's
mutual-fund instrument type (`InstrumentKind.MUTUAL_FUND`). All runs are offline
with recorded NAV fixtures.

| #  | File                              | What you learn                                                          |
|----|-----------------------------------|-------------------------------------------------------------------------|
| 01 | `01_fetch_nav.py`                 | Download/load AMFI NAV history; resample to daily; handle missing days  |
| 02 | `02_sip_backtest.py`              | SIP simulation: fixed-date monthly investment, units at NAV, XIRR       |
| 03 | `03_category_analysis.py`         | Category-level stats: rolling returns, risk metrics, quartile ranking   |
| 04 | `04_portfolio_optimization.py`    | Mean-variance optimization on fund NAVs; constraint-aware allocation    |

## The AMFI NAV feed

`honba_examples.amfi.AmfiNavLoader` provides:
- `load(scheme_code: str) -> list[NavPoint]` — NAV series from local Parquet or AMFI CSV
- `load_category(category: str) -> dict[str, list[NavPoint]]` — all schemes in a category
- `latest_nav(scheme_code: str) -> NavPoint` — most recent NAV (for live SIP)

Each `NavPoint` has `date: dt.date`, `nav: float`, `scheme_code: str`, `scheme_name: str`.

## Running them

```bash
python mutual_funds/01_fetch_nav.py --scheme 120503 --start 2023-01-01 --end 2024-12-31
python mutual_funds/02_sip_backtest.py --scheme 120503 --monthly 10000 --start 2023-01-01
python mutual_funds/03_category_analysis.py --category "Equity:Large Cap" --start 2020-01-01
python mutual_funds/04_portfolio_optimization.py --schemes 120503,120504,120505 --start 2022-01-01
pytest tests/unit -q -k "mutual_fund or nav or sip"
```

## Key concepts

1. **NAV as bar-equivalent** — `NavPoint` maps to `Bar` with `open=high=low=close=nav`, `volume=0`
2. **SIP simulation** — Fixed calendar date (e.g., 7th of each month), buy units = amount/NAV
3. **XIRR** — Internal rate of return for irregular cash flows (Honba's `honba_examples.metrics.xirr`)
4. **Category universes** — AMFI categories map to Honba universe names (`equity_large_cap`, etc.)
5. **Optimization constraints** — Max weight per fund, min allocation, category caps

## From here to production

- Add transaction costs: exit load, stamp duty (0.005% on redemption), STT (equity-oriented)
- Use `honba.session.BacktestSession` with `InstrumentKind.MUTUAL_FUND` for event-driven runs
- Schedule rebalancing with `honba.strategies.scheduling.RebalanceCalendar`