"""production/04_monitoring: Health checks and metrics endpoint.

Serves adapter latency, order throughput, position drift, and system health on a
simple HTTP endpoint for Prometheus/Grafana scraping. The values are simulated
(random per scrape), so it shows the endpoint shape, not real measurements.

Run::

    python production/04_monitoring.py --port 9090

Pitfalls::

    - Simulated numbers: latency, order counts, drift, P&L and utilisation are drawn at random on
      every scrape and /health is always "healthy" with hard-coded checks. A dashboard or alert
      built on this cannot fail, so it proves nothing about the system it will watch. Wire real
      counters first.
    - Counters and staleness: the *_total series are re-drawn each scrape, so they go backwards and
      break rate(). There are no HELP/TYPE lines or sample timestamps, and a hung process would keep
      answering scrapes. Alert on scrape failure and on the age of the last real event, not just on
      a value.
    - The server binds 0.0.0.0 with no authentication, which exposes P&L to the network. Bind a
      loopback or internal address, or firewall the port. It also stops after --duration seconds.
    - Drift needs a real definition: broker positions against the internal ledger in quantity, with
      a tolerance and a re-check after every restart. honba_uptime_seconds is seconds since this
      process started; use UTC for timestamps and convert to IST only for display.
"""

from __future__ import annotations

import json
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]

_STARTED = time.monotonic()


class MetricsHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for /metrics endpoint."""

    def do_GET(self) -> None:
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()

            # Prometheus-format metrics
            metrics = generate_metrics()
            self.wfile.write(metrics.encode())
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            health = generate_health()
            self.wfile.write(json.dumps(health, indent=2).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress default log


def generate_metrics() -> str:
    """Generate Prometheus-format metrics."""
    lines = []

    # Adapter latency (simulated)
    adapter_latency = random.uniform(10, 200)  # ms
    lines.append(f'honba_adapter_latency_ms{{adapter="dhan"}} {adapter_latency:.2f}')

    # Order throughput
    orders_sent = random.randint(0, 50)
    orders_filled = random.randint(0, orders_sent)
    orders_rejected = orders_sent - orders_filled
    lines.append(f"honba_orders_sent_total {orders_sent}")
    lines.append(f"honba_orders_filled_total {orders_filled}")
    lines.append(f"honba_orders_rejected_total {orders_rejected}")

    # Position drift (bps)
    drift_bps = random.uniform(-50, 50)
    lines.append(f"honba_position_drift_bps {drift_bps:.2f}")

    # P&L
    pnl = random.uniform(-10000, 10000)
    lines.append(f"honba_pnl_rupees {pnl:.2f}")

    # Capital utilization
    utilization = random.uniform(0.3, 0.9)
    lines.append(f"honba_capital_utilization_pct {utilization:.2f}")

    # Session uptime
    lines.append(f"honba_uptime_seconds {int(time.monotonic() - _STARTED)}")

    return "\n".join(lines) + "\n"


def generate_health() -> dict[str, Any]:
    """Generate health check JSON."""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "checks": {
            "adapter_connected": True,
            "market_data_stream": True,
            "order_gateway": True,
            "position_reconciliation": "ok",
            "risk_limits": "within_bounds",
        },
        "metrics": {
            "adapter_latency_ms": random.uniform(10, 200),
            "orders_per_second": random.uniform(0.1, 5.0),
            "position_drift_bps": random.uniform(-50, 50),
        },
    }


def run(port: int = 9090, duration: int = 30) -> dict[str, Any]:
    """Run monitoring server for a duration."""
    server = HTTPServer(("0.0.0.0", port), MetricsHandler)

    # Run in thread for demo
    import threading

    def serve():
        server.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    time.sleep(duration)
    server.shutdown()

    return {
        "status": "completed",
        "port": port,
        "duration_seconds": duration,
        "metrics_sample": generate_metrics().split("\n")[:5],
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--duration", type=int, default=10)
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(port=args.port, duration=args.duration)

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
