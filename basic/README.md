# Basic – broker adapter mechanics

These examples walk the plumbing that every strategy sits on: registering an adapter,
connecting, reading the instrument master, streaming quotes, placing paper orders and
reading the account books. There is **no trading strategy here** — the focus is the
adapter façade and its role protocols (`ADR 0010`), so no broker wire format leaks past
the boundary.

Every script runs offline and deterministically on the `fake` adapter (synthetic
instruments, quotes and fills) by default, so no credentials and no network are needed.
Swap `--adapter dhan` (or any registered adapter) plus its `--config KEY=VALUE` pairs for
real connectivity.

## At a glance

| # | File | Strategy / focus | What you learn |
|---|---|---|---|
| 01 | `01_connect_dhan.py` | — | Resolve an adapter through the registry; print session and capabilities |
| 02 | `02_fetch_instruments.py` | — | Pull the instrument master and search it; `Instrument` fields at the boundary |
| 03 | `03_subscribe_quotes.py` | — | `subscribe`/`unsubscribe` for streamed `QuoteTick`s vs one-shot `quote`; capability refusals as data |
| 04 | `04_place_order_paper.py` | — | Intent → report → fills; idempotent `client_order_id`, cancel, reject as a report |
| 05 | `05_check_positions.py` | — | Positions, funds and trades after a round trip; flat positions are omitted |

## Running them

```bash
python basic/01_connect_dhan.py
python basic/02_fetch_instruments.py --query RELIANCE --out instruments.json
python basic/03_subscribe_quotes.py --advances 5
python basic/04_place_order_paper.py --out orders.json
python basic/05_check_positions.py --out positions.json
pytest tests/unit -q -k "connect_dhan or fetch_instruments or subscribe_quotes or place_order_paper or check_positions"
```

## The adapter façade

Adapters are discovered through the registry (`available_adapters`, `resolve_adapter`,
`default_registry`) and implement the canonical interface plus role protocols. A single
`connect()` returns a session; capabilities are pure data, so an unsupported request
(streaming mode, holdings, depth) is refused *before* any I/O, and the refusal can be
reported as data rather than a traceback. See also the dedicated
[adapter examples](../../honba-adapters/examples/) for each broker.
