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
| `--train-years N` | In-sample training window years (default: 3) |
| `--top N` | Limit to top N candidates (default: 10) |
| `--format FORMAT` | Output: `table` (rich), `json` (machine), `symbols` (space-separated), `plain` (newline-separated) |
| `--data-dir PATH` | Override catalog directory |

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
- Optional: P/E ratio ceiling
- Optional: Minimum ROCE
- Debt/Equity ceiling
- Recent 52-week low touch detection

### Options:
| Option | Default | Description |
|--------|---------|-------------|
| `--max-pct-from-52w-low` | 15.0 | Max % above 52-week low |
| `--min-div-yield` | 0.0 | Min dividend yield % (0 = disabled) |
| `--min-roce` | 10.0 | Min ROCE % |
| `--max-pe` | 20.0 | Max P/E ratio (0 = disabled) |
| `--max-debt-equity` | 1.5 | Max debt-to-equity ratio |

### Example:
```bash
# Screen deep value stocks at 52w low with P/E < 20, Debt/Eq < 1.0
python screening/02_value_52w_low_screener.py --asof 2023-01-01 --max-pe 20 --max-debt-equity 1.0 --format symbols
```

---

## Screener 3: High Dividend Yield at 52-Week Low (`03_dividend_yield_screener.py`)

Identifies high dividend yield candidates near 52-week low with strong fundamentals (Dogs of NSE approach).

### Filters Applied:
- All value filters above
- Minimum dividend yield threshold
- Sorted by dividend yield descending

### Options:
| Option | Default | Description |
|--------|---------|-------------|
| `--max-pct-from-52w-low` | 15.0 | Max % above 52-week low |
| `--min-div-yield` | 3.0 | Min dividend yield % |
| `--min-roce` | 12.0 | Min ROCE % |
| `--max-pe` | disabled | Max P/E ratio |
| `--max-debt-equity` | 1.0 | Max debt-to-equity ratio |

### Example:
```bash
# Dogs of NSE: High yield at 52w low, ROCE >= 12%, Debt/Eq <= 1.0
python screening/03_dividend_yield_screener.py --asof 2023-01-01 --min-div-yield 3.0 --max-pct-from-52w-low 10.0 --format symbols
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
python screening/03_dividend_yield_screener.py --asof 2023-01-01 --min-div-yield 3.0 --max-pct-from-52w-low 10.0 --format symbols \
  | python backtesting/08_chained_screener_and_sip.py --asof 2023-01-01 --sip-amount 10000
```

### Deep Value at 52w Low → Forward SIP Backtest
```bash
python screening/02_value_52w_low_screener.py --asof 2023-01-01 --max-pe 20 --max-debt-equity 1.0 --format symbols \
  | python backtesting/08_chained_screener_and_sip.py --asof 2023-01-01 --sip-amount 10000
```

### JSON Mode for Programmatic Use
```bash
python screening/01_multibagger_screener.py --asof 2023-01-01 --top 5 --format json | jq '.candidates[].symbol'
```

---

## Data Requirements

Requires:
- 1D NSE Parquet catalog at `honba-data/data/catalog/1D/NSE/` (or `--data-dir`)
- Optional: `fundamentals.duckdb` at `honba-data/data/fundamentals.duckdb` for ROCE, Debt/Eq, P/E, Div Yield overlays

Without fundamentals DB, screeners run on price/volume only (fundamental columns will be null).