"""backtesting/07_run_via_client: same strategy over InprocTransport and HttpTransport, run_id, assumptions.

Demonstrates the Honba run service (ADR 0017, E4-S3): a backtest is a first-class server
object with a run_id, status lifecycle, and assumptions including not_modelled.
This example runs the same strategy via the in-process transport and an HTTP transport
to a running `honba serve` and asserts parity of fills, run_id traceability, and
assumptions presence.

Run::

    # In-process only (no server needed)
    python examples/backtesting/07_run_via_client.py --data-dir data/catalog --inproc

    # Via HTTP to a running `honba serve` (requires the binary)
    python examples/backtesting/07_run_via_client.py --data-dir data/catalog --url http://127.0.0.1:8080

    # Both, asserting parity
    python examples/backtesting/07_run_via_client.py --data-dir data/catalog --both

Pitfalls::

    - Hindsight universe: the run is fixed to RELIANCE.NSE, one large stock still listed today, over
      2024-01-01 to 2024-06-01. That is one name chosen with hindsight in about five months of daily
      bars and one regime; it demonstrates the run service, not the strategy.
    - Parity is plumbing, not realism: assert_parity shows in-process and HTTP produce identical
      fills, which proves determinism. It does not show the fill model is realistic. Read
      assumptions.timing and assumptions.not_modelled in the result before trusting any metric.
    - Closed trades only: the comment in assert_parity says metrics come from closed round trips
      with no mark-to-market, so a position still open at the end (perhaps deep underwater) is not
      counted. Read the journal as well as the summary.
    - Data quality is the catalog's: bars come from --data-dir as stored. Confirm they are split and
      dividend adjusted and have no gaps, because the same strategy gives different fills on
      unadjusted data.
    - The seed (42) only pins stochastic components; it is not a robustness check. Vary the window
      and the instrument before concluding anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from honba.client import BacktestResult, Client


def run_inproc(data_dir: Path) -> BacktestResult:
    """Run the strategy over InprocTransport."""
    client = Client.inproc(data_dir, journals_dir=data_dir.parent / "journals_inproc")
    try:
        result = client.run_backtest(
            strategy="sma_crossover",
            universe="RELIANCE.NSE",
            start="2024-01-01",
            end="2024-06-01",
            seed=42,
            bar_spec="1d",
            initial_capital=1_000_000.0,
            timeout=60.0,
        )
        return result
    finally:
        client.close()


def run_http(base_url: str) -> BacktestResult:
    """Run the strategy over HttpTransport."""
    client = Client.http(base_url, timeout=60.0)
    try:
        result = client.run_backtest(
            strategy="sma_crossover",
            universe="RELIANCE.NSE",
            start="2024-01-01",
            end="2024-06-01",
            seed=42,
            bar_spec="1d",
            initial_capital=1_000_000.0,
            timeout=60.0,
        )
        return result
    finally:
        client.close()


def assert_parity(
    inproc: BacktestResult, http: BacktestResult, client_inproc: Client, client_http: Client
) -> None:
    """Assert that the two runs produce identical fills and run_id traceability."""
    # Same strategy, same seed, same data => same fills
    inproc_journal = client_inproc.backtest_journal(inproc.run_id)
    http_journal = client_http.backtest_journal(http.run_id)

    # Fills must match exactly (same seed, same data, same fill model)
    assert len(inproc_journal) == len(http_journal), "fill count mismatch"
    for a, b in zip(inproc_journal, http_journal, strict=True):
        assert a.order_id == b.order_id, f"order_id mismatch: {a.order_id} != {b.order_id}"
        assert a.instrument_id == b.instrument_id, "instrument_id mismatch"
        assert a.side == b.side, "side mismatch"
        assert abs(a.quantity - b.quantity) < 1e-9, "quantity mismatch"
        assert abs(a.price - b.price) < 1e-9, "price mismatch"
        assert a.ts_event == b.ts_event, "ts_event mismatch"

    # Both completed
    assert inproc.status == "completed"
    assert http.status == "completed"

    # Both have assumptions with not_modelled
    assert inproc.assumptions is not None
    assert http.assumptions is not None
    assert len(inproc.assumptions.not_modelled) > 0
    assert len(http.assumptions.not_modelled) > 0
    assert inproc.assumptions.timing
    assert http.assumptions.timing

    # Metrics should match (closed round-trips, no mark-to-market)
    assert inproc.metrics is not None
    assert http.metrics is not None
    assert inproc.metrics.trades == http.metrics.trades
    assert abs(inproc.metrics.net_pnl - http.metrics.net_pnl) < 1e-6
    assert abs(inproc.metrics.sharpe - http.metrics.sharpe) < 1e-6
    assert abs(inproc.metrics.max_drawdown - http.metrics.max_drawdown) < 1e-6
    assert abs(inproc.metrics.total_return - http.metrics.total_return) < 1e-6


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\nPitfalls::")[0] + "\n")
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Directory with SYMBOL.EXCHANGE.parquet bar files",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080",
        help="Base URL of running honba serve",
    )
    parser.add_argument(
        "--inproc",
        action="store_true",
        help="Run only in-process (default: both if server available)",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run only against HTTP server",
    )
    parser.add_argument(
        "--both",
        action="store_true",
        help="Run both and assert parity (default if neither --inproc nor --http)",
    )
    args = parser.parse_args(argv)

    data_dir = args.data_dir
    if not data_dir.exists():
        print(f"Data directory does not exist: {data_dir}", file=sys.stderr)
        return 1

    # Check if HTTP server is available
    http_available = False
    if args.http or args.both or (not args.inproc and not args.http):
        try:
            resp = httpx.get(f"{args.url}/health", timeout=5.0)
            http_available = resp.status_code == 200
        except (httpx.HTTPError, OSError):
            http_available = False

    if args.inproc or (not args.http and not http_available):
        print("Running in-process...")
        inproc_result = run_inproc(data_dir)
        print(f"Inproc result: run_id={inproc_result.run_id}, status={inproc_result.status}")
        print(f"  metrics: {inproc_result.metrics.model_dump() if inproc_result.metrics else None}")
        print(
            f"  assumptions: {inproc_result.assumptions.model_dump() if inproc_result.assumptions else None}"
        )
        if inproc_result.error:
            print(f"  error: {inproc_result.error}")

    if args.http or args.both or (not args.inproc and http_available):
        if not http_available:
            print("HTTP server not available at --url", file=sys.stderr)
            return 1
        print("Running via HTTP...")
        http_result = run_http(args.url)
        print(f"HTTP result: run_id={http_result.run_id}, status={http_result.status}")
        print(f"  metrics: {http_result.metrics.model_dump() if http_result.metrics else None}")
        print(
            f"  assumptions: {http_result.assumptions.model_dump() if http_result.assumptions else None}"
        )
        if http_result.error:
            print(f"  error: {http_result.error}")

    if args.both or (not args.inproc and not args.http and http_available):
        print("Asserting parity...")
        client_inproc = Client.inproc(data_dir, journals_dir=data_dir.parent / "journals_inproc")
        client_http = Client.http(args.url, timeout=60.0)
        try:
            inproc_result = run_inproc(data_dir)
            http_result = run_http(args.url)
            assert_parity(inproc_result, http_result, client_inproc, client_http)
            print("✓ Parity confirmed: inproc and HTTP produce identical fills and assumptions")
        finally:
            client_inproc.close()
            client_http.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
