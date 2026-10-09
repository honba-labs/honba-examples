"""production/04_monitoring: Health checks and metrics endpoint.

Exposes adapter latency, order throughput, position drift, and system health
via a simple HTTP endpoint for Prometheus/Grafana scraping.

Run::

    python production/04_monitoring.py --port 9090
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


__all__ = ["main", "run"]


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
    lines.append(f'honba_orders_sent_total {orders_sent}')
    lines.append(f'honba_orders_filled_total {orders_filled}')
    lines.append(f'honba_orders_rejected_total {orders_rejected}')

    # Position drift (bps)
    drift_bps = random.uniform(-50, 50)
    lines.append(f'honba_position_drift_bps {drift_bps:.2f}')

    # P&L
    pnl = random.uniform(-10000, 10000)
    lines.append(f'honba_pnl_rupees {pnl:.2f}')

    # Capital utilization
    utilization = random.uniform(0.3, 0.9)
    lines.append(f'honba_capital_utilization_pct {utilization:.2f}')

    # Session uptime
    lines.append(f'honba_uptime_seconds {int(time.time())}')

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
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(port=args.port, duration=args.duration)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())