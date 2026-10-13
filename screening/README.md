# Screening Tools

Point-in-time screener CLI tools for composable Unix pipeline chaining.

## Directory Structure

```
screening/
├── 01_multibagger_screener.py          # Stage 2 momentum breakout candidates
├── 02_value_52w_low_screener.py        # Deep value & 52-week low contrarian
├── 03_dividend_yield_screener.py       # High dividend yielders at 52-week low
├── 04_intelligent_investor_screener.py # Benjamin Graham value & Margin of Safety
├── 05_net_net_screener.py              # Benjamin Graham Net-Net (NCAV) deep asset value
└── README.md                           # This file
```

## Common Options

All screeners share these options:

| Option | Description |
|--------|-------------|
| `--asof YYYY-MM-DD` | Evaluation date (defaults to latest available market date) |
| `--format FORMAT` | Output: `table` (rich), `json` (machine), `symbols` (space-separated), `plain` (newline-separated), `csv` |
| `--data-dir PATH` | Override catalog directory |

Value/dividend/Graham screeners share the fundamental options:

| Option | Description |
|--------|-------------|
| `--train-years N` | In-sample training window years (default: 3) |
| `--top N` | Limit to top N candidates (default: 10) |
| `--min-roce F` | Minimum ROCE % |
| `--max-pe F` | Maximum derived P/E (0 = disabled) |
| `--max-pb F` | Maximum derived P/B (0 = disabled) |
| `--max-debt-equity F` | Maximum debt-to-equity |
| `--min-div-yield F` | Minimum trailing dividend yield % (0 = disabled) |
| `--require-positive-fcf` / `--no-require-positive-fcf` | Require positive free cash flow |
| `--fiscal-lag-days N` | Reporting-lag buffer (days) so only fundamentals published by `--asof` are used |

## Point-in-Time Valuation (Zero Lookahead)

Prices come from the 1D Parquet catalog up to `--asof`. Fundamentals come from
`fundamentals.duckdb`, restricted to the latest fiscal period ending on or before
`--asof` (optionally shifted back by `--fiscal-lag-days`). Valuation is **derived**, never
read from a live snapshot:

| Metric | Derivation |
|--------|------------|
| P/E | as-of close ÷ latest reported EPS (only when EPS > 0) |
| P/B | as-of close ÷ book value per share (equity ÷ diluted average shares) |
| Dividend yield | trailing annual cash dividend per share ÷ as-of close |
| Graham Value | $\sqrt{22.5 \times \text{EPS} \times \text{BVPS}}$ |
| Margin of Safety | $(\text{Graham Value} - \text{Close}) / \text{Graham Value} \times 100$ |
| Graham Product | $\text{P/E} \times \text{P/B}$ |
| Current Ratio | $\text{Current Assets} / \text{Current Liabilities}$ |

> **Data availability:** point-in-time fundamentals in the bundled lakehouse are annual (FY)
> vintages reporting from `2023-03-31` onwards. Screens evaluated before mid-2023 will return
> few or no fundamental candidates; this is a data limitation, not a screener bug.

---

## Screener 1: Prospective Multibagger (`01_multibagger_screener.py`)

Identifies Stage 2 momentum breakout leaders before late-stage exhaustion.

### Filters Applied:
- Minervini Stage 2 trend template (Price >= SMA50 >= SMA200, rising 200 SMA)
- Tight base consolidation near 52-week high
- Momentum acceleration (3-month velocity > 0%)
- Volume accumulation surge (20-day volume > 200-day baseline)
- ROCE capital efficiency and low Debt/Equity

---

## Screener 2: Deep Value Near 52-Week Low (`02_value_52w_low_screener.py`)

Identifies contrarian value opportunities trading near their 52-week lows with healthy solvency.

### Options:
| Option | Default | Description |
|--------|---------|-------------|
| `--max-pct-from-52w-low` | 15.0 | Max % above 52-week low |
| `--min-roce` | 10.0 | Min ROCE % |
| `--max-pe` | 20.0 | Max derived P/E (0 = disabled) |
| `--max-pb` | 2.0 | Max derived P/B (0 = disabled) |
| `--max-debt-equity` | 1.5 | Max debt-to-equity |
| `--min-div-yield` | 0.0 | Min trailing dividend yield % (0 = disabled) |
| `--require-positive-fcf` | off | Require FCF > 0 |

---

## Screener 3: High Dividend Yield at 52-Week Low (`03_dividend_yield_screener.py`)

Identifies high dividend yield candidates near 52-week low with strong fundamentals (Dogs of NSE approach).

### Options:
| Option | Default | Description |
|--------|---------|-------------|
| `--max-pct-from-52w-low` | 15.0 | Max % above 52-week low |
| `--min-div-yield` | 3.0 | Min trailing dividend yield % |
| `--min-roce` | 12.0 | Min ROCE % |
| `--max-pe` | disabled (0) | Max derived P/E |
| `--max-pb` | disabled (0) | Max derived P/B |
| `--max-debt-equity` | 1.0 | Max debt-to-equity |
| `--require-positive-fcf` | on | Require FCF > 0 |

---

## Screener 4: Benjamin Graham Value & Margin of Safety (`04_intelligent_investor_screener.py`)

Implements the timeless value investing criteria of Benjamin Graham (*The Intelligent Investor*, Chapters 14 & 15).

