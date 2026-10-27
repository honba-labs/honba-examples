# Options microstructure (Phase 3, M2)

Indian equity index options on NSE — NIFTY/BANKNIFTY — are European, cash-settled
and cleared through ICCL on a T+1 cycle. This phase documents the three new
examples under `examples/options/` (06–08) and the cost-model helpers they share
in `src/honba_examples/options/`.

## Scripts

| #  | File                                | What you learn                                                                |
|----|-------------------------------------|-------------------------------------------------------------------------------|
| 06 | `06_nifty_920_straddle.py`          | Short ATM straddle entered at 09:20 IST, 25 % per-leg stop-loss, 15:20 IST square-off; premium-side STT on the sell side |
| 07 | `07_large_qty_options_slicer.py`    | A 5,000-contract NIFTY order sliced below the freeze limit; every slice verified against the instrument master |
| 08 | `08_iron_condor_hedging.py`         | Short iron condor with dynamic delta hedging and Greek risk bounds (delta/gamma/vega) |

## Cost model: option-premium STT and ICCL settlement

`honba_examples.options` collects the option-specific pieces the core
`honba.markets.india.costs` schedules do not yet cover (its F&O branch is a
stub pending the NSE F&O circular):

* **STT on the sell side** — 0.0625 % of the option premium (`OPTION_SELL_STT_RATE`),
  payable by the option seller per the SEBI / NSE circular in force for FY 2025-26.
  Buyers pay no premium-side STT. `option_sell_stt(premium, lots)` returns the
  contract-note line, rounded to the paise once.
  > Rates change by circular: re-verify `OPTION_SELL_STT_RATE` against the latest
  > SEBI/NSE circular before any paper or live run.
* **ICCL settlement** — index options are European and cash-settled; premium and
  mark-to-market amounts settle T+1 through ICCL. There is no physical delivery
  and no exercise STT on index options, so the 06/08 models have no exercise or
  delivery leg.
* **Freeze limit** — SEBI single-order freeze quantity (planned 1,800 NIFTY
  contracts) read via `freeze_limit_qty(symbol, exchange)`, which consults the
  instrument master and falls back to the planned default offline. Never hard-code
  it.

## What each script demonstrates

**06 — intraday short straddle.** Sell the ATM call and put at 09:20 on a
seeded GBM intraday path; each leg is bought back when its premium appreciates
25 % vs entry (stop-loss per leg), and whatever survives is squared off at
15:20 IST. STT is debited on both opening sells and sunk regardless of outcome.
Reported: entry credit, breakevens (strike ± credit), per-leg P&L, STT total,
net P&L and the max adverse spot move of the path.

**07 — freeze-limit slicer.** A 5,000-contract parent exceeds the planned
1,800-contract NIFTY freeze limit, so it must be sliced. The script prefers
`honba_te_slicer` (sibling `honba-te` repo) and falls back to the deterministic
reference slicer in `honba_examples.options` (`slice_order`); `verify_slices`
raises when any child exceeds the limit. 5,000 → [1,800, 1,800, 1,400].

**08 — delta-hedged iron condor.** Sell the 4-leg condor for a net credit, then
re-price the legs each bar with `honba.analytics.greeks.bs_greeks` and flatten
net delta with the underlying futures whenever `|net delta|` breaches the bound
in `GreekRiskBounds`; gamma/vega breaches are logged. STT on the four opening
sells is modelled; closing buys attract none. Reported: Greeks time series,
breach count, hedge trades, close premiums and net P&L.

## Pitfalls shared across the phase

* Short gamma is uncapped: a directional move past a breakeven loses point for
  point with no hedge modelled (06); delta hedging removes delta but not gamma,
  vega or pin risk (08).
* Fills are at Black-Scholes mid — no bid/ask cross, slippage, partial fills or
  order sequencing; real execution needs the slicer (07) and a fill model.
* The intraday path is seeded GBM whose realised vol equals the flat iv: it
  shows mechanics, not edge.
* Square-off at 15:20 IST is a hard model exit; broker MIS auto square-off is
  policy-dependent and may start earlier.
* ATM strikes round to the contract strike step and lot sizes change by circular
  — read both from the instrument master.

## Running

```bash
python examples/options/06_nifty_920_straddle.py --spot 22000 --iv 0.15 --out straddle.json
python examples/options/07_large_qty_options_slicer.py --total-qty 5000 --out slices.json
python examples/options/08_iron_condor_hedging.py --spot 24000 --wings 200 --dte 7 --out hedge.json
pytest tests/unit -q -k "06_nifty_920_straddle or 07_large_qty_options_slicer or 08_iron_condor_hedging"
```
