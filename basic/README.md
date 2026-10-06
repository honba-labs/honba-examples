# 01 Basic – broker adapters

Walk these files in order. Every example defaults to the offline, deterministic
`fake` adapter, so the whole path runs without credentials or network; add
`--adapter dhan --config client_id=... --config access_token=...` (or any
registered adapter) to point the same code at a real broker.

| #  | File                              | What you learn                                                     |
|----|-----------------------------------|--------------------------------------------------------------------|
| 01 | `01_connect_dhan.py`              | Look an adapter up in the registry, connect, read `SessionInfo` and `AdapterCapabilities` |
| 02 | `02_fetch_instruments.py`         | Pull the instrument master, search it, summarise lots and ticks    |
| 03 | `03_subscribe_quotes.py`          | `subscribe`/`unsubscribe` with a stream callback vs a one-shot `quote`; unsupported capabilities as data |
| 04 | `04_place_order_paper.py`         | `OrderIntent` → `OrderReport`: fills, resting limits, rejections, idempotent `client_order_id` |
| 05 | `05_check_positions.py`           | The account books: `positions()`, `funds()`, `trades()` and capability-gated `holdings()` |

## The three rules these examples demonstrate

1. **The adapter boundary is the anti-corruption layer.** Everything past it is a
   Honba type (`Instrument`, `QuoteTick`, `OrderReport`, `Position`); no broker
   wire format reaches the example.
2. **Broker refusals are data.** An order the broker refuses comes back as
   `status=REJECTED` with a `reject_reason`; only connection and capability
   problems raise.
3. **Capabilities are checked before I/O.** `capabilities()` is pure data, so an
   unsupported request is refused without a network call.

## Running them

```bash
python basic/01_connect_dhan.py
python basic/02_fetch_instruments.py --query RELIANCE --out instruments.json
python basic/03_subscribe_quotes.py --advances 3
python basic/04_place_order_paper.py
python basic/05_check_positions.py --out positions.json
pytest tests/unit -q          # the tests behind them (no network)
```
