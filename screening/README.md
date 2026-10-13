# Screening Tools

Point-in-time screener CLI tools for composable Unix pipeline chaining.

## Directory Structure

```
screening/
├── 01_multibagger_screener.py      # Stage 2 momentum breakout candidates
├── 02_value_52w_low_screener.py    # Deep value & 52-week low contrarian
├── 03_dividend_yield_screener.py   # High dividend yielders at 52-week low
└── README.md                       # This file
```

## Common Options

All screeners share these options:

| Option | Description |
|--------|-------------|
| `--asof YYYY-MM-DD` | Evaluation date (defaults to latest available market date) |
| `--format FORMAT` | Output: `table` (rich), `json` (machine), `symbols` (space-separated), `plain` (newline-separated) |
| `--data-dir PATH` | Override catalog directory |

Value/dividend screeners share the fundamental options:

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

> **Data availability:** point-in-time fundamentals in the bundled lakehouse are annual (FY)
> vintages reporting from `2023-03-31` onwards. Screens evaluated before mid-2023 will return
> few or no fundamental candidates; this is a data limitation, not a screener bug.

## Screener 1: Prospective Multibagger (`01_multibagger_screener.py`)

Identifies Stage 2 momentum breakout leaders before late-stage exhaustion.

### Filters Applied:
- Trend template: Price ≥ SMA50 ≥ SMA200, rising 200 SMA
- Consolidation base: Price within 15% of 52-week high
- Momentum acceleration: Positive 3-month momentum
- Volume accumulation: 20-day volume > 200-day baseline
- Fundamentals: ROCE capital efficiency, Debt/Equity ≤ 1.2

### Options:
| Option | Default | Description |
|--------|---------|-------------|
| `--min-return-1y` | 15.0 | Minimum 1-year return % |
| `--max-return-1y` | 175.0 | Maximum 1-year return % (avoid parabolic) |
| `--max-pct-from-high` | 15.0 | Max % below 52-week high |
| `--max-debt-equity` | 1.2 | Max debt-to-equity ratio |

### Example:
```bash
# Screen top 5 multibaggers as of 2023-01-01, output symbols for piping
python screening/01_multibagger_screener.py --asof 2023-01-01 --top 5 --format symbols

# Full table output
python screening/01_multibagger_screener.py --asof 2023-01-01 --top 10
```

---

## Screener 2: Deep Value at 52-Week Low (`02_value_52w_low_screener.py`)

Identifies contrarian value candidates trading near 52-week low with strong balance sheets.

### Filters Applied:
- Price within N% of 52-week low
- Derived point-in-time valuation: P/E (price ÷ EPS), P/B (price ÷ BVS)
- ROCE quality floor, Debt/Equity ceiling, optional FCF > 0
- Recent 52-week low touch detection

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

### Example:
```bash
# Deep value at 52w low: derived P/E < 20, P/B < 2.0, Debt/Eq < 1.5 on a date with fundamentals
python screening/02_value_52w_low_screener.py --asof 2025-03-01 --max-pe 20 --max-pb 2.0 --max-debt-equity 1.5 --format symbols
```

---

## Screener 3: High Dividend Yield at 52-Week Low (`03_dividend_yield_screener.py`)

Identifies high dividend yield candidates near 52-week low with strong fundamentals (Dogs of NSE approach).

### Filters Applied:
- All value filters above
- Minimum trailing dividend yield threshold
- Sorted by dividend yield descending
- Default requires FCF > 0 (disable with `--no-require-positive-fcf`)

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

### Example:
```bash
# Dogs of NSE: high yield at 52w low, ROCE >= 12%, FCF > 0, D/E <= 1.0 on a date with fundamentals
python screening/03_dividend_yield_screener.py --asof 2025-03-01 --min-div-yield 3.0 --max-pct-from-52w-low 15.0 --format symbols
```

---

## Pipeline Chaining (Unix Philosophy)

Screeners output pure symbol streams (`--format symbols` or `--format plain`) designed for piping:

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

### JSON Mode for Programmatic Use
```bash
python screening/01_multibagger_screener.py --asof 2023-01-01 --top 5 --format json | jq '.candidates[].symbol'
```

---

## Data Requirements

Point-in-time valuation comes from the 1D Parquet catalog (`honba-data/data/catalog/1D/NSE/`)
plus the fundamentals lakehouse:

- `financial_ratios` — EPS, ROCE, debt/equity, operating margin, free cash flow, book equity.
- `financial_statements` — cash dividends, common dividends, diluted average shares.
- Derived fields: `P/E = close / EPS`, `P/B = close / (equity / shares)`,
  `yield = abs(dividends) / shares / close`.

Requires `fundamentals.duckdb` at `honba-data/data/fundamentals.duckdb`. Without it, value/dividend
screeners run price-only (fundamental columns are null) and fundamental filters are skipped.