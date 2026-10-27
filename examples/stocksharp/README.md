# 09 StockSharp – ported reference strategies (Phase 4, M3)

Canonical Python implementations of classical `StockSharp.Algo.Strategies`
samples. Every script is offline and deterministic (seeded RNGs, synthetic or
hand-rolled data), teaches a single concept, and carries a `Pitfalls::`
section in its module docstring (enforced by
`tests/unit/test_pitfalls_sections.py`).

## At a glance

| #  | File                            | Strategy / focus                              | What you learn                                                           |
|----|---------------------------------|-----------------------------------------------|--------------------------------------------------------------------------|
| 01 | `01_pairs_trading.py`           | Statistical pairs trading                     | OLS hedge ratio, Dickey-Fuller cointegration gate, z-score ±2σ entry / 0 exit |
| 02 | `02_twap_vwap.py`               | Execution slicers (TWAP / VWAP)               | Intraday volume pacing, seeded microsecond jitter, freeze-limit verification |
| 03 | `03_turtle_breakout.py`         | Turtle breakout system                        | 20/55-day Donchian channel, 2N ATR trailing stop, 4-unit pyramiding      |
| 04 | `04_market_maker.py`            | Market making                                 | Avellaneda-Stoikov reservation price/spread, inventory skew, OBI filter  |
| 05 | `05_risk_parity.py`             | Risk-parity rebalancer                        | Inverse-60-day-vol weights via `PortfolioStrategy` (Universe → Weighting → Schedule) |

## Running them

```bash
python examples/stocksharp/01_pairs_trading.py --symbol-a ICICIBANK --symbol-b HDFCBANK --bars 252
python examples/stocksharp/01_pairs_trading.py --entry-z 2.0 --exit-z 0.0 --lookback 20 --seed 7 --out pairs.json
python examples/stocksharp/02_twap_vwap.py --scheme twap --parent-qty 5000 --slices 75
python examples/stocksharp/02_twap_vwap.py --scheme vwap --parent-qty 5000 --slices 75 --seed 7 --out slices.json
python examples/stocksharp/03_turtle_breakout.py --entry-window 20 --exit-window 10 --bars 200
python examples/stocksharp/03_turtle_breakout.py --entry-window 55 --exit-window 20 --out turtle.json
python examples/stocksharp/04_market_maker.py --steps 240 --seed 42 --gamma 0.1 --k 1.5 --out mm.json
python examples/stocksharp/05_risk_parity.py --symbols 10 --bars 260 --rebalance-days 20 --seed 42 --out risk_parity.json
pytest tests/unit -q -k "pairs_trading or twap_vwap or turtle_breakout or market_maker or risk_parity"
```

## Notes on the ports

* `02_twap_vwap.py` reuses the freeze-limit helpers in `honba_examples.options`
  (`freeze_limit_qty`, `slice_order`, `verify_slices`) — the same ones the
  Phase 3 options examples use — so a 5,000-contract parent is always sliced
  below the instrument's freeze limit before it is "submitted".
* `05_risk_parity.py` composes `honba.strategies.PortfolioStrategy` from
  `StaticUniverse` + `InverseVolatility` + `EveryNDays`, the same parts pattern
  as `strategies/06_equal_weight_rebalance.py`, and replays it on synthetic
  bars with a `LedgerContext`.
* No script places real orders: they are reference implementations and
  verification harnesses, not execution systems.

## Pitfalls

Each script documents the traps specific to what it does (synthetic data says
nothing about edge, static hedge ratios are look-ahead if used live, AS
parameters need per-asset calibration, fills are at last close in replay, ...).
Read a script's `Pitfalls::` section before trusting its numbers.