from __future__ import annotations

from typing import Any


def resolve_settlement_days(
    exchange: str | None,
    as_of=None,
    cli_value: int | None = None,
    cfg_value: Any = None,
) -> tuple[int, str]:
    """Return (settlement_days, source) where source in {'cli','config','market'}.

    Precedence: cli_value (not None) > cfg_value (not None/truthy as used) > market default.
    Mirrors examples: cli overrides config overrides settlement_days_for(exchange).
    """
    if cli_value is not None:
        return int(cli_value), "cli"
    # config may be 0 or int; treat None/empty as not set
    if cfg_value is not None and cfg_value != "":
        try:
            days = int(cfg_value)
            return days, "config"
        except (TypeError, ValueError):
            pass
    # fallback to market
    exch = exchange or "NSE"
    src = f"engine:settlement_days_for({exch}, as_of={as_of})" if as_of is not None else "market"
    try:
        from honba.markets.india import settlement_days_for

        days = settlement_days_for(exch, as_of=as_of)
        return int(days), src
    except (ImportError, AttributeError, ValueError, TypeError):
        try:
            from honba.markets.india import settlement_days_for

            days = settlement_days_for(exch)
            return int(days), src
        except (ImportError, AttributeError, ValueError, TypeError):
            return 1, src


