"""Fixture builders for the sweep tests.

Kept out of `tests/` so that several test modules can share one definition
of "a bar" — three slightly different hand-rolled bar builders across
three files is how fixtures start disagreeing about what an OHLC row
means, and a test that passes against a malformed fixture proves nothing.

Nothing here is used by the strategy itself.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import pandas as pd

from .bars import known_at
from .bias import Direction
from .fvg import FairValueGap
from .levels import LiquidityLevel
from .mss import StructureShift
from .params import SweepParams
from .strategy import Setup, StrategyContext
from .sweep import Sweep
from .swings import Pivot


def bars(
    rows: Sequence[Sequence[float]],
    start: str = "2026-01-05 00:00",
    freq: str = "5min",
    tz: str = "UTC",
) -> pd.DataFrame:
    """A frame from (open, high, low, close) tuples, volume filled in.

    Deliberately NOT validating that high >= max(open, close): some tests
    need a malformed bar to check that the code rejects it rather than
    silently producing a level from it.
    """
    index = pd.date_range(start=start, periods=len(rows), freq=freq, tz=tz)
    return pd.DataFrame(
        {
            "Open": [r[0] for r in rows],
            "High": [r[1] for r in rows],
            "Low": [r[2] for r in rows],
            "Close": [r[3] for r in rows],
            "Volume": [1.0] * len(rows),
        },
        index=index,
    )


def walk(
    n: int,
    seed: int = 0,
    start: str = "2026-01-05 00:00",
    freq: str = "5min",
    base: float = 5000.0,
    step: float = 0.5,
) -> pd.DataFrame:
    """A reproducible random walk with well-formed bars.

    Seeded rather than random: a look-ahead test that fails once in twenty
    runs is worse than no test, because it gets re-run until it passes.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    close = base + rng.normal(0, step, n).cumsum()
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0, step, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, step, n))
    index = pd.date_range(start=start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1.0},
        index=index,
    )


def make_setup(
    frame: pd.DataFrame,
    direction: Direction,
    entry_limit: float,
    stop: float,
    target: float,
    *,
    valid_from_bar: int = 0,
    expires_bar: Optional[int] = None,
    timeframe: str = "5min",
) -> Setup:
    """A `Setup` with plausible but inert provenance.

    The sweep/shift/gap objects are real instances rather than None so the
    production types stay non-optional: a simulator that has to tolerate a
    missing `sweep` in tests will eventually tolerate one in a run.
    """
    ts = frame.index[valid_from_bar]
    valid_from = known_at(ts, timeframe)
    last = len(frame) - 1
    expires_at = frame.index[min(expires_bar if expires_bar is not None else last, last)]

    level = LiquidityLevel(
        kind="prev_day_high" if direction == "bearish" else "prev_day_low",
        side="high" if direction == "bearish" else "low",
        price=target,
        known_at=valid_from,
        source_ts=ts,
        expires_at=expires_at + pd.Timedelta(days=5),
    )
    sweep = Sweep(
        level=level,
        bar_ts=ts,
        bar_index=valid_from_bar,
        known_at=valid_from,
        direction=direction,
        extreme=stop,
        penetration=0.01,
    )
    pivot = Pivot(
        kind="high" if direction == "bullish" else "low",
        ts=ts,
        price=entry_limit,
        confirmed_at=valid_from,
        timeframe=timeframe,
    )
    shift = StructureShift(
        bar_ts=ts, bar_index=valid_from_bar, known_at=valid_from, broken=pivot, body_frac=1.0
    )
    gap = FairValueGap(
        direction=direction,
        bottom=min(entry_limit, entry_limit + 0.01),
        top=max(entry_limit, entry_limit + 0.01),
        bar_ts=ts,
        bar_index=valid_from_bar,
        known_at=valid_from,
    )
    risk = abs(entry_limit - stop)
    return Setup(
        direction=direction,
        entry_limit=entry_limit,
        stop=stop,
        target=target,
        valid_from=valid_from,
        expires_at=expires_at,
        reward_risk=abs(target - entry_limit) / risk if risk else 0.0,
        sweep=sweep,
        shift=shift,
        gap=gap,
        target_level=level,
    )


def make_context(
    symbol: str,
    frame: pd.DataFrame,
    setups: Sequence[Setup],
    params: Optional[SweepParams] = None,
) -> StrategyContext:
    """A context carrying hand-built setups and nothing else.

    Lets the simulator be tested on exactly one order at a time, which the
    full pipeline cannot do — its setups are whatever the detectors happen
    to find.
    """
    from .bars import build_timeframes
    from .bias import BiasSeries
    from .levels import LevelBook

    params = params or SweepParams()
    timeframes = build_timeframes(
        frame, execution=params.execution_timeframe, higher=(), day_boundary_hour=params.day_boundary_hour
    )
    return StrategyContext(
        symbol=symbol,
        params=params,
        timeframes=timeframes,
        levels=LevelBook([]),
        bias=BiasSeries([], enabled=False),
        sweeps=[],
        setups=list(setups),
    )
