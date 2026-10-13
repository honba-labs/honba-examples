"""honba_examples.pipeline: composable in-code strategy pipeline DSL.

Compose point-in-time screeners, quality/regime filters, timing signals, position sizers and
execution harnesses with the ``>>`` (or ``|``) operator::

    from honba_examples.pipeline import (
        Pipeline,
        DividendYieldScreener,
        FundamentalQualityFilter,
        MarketRegimeFilter,
        FixedSipSizer,
        SipBacktestHarness,
    )

    pipeline = (
        DividendYieldScreener(min_yield_pct=3.0, max_pct_from_52w_low=10.0)
        >> FundamentalQualityFilter(min_roce=15.0, max_debt_to_equity=1.0)
        >> MarketRegimeFilter(benchmark="NIFTY", condition="above_sma200")
        >> FixedSipSizer(monthly_amount=10000.0)
        >> SipBacktestHarness(asof="2023-01-01")
    )
    results = pipeline.run()
    results.print_summary()
"""

from __future__ import annotations

from honba_examples.pipeline.base import (
    Pipeline,
    PipelineContext,
    PipelineResult,
    PipeMixin,
    Step,
    as_date,
)
from honba_examples.pipeline.execution import SipBacktestHarness
from honba_examples.pipeline.filters import (
    FundamentalQualityFilter,
    LiquidityFilter,
    MarketRegimeFilter,
)
from honba_examples.pipeline.io import dumps, parse_symbols, read_symbols, to_stream
from honba_examples.pipeline.screeners import (
    DividendYieldScreener,
    MultibaggerScreener,
    ScreenerStep,
    ValueScreener,
)
from honba_examples.pipeline.signals import BreakoutSignal, BuyAndHoldSignal, PullbackSignal
from honba_examples.pipeline.sizing import (
    EqualWeightSizer,
    FixedSipSizer,
    InverseVolatilitySizer,
    SizerStep,
)

__all__ = [
    "BreakoutSignal",
    "BuyAndHoldSignal",
    "DividendYieldScreener",
    "EqualWeightSizer",
    "FixedSipSizer",
    "FundamentalQualityFilter",
    "InverseVolatilitySizer",
    "LiquidityFilter",
    "MarketRegimeFilter",
    "MultibaggerScreener",
    "PipeMixin",
    "Pipeline",
    "PipelineContext",
    "PipelineResult",
    "PullbackSignal",
    "ScreenerStep",
    "SipBacktestHarness",
    "SizerStep",
    "Step",
    "ValueScreener",
    "as_date",
    "dumps",
    "parse_symbols",
    "read_symbols",
    "to_stream",
]
