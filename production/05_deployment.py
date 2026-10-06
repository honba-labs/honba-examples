"""production/05_deployment: Systemd/container deployment configuration.

Generates systemd service files, Docker Compose, and environment configuration
for production deployment of Honba strategies.

Run::

    python production/05_deployment.py --output /etc/honba/
    python production/05_deployment.py  # prints to stdout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba_examples.jsonable import jsonable

__all__ = ["main", "run"]


SYSTEMD_SERVICE = """[Unit]
Description=Honba Trading Strategy: {strategy_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory={workdir}
Environment=PYTHONPATH={workdir}
Environment=HONBA_STRATEGIES_DIR={strategies_dir}
EnvironmentFile=-{env_file}
ExecStart={python} -m honba.session.run --config {config_file}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=honba-{strategy_name}

# Security
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={workdir}/logs {workdir}/data

[Install]
WantedBy=multi-user.target
"""

DOCKER_COMPOSE = """version: '3.8'

services:
  honba-{strategy_name}:
    image: honba/{strategy_name}:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: honba-{strategy_name}
    restart: unless-stopped
    environment:
      - PYTHONPATH=/app
      - HONBA_STRATEGIES_DIR=/app/strategies
    env_file:
      - .env
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - honba-net
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M

networks:
  honba-net:
    driver: bridge
"""

DOCKERFILE = """FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    gcc \\
    libpq-dev \\
    && rm -rf /var/lib/apt/lists/*

# Install Honba and dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Non-root user
RUN useradd -m -u 1000 honba && chown -R honba:honba /app
USER honba

CMD ["python", "-m", "honba.session.run", "--config", "config/production.toml"]
"""

ENV_TEMPLATE = """# Honba Production Environment
# Copy to .env and fill in values

# Adapter credentials
DHAN_CLIENT_ID=
DHAN_ACCESS_TOKEN=

# Strategy config
STRATEGY_NAME=sma_crossover
SYMBOL=RELIANCE
EXCHANGE=NSE
TIMEFRAME=1d
CAPITAL=100000
FAST_PERIOD=10
SLOW_PERIOD=30

# Risk limits
MAX_POSITION_PCT=0.1
MAX_DAILY_LOSS=50000
MAX_DRAWDOWN_PCT=0.15

# Logging
LOG_LEVEL=INFO
LOG_FILE=/app/logs/honba.log

# Monitoring
METRICS_PORT=9090
HEALTH_PORT=9091
"""


def run(
    strategy_name: str = "sma_crossover",
    output: Path | None = None,
    user: str = "honba",
    group: str = "honba",
    workdir: str = "/opt/honba",
    strategies_dir: str = "/opt/honba/strategies",
    python: str = "/usr/bin/python3",
) -> dict[str, Any]:
    config = {
        "strategy_name": strategy_name,
        "user": user,
        "group": group,
        "workdir": workdir,
        "strategies_dir": strategies_dir,
        "python": python,
        "env_file": f"{workdir}/.env",
        "config_file": f"{workdir}/config/{strategy_name}.toml",
    }

    systemd = SYSTEMD_SERVICE.format(**config)
    docker_compose = DOCKER_COMPOSE.format(strategy_name=strategy_name)
    dockerfile = DOCKERFILE
    env = ENV_TEMPLATE

    if output:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)

        # Systemd
        (output / f"honba-{strategy_name}.service").write_text(systemd)

        # Docker
        (output / "docker-compose.yml").write_text(docker_compose)
        (output / "Dockerfile").write_text(dockerfile)

        # Env template
        (output / ".env.template").write_text(env)

        # Config directory
        (output / "config").mkdir(exist_ok=True)
        config_toml = f"""# {strategy_name} production config
[strategy]
name = "{strategy_name}"
symbol = "RELIANCE"
exchange = "NSE"
timeframe = "1d"
capital = 100000
fast_period = 10
slow_period = 30

[risk]
max_position_pct = 0.1
max_daily_loss = 50000
max_drawdown_pct = 0.15

[adapter]
name = "dhan"
# client_id and access_token from .env

[session]
log_level = "INFO"
metrics_port = 9090
"""
        (output / f"config/{strategy_name}.toml").write_text(config_toml)

        return {
            "status": "written",
            "output_dir": str(output),
            "files": [
                f"honba-{strategy_name}.service",
                "docker-compose.yml",
                "Dockerfile",
                ".env.template",
                f"config/{strategy_name}.toml",
            ],
        }

    return {
        "status": "stdout",
        "systemd_service": systemd,
        "docker_compose": docker_compose,
        "dockerfile": dockerfile,
        "env_template": env,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--strategy", type=str, default="sma_crossover")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--user", type=str, default="honba")
    parser.add_argument("--group", type=str, default="honba")
    parser.add_argument("--workdir", type=str, default="/opt/honba")
    parser.add_argument("--strategies-dir", type=str, default="/opt/honba/strategies")
    parser.add_argument("--python", type=str, default="/usr/bin/python3")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run(
        strategy_name=args.strategy,
        output=args.output,
        user=args.user,
        group=args.group,
        workdir=args.workdir,
        strategies_dir=args.strategies_dir,
        python=args.python,
    )

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())