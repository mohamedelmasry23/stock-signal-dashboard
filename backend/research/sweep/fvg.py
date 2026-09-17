"""Component 5: the fair value gap — where the limit order rests.

A fast move leaves an imbalance: three candles where candle 1's and
candle 3's wicks do not overlap. The strategy waits for price to retrace
into that gap rather than entering at market, which buys a tighter stop
and a better R multiple. If price never comes back, the trade is missed,
and that is correct behaviour rather than something to engineer around.

## Detected at candle 3's close

This is the third of the spec's named coding errors and the easiest one to
write by accident, because the gap is *visually* between candles 1 and 3
and it is tempting to stamp it at candle 2, where it appears on the chart.
But the gap is not knowable until candle 3 has closed and its wick is
final. `known_at` is therefore candle 3's close, and `bar_index` is
candle 3 — never the middle candle.

## Which gap, when the leg leaves several

Scanning runs BACKWARD from the bar after the structure shift toward the
sweep, and takes the first gap that qualifies. That means the gap nearest
the shift wins — the imbalance the shift itself left, which is what the
spec asks for. Earlier gaps in the same leg are the fallback, taken only
when the nearest one is too small to count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .bars import known_at
from .bias import Direction
from .params import SweepParams


@dataclass(frozen=True)
class FairValueGap:
    direction: Direction
    #: Gap edges in price order. `top` > `bottom` always.
    bottom: float
    top: float
    #: Candle 3 — the bar that completed it.
    bar_ts: pd.Timestamp
    bar_index: int
    known_at: pd.Timestamp

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def entry_limit(self, depth: float) -> float:
        """Where the limit rests, `depth` of the way across the gap.

        Depth 0 is the PROXIMAL edge — the side price reaches first, so
        the most likely to fill and the worst price. Depth 1 is the far
        edge: a better fill that happens less often. Which of those is
        better is an empirical question, which is why it is a parameter
        and why `SENSITIVITY_GRID` walks it.
        """
        if self.direction == "bullish":
            # Price falls into the gap from above, so it meets the top first.
            return self.top - depth * self.size
        return self.bottom + depth * self.size


def find_entry_gap(
    frame: pd.DataFrame,
    direction: Direction,
    sweep_index: int,
    shift_index: int,
    params: SweepParams,
) -> Optional[FairValueGap]:
    """The gap the displacement left, or None if it left none big enough."""
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    index = frame.index

    # Candle 3 may be the bar after the shift (the shift candle is then
    # the middle one, which is the usual shape) down to the first triple
    # that could exist after the sweep.
    newest = min(shift_index + 1, len(index) - 1)
    oldest = max(sweep_index + 2, 2)

    for third in range(newest, oldest - 1, -1):
        first = third - 2
        if first < 0:
            break
        gap = _gap_between(highs, lows, first, third, direction)
        if gap is None:
            continue
        bottom, top = gap
        if top - bottom < params.min_fvg_size:
            continue
        return FairValueGap(
            direction=direction,
            bottom=float(bottom),
            top=float(top),
            bar_ts=index[third],
            bar_index=third,
            known_at=known_at(index[third], params.execution_timeframe),
        )
    return None


def _gap_between(
    highs: np.ndarray, lows: np.ndarray, first: int, third: int, direction: Direction
) -> Optional[tuple]:
    """The non-overlapping span between candle 1 and candle 3, if any.

    Strict inequality: wicks that touch exactly have no imbalance between
    them, and admitting a zero-width gap would put a limit order at a price
    that every neighbouring bar already traded.
    """
    if direction == "bullish":
        bottom, top = highs[first], lows[third]
    else:
        bottom, top = highs[third], lows[first]
    if not (np.isfinite(bottom) and np.isfinite(top)):
        return None
    if top <= bottom:
        return None
    return bottom, top
