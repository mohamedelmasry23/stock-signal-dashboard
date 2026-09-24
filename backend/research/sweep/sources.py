"""Where bars come from.

The strategy code is deliberately source-agnostic: `build_context` takes a
DataFrame and asks nothing about where it came from. This module holds the
adapters, and there are two of them for two different jobs.

`DailyCacheSource` reads the project's existing 2016-2026 daily cache. It
cannot express the strategy faithfully — there are no Asia or London
sessions in a daily bar, and a "4H structure shift" resampled from daily
data is the daily bar under another name — but it runs today, on ten years
of data, over the same universe every other experiment used. It is for
smoke-testing the machinery and for the `donchian_veto` variant, which is
a daily rule by construction.

`DatabentoSource` is the real one. It reads the continuous, back-adjusted
1-minute CSVs that `fetch_futures_data.py` writes (that script owns every
paid network call) and resamples them to the execution timeframe.

## The contract

A source returns, per symbol, a DataFrame with:

  * a tz-aware DatetimeIndex labelled by each bar's OPEN, in any timezone
    (`normalize_bars` converts to UTC);
  * float columns Open, High, Low, Close and ideally Volume;
  * bars at ONE resolution, the finest the strategy will use. Everything
    coarser is resampled by `bars.build_timeframes`, which is what makes
    the higher-timeframe look-ahead trap unrepresentable. A source that
    supplies its own 1H and 4H series alongside would reintroduce it.
  * no partial final bar. A bar still forming has a high and low that will
    change, and every level built from it would be revised after the fact.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

import pandas as pd

from .bars import normalize_bars
from .params import SweepParams, ALL_LEVEL_TYPES


class BarSource(Protocol):
    """Anything that can hand the strategy bars for a set of symbols."""

    def load(self, symbols: Iterable[str], start: date, end: date) -> Dict[str, pd.DataFrame]:
        ...


#: Params shaped for daily bars: no sessions (a daily bar has none), and
#: every timeframe collapsed onto the feed's own. Exposed as a function
#: rather than a constant so callers get a fresh frozen instance to
#: `.replaced()` from.
def daily_params(**overrides) -> SweepParams:
    """A `SweepParams` that is honest about what daily bars can support.

    Asia and London levels are dropped rather than approximated. The
    structure and bias timeframes collapse onto the daily bar itself,
    which means the "higher timeframe" separation the method depends on is
    ABSENT from this configuration — bias, structure and execution are all
    read from the same series. Any result from it is a result about the
    sweep-and-reclaim pattern on daily bars, not about the method.
    """
    base = dict(
        execution_timeframe="1d",
        structure_timeframe="1d",
        bias_timeframe="1d",
        # A daily bar stamped 00:00 UTC is 19:00 the previous evening in
        # New York, so an 18:00 boundary would push every bar into the
        # next trading day. Zero puts it back on its own date.
        day_boundary_hour=0,
        level_types=tuple(
            k for k in ALL_LEVEL_TYPES if not k.startswith(("asia", "london"))
        ),
        tick_size=0.01,
    )
    base.update(overrides)
    return SweepParams(**base)


class DailyCacheSource:
    """The project's own daily bar cache, as a bar source.

    Dividend-adjusted by default, matching every backtest in this repo —
    `app/backtest/prices.py` explains why total return needs it. The chart
    in the live app uses raw closes instead, and mixing the two inside one
    run is the mistake that module's docstring warns about.
    """

    def __init__(self, *, price_mode: str = "adjusted", offline: bool = True):
        self.price_mode = price_mode
        self.offline = offline

    def load(self, symbols: Iterable[str], start: date, end: date) -> Dict[str, pd.DataFrame]:
        from app.backtest.prices import load_prices

        raw = load_prices(
            sorted(set(symbols)),
            start,
            end,
            offline=self.offline,
            price_mode=self.price_mode,
            verbose=False,
        )
        return {symbol: normalize_bars(frame) for symbol, frame in raw.items()}


class DatabentoSource:
    """Intraday futures bars produced by `fetch_futures_data.py`.

    That script owns everything that costs money or touches the network —
    pulling, paging, caching, rolling and back-adjusting. This class only
    reads its output, so a backtest can never trigger a paid request.

    Bars arrive at 1 minute and are resampled here to the strategy's
    execution timeframe (5 minutes by default). Resampling once, from the
    finest feed, is the same rule `bars.build_timeframes` applies upward:
    every coarser bar is derived, none is fetched separately, so no
    timeframe can disagree with another about what happened.
    """

    def __init__(self, data_dir: Optional[Path] = None, execution_timeframe: str = "5min",
                 day_boundary_hour: int = 18):
        from .fetch_futures_data import DATA_DIR

        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.execution_timeframe = execution_timeframe
        self.day_boundary_hour = day_boundary_hour

    def path(self, symbol: str) -> Path:
        return self.data_dir / f"{symbol}.csv"

    def load_raw(self, symbol: str) -> pd.DataFrame:
        """The 1-minute file exactly as written, as a normalised frame."""
        path = self.path(symbol)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} does not exist. Fetch it first:\n"
                f"    ../.venv/bin/python -m research.sweep.fetch_futures_data --roots {symbol}"
            )
        frame = pd.read_csv(path)
        # utc=True, then the offsets in the file decide the instant. Parsing
        # as local time would merge November's two 01:xx hours.
        index = pd.to_datetime(frame.pop("timestamp"), utc=True, format="ISO8601")
        frame.index = pd.DatetimeIndex(index)
        frame = frame.rename(columns=str.capitalize)
        return normalize_bars(frame)

    def load(self, symbols: Iterable[str], start: Optional[date] = None,
             end: Optional[date] = None) -> Dict[str, pd.DataFrame]:
        from .bars import resample_ohlc

        out: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            bars = self.load_raw(symbol)
            if start is not None:
                bars = bars[bars.index >= pd.Timestamp(start, tz="UTC")]
            if end is not None:
                bars = bars[bars.index < pd.Timestamp(end, tz="UTC")]
            if self.execution_timeframe != "1min":
                bars = resample_ohlc(bars, self.execution_timeframe, self.day_boundary_hour)
            out[symbol] = bars
        return out
