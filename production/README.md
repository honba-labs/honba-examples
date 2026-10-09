# 10 Production – paper trading, live, monitoring, deployment

Production examples demonstrate the path from backtest to live trading. The first
three wire up an inline SMA-crossover strategy (`SmaPaper`, `SmaLive`, `SmaMulti`)
selected with `--strategy sma_crossover`; the last two are operational tooling with
no strategy. All examples run offline and deterministically on the `fake` adapter
unless you explicitly opt into a real broker (`--no-dry-run` on the Dhan example).

## At a glance

| #  | File                          | Strategy / focus  | What you learn                                                         |
|----|-------------------------------|-------------------|------------------------------------------------------------------------|
| 01 | `01_paper_trading.py`         | SMA crossover     | Paper trading session: simulated fills, real-time P&L, risk guards     |
| 02 | `02_live_trading_dhan.py`     | SMA crossover     | Dhan adapter: connect, place orders, handle rejections, sync positions |
| 03 | `03_multi_account.py`         | SMA crossover     | Multiple accounts: isolate capital, aggregate P&L, per-account risk    |
| 04 | `04_monitoring.py`            | —                 | Health checks: adapter latency, order throughput, position drift       |
| 05 | `05_deployment.py`            | —                 | Systemd/container deploy: config, secrets, logging, graceful shutdown  |

## The production pattern

```python
from honba.session import Session, SessionConfig
from honba.adapters.registry import AdapterRegistry
from honba.strategies.base import Strategy

# 1. Configure session (paper or live)
config = SessionConfig(
    adapter="dhan",
    adapter_config={"client_id": "...", "access_token": "..."},
    strategy=MyStrategy(),
    risk_limits={"max_position_pct": 0.1, "max_daily_loss": 50000},
    capital=10_00_000,
)

# 2. Run session (blocks until stop)
session = Session(config)
session.start()
# ... on signal: session.stop()
```

## Running them

```bash
# Paper trading (always works offline)
python production/01_paper_trading.py --strategy sma_crossover --capital 100000

# Live trading (requires Dhan credentials)
python production/02_live_trading_dhan.py --client-id XXX --access-token YYY --strategy sma_crossover

# Multi-account
python production/03_multi_account.py --accounts config/accounts.yaml

# Monitoring
python production/04_monitoring.py --port 9090

# Deployment config generation
python production/05_deployment.py --output /etc/honba/
```

## Key concepts

1. **Adapter swap** — Same strategy code runs paper ↔ live by changing adapter config
2. **Risk guards** — Session-level limits (position, daily loss, margin) enforced before order send
3. **Idempotency** — `client_order_id` ensures safe retries; broker deduplication
4. **Reconciliation** — Periodic `positions()` vs broker `holdings()` drift check
5. **Graceful shutdown** — Cancel open orders, flatten positions (optional), persist state

## From paper to live

```
paper (fake adapter) → paper (real adapter, simulated fills) → live (real adapter, real fills)
     ↑                      ↑                              ↑
  offline              network                         real money
  deterministic        non-deterministic               requires monitoring
```