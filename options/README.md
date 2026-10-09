# 08 Options – chain, Greeks, and strategy backtests

Options examples use Honba's option chain (`OptionChain`, `OptionContract`) and
Greeks engine (`honba.analytics.greeks`). They hand-roll their legs directly —
there is no `Strategy` subclass. All runs are offline and deterministic, on
synthetic or recorded chain data by default; no network or wall clock is involved.
Catalog equivalents live in `honba-strategies/options/*`.

## At a glance

| #  | File                            | Strategy / focus      | What you learn                                                      |
|----|---------------------------------|-----------------------|---------------------------------------------------------------------|
| 01 | `01_option_chain.py`            | —                     | Load/construct option chain; filter by expiry, strike, moneyness    |
| 02 | `02_greeks_calculation.py`      | —                     | Black-Scholes Greeks (delta, gamma, theta, vega, rho) per contract  |
| 03 | `03_backtest_straddle.py`       | Long ATM straddle     | Backtest long straddle: buy ATM call+put, hold to expiry            |
| 04 | `04_backtest_iron_condor.py`    | Iron condor           | Backtest iron condor: 4-leg credit spread, P&L zones                |
| 05 | `05_expiry_day_strategy.py`     | Expiry-day scalp      | Intraday expiry-day strategy: gamma scalping / theta decay capture  |

Catalog equivalents: `options/nifty_short_straddle`, `options/banknifty_iron_condor`,
`options/expiry_day_scalp`.

## Option chain structure

```python
from honba.domain.option import OptionChain, OptionContract, OptionKind, OptionStyle

chain: OptionChain  # underlying: InstrumentId, expiry: dt.date, contracts: list[OptionContract]
contract: OptionContract  # strike, kind (CALL/PUT), style (EUROPEAN/AMERICAN), bid, ask, mid, iv, greeks
```

## Running them

```bash
python options/01_option_chain.py --underlying NIFTY --expiry 2024-12-26
python options/02_greeks_calculation.py --spot 24000 --strike 24000 --days-to-expiry 7 --iv 0.15
python options/03_backtest_straddle.py --underlying NIFTY --expiry 2024-12-26
python options/04_backtest_iron_condor.py --underlying NIFTY --expiry 2024-12-26
python options/05_expiry_day_strategy.py --underlying BANKNIFTY --expiry 2024-12-25
pytest tests/unit -q -k "option or greeks or straddle or iron_condor"
```

> There are no dedicated option unit tests in `tests/unit` yet; the `-k` filter
> above is a placeholder that also picks up these examples' names once tests land.

## Key concepts

1. **Chain as snapshot** — `OptionChain` is a point-in-time view; for backtests, load a series of chains
2. **Moneyness** — ATM = strike ≈ spot; OTM/ITM defined by spot vs strike for each kind
3. **Greeks** — `honba.analytics.greeks.bs_greeks(kind, spot, strike, tau, iv, r=0.07)` returns all 5
4. **P&L at expiry** — Max loss for long straddle = premium paid; iron condor = width - credit
5. **Expiry dynamics** — Gamma spikes near ATM; theta accelerates in final week

## From here to production

- Use `honba.adapters.base.OptionChainProvider` for live chain streaming
- Add IV surface modeling (`honba.analytics.iv_surface`) for smile/skew
- Schedule strategies with `honba.strategies.scheduling.ExpiryCalendar`
- Risk: assignment risk on ITM short legs, pin risk at expiry