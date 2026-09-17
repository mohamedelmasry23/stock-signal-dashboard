"""Component 4: the market structure shift — evidence control actually flipped.

After a sweep, price must close beyond the most recent opposing CONFIRMED
swing point, with a decisive body. Without this, the strategy is entering
on the sweep alone, which is a guess that the extreme is in.

Two thresholds, both parameters because both are judgement calls the
original method makes by eye:

    min_body_frac   body / range of the breaking candle. Displacement is
                    the point — a slow drift through a level is not the
                    same event as a fast one, and a long-wicked candle
                    that closes mid-range is a drift.
    sweep_window    how many bars after the sweep the shift may arrive in.
                    Widen it far enough and any later move confirms any
                    earlier sweep, which would make the component
                    meaningless while still producing trades.

The swing point must have confirmed before the bar that breaks it — see
the same argument in bias.py. A pivot confirmed by the breaking bar
includes that bar in its own right-hand side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .bars import known_at
from .params import SweepParams
from .sweep import Sweep
from .swings import Pivot, PivotSeries


@dataclass(frozen=True)
class StructureShift:
    bar_ts: pd.Timestamp
    bar_index: int
    known_at: pd.Timestamp
    #: The swing that was broken.
    broken: Pivot
    body_frac: float


def body_fraction(open_: float, high: float, low: float, close: float) -> float:
    """|close - open| as a fraction of the bar's range.

    A zero-range bar has no body fraction to speak of; it returns 0.0 so
    that a flat or halted bar can never pass a displacement test. Returning
    1.0 — which "the body is the whole range" would suggest — would make
    every halted bar maximally decisive.
    """
    span = high - low
    if not np.isfinite(span) or span <= 0:
        return 0.0
    return float(abs(close - open_) / span)


def find_structure_shift(
    frame: pd.DataFrame,
    sweep: Sweep,
    pivots: PivotSeries,
    params: SweepParams,
) -> Optional[StructureShift]:
    """The first qualifying shift within `sweep_window` bars of the sweep.

    The scan starts at the bar AFTER the sweep. The sweep bar itself
    closing beyond an opposing swing would be one candle doing both jobs,
    and the spec separates them deliberately: the sweep is the setup, the
    shift is the confirmation that it was not just a pause.
    """
    opens = frame["Open"].to_numpy(dtype=float)
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    closes = frame["Close"].to_numpy(dtype=float)
    index = frame.index

    first = sweep.bar_index + 1
    last = min(sweep.bar_index + params.sweep_window, len(index) - 1)

    for i in range(first, last + 1):
        close = closes[i]
        if not np.isfinite(close):
            continue

        # Opposing structure: a bullish reversal must break a swing HIGH.
        kind = "high" if sweep.direction == "bullish" else "low"
        pivot = pivots.last(kind, index[i])
        if pivot is None:
            continue

        broke = close > pivot.price if kind == "high" else close < pivot.price
        if not broke:
            continue

        fraction = body_fraction(opens[i], highs[i], lows[i], close)
        if fraction < params.min_body_frac:
            # Price got there, but not decisively. Keep scanning: a later
            # bar inside the window may do it properly.
            continue

        return StructureShift(
            bar_ts=index[i],
            bar_index=i,
            known_at=known_at(index[i], params.execution_timeframe),
            broken=pivot,
            body_frac=fraction,
        )

    return None
