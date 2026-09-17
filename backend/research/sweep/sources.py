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

`DatabentoSource` is the real one and is deliberately NOT implemented
here. The API key and the specification for how to pull, page and cache
are coming in a separate brief, and guessing at them would produce an
untested network adapter that looks finished. What this file does commit
to is the CONTRACT that adapter must satisfy, below, so the strategy code
above it does not move when it lands.

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
    """Intraday bars from Databento. Not implemented yet, on purpose.

    Everything above this line runs without it, and the moment it exists
    the only change needed is which source the CLI constructs.

    What it must do when written:

      1. Authenticate from `DATABENTO_API_KEY` in the environment — never
         a literal in this file, and never a value committed anywhere near
         `backend/data/`.
      2. Pull OHLCV at ONE resolution (the finest the run needs) for each
         symbol, paging over the requested window.
      3. Cache to disk in its own SQLite file, following the isolation
         rule in `app/backtest/prices.py`: its own engine, its own bare
         MetaData, never attached to `app.database.Base`, and under
         `data/` so the existing gitignore covers it. Intraday history is
         large and metered; refetching it per run is both slow and billed.
      4. Return frames satisfying the contract in this module's docstring
         — in particular, DROP the final bar if the window ends inside a
         session. The daily pipeline already learned this one the hard
         way; see `drop_in_progress_bar()` and the note in CLAUDE.md.
      5. Map continuous-contract symbols explicitly. A futures backtest
         that silently switches contract months records the roll gap as a
         return, which is why `app/backtest/universe.py` excludes futures
         from the daily runs altogether.
    """

    def __init__(self, dataset: str, schema: str = "ohlcv-1m", api_key: Optional[str] = None):
        self.dataset = dataset
        self.schema = schema
        self.api_key = api_key

    def load(self, symbols: Iterable[str], start: date, end: date) -> Dict[str, pd.DataFrame]:
        raise NotImplementedError(
            "DatabentoSource is a placeholder. The pull/page/cache design is "
            "specified in this module's docstring and is waiting on the API "
            "key and the backtest brief. Use DailyCacheSource until then."
        )
