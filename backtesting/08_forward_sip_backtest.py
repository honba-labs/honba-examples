"""backtesting/08_forward_sip_backtest: forward SIP backtester (reads --symbols or a stdin pipe).

Composable execution harness for the screening → backtest runtime plane.  Thin re-export of
:mod:`backtesting.08_chained_screener_and_sip` under the roadmap name
``08_forward_sip_backtest`` so the CLI plumbing in ``roadmap-chaining.md`` —

    python screening/01_multibagger_screener.py --asof 2023-01-01 --top 5 --format symbols \\
      | python backtesting/08_forward_sip_backtest.py --asof 2023-01-01 --sip-amount 10000

— and the recipe ``08_pipeline_intro`` can both find it.  All options are documented on the
underlying example; this shim rewrites argv[0] so ``--help`` shows the right script name.

.. deprecated::
   Import ``ChainedScreenerSipExample`` directly from
   ``backtesting.08_chained_screener_and_sip`` instead.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_ALIASED = Path(__file__).with_name("08_chained_screener_and_sip.py")
_spec = importlib.util.spec_from_file_location(
    "backtesting._aliased_08_chained_screener_and_sip", str(_ALIASED)
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
ChainedScreenerSipExample = _mod.ChainedScreenerSipExample  # type: ignore[attr-defined]
SipPortfolioStrategy = _mod.SipPortfolioStrategy  # type: ignore[attr-defined]

__all__ = ["ChainedScreenerSipExample", "SipPortfolioStrategy", "main"]


def main(argv: list[str] | None = None) -> Any:
    """Entrypoint that re-exposes the harness under the ``08_forward_sip_backtest`` name."""
    return ChainedScreenerSipExample().main(argv)


if __name__ == "__main__":
    sys.exit(main())