### Core Principles:
- **Avoid Overpaying**: Enforces $\text{P/E} \times \text{P/B} \le 22.5$, $\text{P/E} \le 15-20$, $\text{P/B} \le 1.5-2.0$.
- **Margin of Safety**: Intrinsic Graham Number $\sqrt{22.5 \times \text{EPS} \times \text{BVPS}}$, ranked by discount to Graham Value.
- **Financial Condition**: Current Assets $\ge 1.2-2.0 \times$ Current Liabilities; conservative Debt-to-Equity.
- **No Speculation**: Positive audited earnings and stockholders' equity.

### Profiles:
- `--mode defensive`: Strict Graham Defensive Investor screen ($\text{P/E} \times \text{P/B} \le 22.5$, Debt/Equity $\le 0.6$, positive dividend yield).
- `--mode enterprising`: Active value investor screen allowing turnaround opportunities with large margin of safety.

### Options:
| Option | Default | Description |
|--------|---------|-------------|
| `--mode` | defensive | Graham profile: `defensive` or `enterprising` |
| `--min-margin-of-safety` | 0.0 | Minimum Margin of Safety % relative to Graham Value |
| `--max-graham-product` | 22.5 | Maximum product of P/E × P/B |
| `--max-pe` | 20.0 | Maximum derived P/E ratio |
| `--max-pb` | 2.0 | Maximum derived P/B ratio |
| `--max-debt-equity` | 1.0 | Maximum debt-to-equity ratio |
| `--min-current-ratio` | 1.2 | Minimum Current Assets ÷ Current Liabilities |
| `--min-div-yield` | 0.0 | Minimum trailing dividend yield % |

### Example:
```bash
# Screen Graham defensive value candidates as of 2024-01-01
python screening/04_intelligent_investor_screener.py --asof 2024-01-01 --mode defensive --top 5
```

---

---

## Screener 5: Benjamin Graham Net-Net / Deep Asset Value (`05_net_net_screener.py`)

Implements Benjamin Graham's most profitable historical strategy: **Net Current Asset Value (NCAV)**.

### Core Principles:
- **NCAV Formula**: $\text{NCAV} = \text{Current Assets} - \text{Total Liabilities}$.
- **Liquidation Floor**: Current Assets (cash, receivables, inventory) exceed ALL short-term and long-term liabilities combined.
- **Acquiring Fixed Assets for Free**: Plant, machinery, property, equipment, brand, and goodwill are acquired at zero cost.
- **Classic 2/3 Net-Net**: $\text{Price} \le 0.67 \times \text{NCAV per share}$ (Margin of Safety $\ge 33.3\%$).

### Options:
| Option | Default | Description |
|--------|---------|-------------|
| `--max-p-ncav` | disabled (0) | Maximum Price-to-NCAV ratio (e.g. `0.67` for classic 2/3 Net-Net, `1.0` for NCAV) |
| `--min-ncav-margin-of-safety` | disabled (0) | Minimum Margin of Safety % to NCAV |
| `--require-positive-cfo` | off | Require positive operating cash flow (avoids cash burn) |
| `--require-positive-eps` | off | Require positive EPS |
| `--max-debt-equity` | 1.0 | Maximum debt-to-equity ratio |
| `--min-current-ratio` | 1.0 | Minimum Current Assets ÷ Current Liabilities |

### Example:
```bash
# Screen deepest asset value / Net-Net candidates as of 2024-01-01
python screening/05_net_net_screener.py --asof 2024-01-01 --top 5

# Pipe Net-Net candidates into forward SIP backtest
python screening/05_net_net_screener.py --asof 2024-01-01 --format symbols \
  | python backtesting/08_chained_screener_and_sip.py --asof 2024-01-01 --sip-amount 10000
```

---

## Pipeline Chaining (Unix Philosophy)

Screeners output pure symbol streams (`--format symbols` or `--format plain`) designed for piping into forward backtests:

### Benjamin Graham Value → Forward SIP Backtest
```bash
python screening/04_intelligent_investor_screener.py --asof 2024-01-01 --mode defensive --format symbols \
  | python backtesting/08_chained_screener_and_sip.py --asof 2024-01-01 --capital 100000 --sip 10000
```

### Multibagger Momentum → Forward SIP Backtest
```bash
python screening/01_multibagger_screener.py --asof 2023-01-01 --top 5 --format symbols \
  | python backtesting/08_chained_screener_and_sip.py --asof 2023-01-01 --sip-amount 10000
```

### Dogs of NSE → Forward SIP Backtest
```bash
python screening/03_dividend_yield_screener.py --asof 2025-03-01 --min-div-yield 3.0 --max-pct-from-52w-low 15.0 --format symbols \
  | python backtesting/08_chained_screener_and_sip.py --asof 2025-03-01 --sip-amount 10000
```

### Deep Value at 52w Low → Forward SIP Backtest
```bash
python screening/02_value_52w_low_screener.py --asof 2025-03-01 --max-pe 20 --max-pb 2.0 --max-debt-equity 1.5 --format symbols \
  | python backtesting/08_chained_screener_and_sip.py --asof 2025-03-01 --sip-amount 10000
```

---

## Data Requirements

Point-in-time valuation comes from the 1D Parquet catalog (`honba-data/data/catalog/1D/NSE/`)
plus the fundamentals lakehouse:

- `financial_ratios` — EPS, ROCE, debt/equity, operating margin, free cash flow, book equity.
- `financial_statements` — cash dividends, common dividends, diluted average shares, current assets, current liabilities.
- Derived fields: `P/E = close / EPS`, `P/B = close / (equity / shares)`,
  `yield = abs(dividends) / shares / close`, `Graham Value = sqrt(22.5 * EPS * BVPS)`.

Requires `fundamentals.duckdb` at `honba-data/data/fundamentals.duckdb`.
