"""Component 3: the sweep — an obvious level broken, and rejected.

Price trades beyond a level by at least one tick and then CLOSES back
inside it. The close is the whole distinction: a break that holds is a
breakout, and the Donchian system on this dashboard exists to trade
exactly that. The two systems disagree about the same bar at the same
level, and the close is what separates them.

This is a SETUP, never an entry. Entering here would be guessing that the
extreme is in.

## Why only the first touch counts

A level is scanned for a sweep on exactly one bar: the first bar to trade
through it. That is not an optimisation shortcut, it is the premise. The
claim is about a pool of resting stops being triggered; once price has
been through the level those stops are gone, and a second visit is a
retest of an empty level, which is a different event with a different
story. `levels.py` already records that bar as `mitigated_bar_ts`, so the
scan is a lookup rather than a search.

The consequence worth stating: a bar that pokes through a level by less
than `sweep_penetration_ticks` still consumes it. With the default of one
tick that is the same condition as mitigation, but raise the penetration
and levels start being used up by pokes too small to qualify — which is
the honest model of the same pool being disturbed, not a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from .bars import known_at
from .bias import Direction
from .levels import LiquidityLevel, LevelBook
from .params import SweepParams


@dataclass(frozen=True)
class Sweep:
    level: LiquidityLevel
    #: The bar that did it.
    bar_ts: pd.Timestamp
    bar_index: int
    #: That bar's close — when the sweep became a fact.
    known_at: pd.Timestamp
    #: Which way the resulting trade faces. A swept HIGH is a bearish
    #: setup: the fade is short.
    direction: Direction
    #: The wick's extreme. The stop goes beyond this, so it is the number
    #: the whole risk model rests on.
    extreme: float
    #: How far past the level price reached, in price units.
    penetration: float


def find_sweeps(
    frame: pd.DataFrame, book: LevelBook, params: SweepParams
) -> List[Sweep]:
    """Every sweep in the series, in the order they became known."""
    index = frame.index
    values = index.values
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    closes = frame["Close"].to_numpy(dtype=float)

    out: List[Sweep] = []
    for level in book:
        sweep = _sweep_of(level, index, values, highs, lows, closes, params)
        if sweep is not None:
            out.append(sweep)

    out.sort(key=lambda s: (s.known_at, s.level.kind, s.level.price))
    return out


def _sweep_of(
    level: LiquidityLevel,
    index: pd.DatetimeIndex,
    values: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    params: SweepParams,
) -> Optional[Sweep]:
    if level.mitigated_bar_ts is None:
        return None  # never touched; nothing was swept

    position = int(np.searchsorted(values, level.mitigated_bar_ts.to_datetime64()))
    if position >= len(index) or index[position] != level.mitigated_bar_ts:
        return None

    close = closes[position]
    if not np.isfinite(close):
        return None

    if level.side == "high":
        reach = highs[position]
        if not np.isfinite(reach):
            return None
        if reach < level.price + params.penetration:
            return None  # touched, but not beyond by enough to be a grab
        if close >= level.price:
            return None  # it held. That is a breakout, not a sweep.
        return Sweep(
            level=level,
            bar_ts=index[position],
            bar_index=position,
            known_at=known_at(index[position], params.execution_timeframe),
            direction="bearish",
            extreme=float(reach),
            penetration=float(reach - level.price),
        )

    reach = lows[position]
    if not np.isfinite(reach):
        return None
    if reach > level.price - params.penetration:
        return None
    if close <= level.price:
        return None
    return Sweep(
        level=level,
        bar_ts=index[position],
        bar_index=position,
        known_at=known_at(index[position], params.execution_timeframe),
        direction="bullish",
        extreme=float(reach),
        penetration=float(level.price - reach),
    )
