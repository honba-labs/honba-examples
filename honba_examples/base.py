"""Base class for Honba examples with common configuration and utilities."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from typing import Any

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.markets.india.universes import resolve_universe
from honba.screener.coverage import DateInterval
from honba.screener.store import ParquetBarStore


class HonbaExample:
    """Base class for Honba examples providing common functionality.

    Attributes:
        universe_name: Name of the universe (e.g., 'nifty50', 'nifty200_alpha_30')
        exchange: Exchange code (default: 'NSE')
        timeframe: Bar timeframe (default: '1D')
        start_date: Start date for data loading
        end_date: End date for data loading
        initial_capital: Initial capital for backtesting
        warmup_days: Number of days for warmup period
        out_dir: Output directory for results
        data_dir: Data directory for Parquet store
    """

    universe_name: str = "nifty50"
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date = dt.date(2022, 1, 1)
    end_date: dt.date = dt.date(2025, 12, 31)
    initial_capital: float = 1_000_000.0
    warmup_days: int = 0
    out_dir: Path = Path("output")
    data_dir: Path | None = None

    def __init__(self, **kwargs: Any) -> None:
        """Initialize with optional overrides. Touches no files: the store and the
        output directory are created on first use, after the CLI has been parsed."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self._store: ParquetBarStore | None = None

    @property
    def store(self) -> ParquetBarStore:
        """Parquet bar store rooted at ``data_dir`` (resolved lazily so ``--data-dir`` applies)."""
        if self._store is None or (
            self.data_dir is not None and self._store.data_dir != Path(self.data_dir).resolve()
        ):
            self._store = ParquetBarStore(self.data_dir)
        return self._store

    def ensure_out_dir(self) -> Path:
        """Create and return ``out_dir``."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self.out_dir

    @classmethod
    def add_common_args(cls, parser: argparse.ArgumentParser) -> None:
        """Add common command-line arguments."""
        parser.add_argument(
            "--universe",
            dest="universe_name",
            default=cls.universe_name,
            help="Universe name (e.g., nifty50, nifty200_alpha_30)",
        )
        parser.add_argument("--exchange", default=cls.exchange, help="Exchange code")
        parser.add_argument("--timeframe", default=cls.timeframe, help="Bar timeframe")
        parser.add_argument(
            "--start",
            dest="start_date",
            type=lambda s: dt.date.fromisoformat(s),
            default=cls.start_date,
            help="Start date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--end",
            dest="end_date",
            type=lambda s: dt.date.fromisoformat(s),
            default=cls.end_date,
            help="End date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--capital",
            dest="initial_capital",
            type=float,
            default=cls.initial_capital,
            help="Initial capital (INR)",
        )
        parser.add_argument(
            "--warmup-days", type=int, default=cls.warmup_days, help="Warmup period in days"
        )
        parser.add_argument("--out-dir", type=Path, default=cls.out_dir, help="Output directory")
        parser.add_argument("--data-dir", type=Path, default=cls.data_dir, help="Data directory")

    def parse_args(self, args: list[str] | None = None) -> argparse.Namespace:
        """Parse command-line arguments."""
        parser = argparse.ArgumentParser(description=self.__doc__ or "Honba example")
        self.add_common_args(parser)
        self.add_custom_args(parser)
        parsed = parser.parse_args(args)
        for key, value in vars(parsed).items():
            if hasattr(self, key):
                setattr(self, key, value)
        return parsed

    def add_custom_args(self, parser: argparse.ArgumentParser) -> None:
        """Override to add example-specific arguments."""

    def load_universe(self) -> list[InstrumentId]:
        """Resolve and return universe instruments."""
        universe = resolve_universe(self.universe_name, exchange=self.exchange)
        print(f"[Universe] Resolved '{self.universe_name}': {len(universe)} equities")
        return universe

    def load_bars(
        self,
        instruments: list[InstrumentId],
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> list[Bar]:
        """Load bars for instruments within date range."""
        start = start or self.start_date
        end = end or self.end_date
        interval = DateInterval(start, end + dt.timedelta(days=1))

        bars: list[Bar] = []
        for inst in instruments:
            bars.extend(self.store.read(inst, self.timeframe, interval))
        bars.sort(key=lambda x: (x.ts, x.instrument_id.symbol))
        return bars

    def load_bars_with_warmup(
        self, instruments: list[InstrumentId], start: dt.date, end: dt.date
    ) -> tuple[list[Bar], list[Bar]]:
        """Load bars split into warmup and main periods.

        Warmup bars are the ``warmup_days`` calendar days immediately *before*
        ``start``; they feed indicators before any main-period bar is processed.

        Returns:
            Tuple of (warmup_bars, main_bars)
        """
        if self.warmup_days <= 0:
            # No warmup - all bars are main
            main_bars = self.load_bars(instruments, start, end)
            return [], main_bars

        warmup_start = start - dt.timedelta(days=self.warmup_days)
        warmup_bars = self.load_bars(instruments, warmup_start, start - dt.timedelta(days=1))
        main_bars = self.load_bars(instruments, start, end)
        return warmup_bars, main_bars

    def run(self) -> Any:
        """Run the example. Override in subclass."""
        raise NotImplementedError

    def main(self, args: list[str] | None = None) -> Any:
        """Entry point: parse args, initialize, and run."""
        self.parse_args(args)
        return self.run()


def ts_to_date(ts: int) -> dt.date:
    """Convert nanosecond timestamp to calendar date (UTC)."""
    return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).date()


def format_timestamp(ts: int) -> str:
    """Format nanosecond timestamp as readable date string."""
    try:
        return dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return "?"
