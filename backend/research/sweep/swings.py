"""Swing pivots, and the confirmation lag that makes them honest.

A swing high is a bar whose high is greater than the `lookback` bars on
BOTH sides of it. The right-hand side is the whole point: it does not
exist yet when the pivot prints. A pivot with a 3-bar lookback becomes
knowable three bars later, and using it before then is the single most
common way this family of strategies gets coded wrong — it makes the
strategy appear to buy the exact low, because it was told where the low
was.

So a `Pivot` carries two timestamps and they are never the same:

    ts            the bar the pivot is AT     — where to draw it
    confirmed_at  when it became knowable     — when it may be USED

Nothing downstream may read `ts` for a timing decision. `confirmed_at` is
the only field the simulator compares against its clock.

Strictly-greater is used on both sides rather than >=. On a plateau —
two bars sharing the same high — a non-strict test marks both as pivots,
and the strategy then reads two "distinct" structure points at one price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Sequence

import numpy as np
import pandas as pd

from .bars import known_at

PivotKind = Literal["high", "low"]


@dataclass(frozen=True)
class Pivot:
    kind: PivotKind
    #: The bar the pivot sits on. For drawing and for price, never timing.
    ts: pd.Timestamp
    price: float
    #: When the right-hand side completed. The only field timing may read.
    confirmed_at: pd.Timestamp
    timeframe: str


def find_pivots(
    frame: pd.DataFrame, lookback: int, timeframe: str
) -> List[Pivot]:
    """Every confirmed pivot in `frame`, in confirmation order.

    Returned sorted by `confirmed_at`, not by `ts`: that is the order the
    strategy actually learns them in, and sorting by `ts` would let a
    binary search over the list hand back a pivot that had not confirmed.
    """
    if lookback < 1:
        raise ValueError("lookback must be at least 1")

    width = 2 * lookback + 1
    n = len(frame)
    if n < width:
        return []

    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    index = frame.index

    high_win = np.lib.stride_tricks.sliding_window_view(highs, width)
    low_win = np.lib.stride_tricks.sliding_window_view(lows, width)

    centre_high = high_win[:, lookback]
    centre_low = low_win[:, lookback]

    # A NaN anywhere in the window makes every comparison False, so a gap
    # in the data cannot manufacture a pivot. That is the behaviour we
    # want and it is why this is not written with np.nanmax.
    left_high = high_win[:, :lookback].max(axis=1)
    right_high = high_win[:, lookback + 1 :].max(axis=1)
    left_low = low_win[:, :lookback].min(axis=1)
    right_low = low_win[:, lookback + 1 :].min(axis=1)

    is_high = (centre_high > left_high) & (centre_high > right_high)
    is_low = (centre_low < left_low) & (centre_low < right_low)

    pivots: List[Pivot] = []
    for offset in np.flatnonzero(is_high):
        centre = offset + lookback
        pivots.append(
            Pivot(
                kind="high",
                ts=index[centre],
                price=float(highs[centre]),
                confirmed_at=known_at(index[centre + lookback], timeframe),
                timeframe=timeframe,
            )
        )
    for offset in np.flatnonzero(is_low):
        centre = offset + lookback
        pivots.append(
            Pivot(
                kind="low",
                ts=index[centre],
                price=float(lows[centre]),
                confirmed_at=known_at(index[centre + lookback], timeframe),
                timeframe=timeframe,
            )
        )

    # Confirmation order, with `ts` as a tiebreak so a run is reproducible
    # rather than dependent on the order the two loops happened to append.
    pivots.sort(key=lambda p: (p.confirmed_at, p.ts, p.kind))
    return pivots


class PivotSeries:
    """Confirmed pivots, queryable as of an instant.

    The whole list is precomputed — that is not look-ahead, because every
    lookup is gated on `confirmed_at`. Precomputing and then filtering is
    equivalent to streaming, and `test_truncation_invariance` is what
    proves the equivalence rather than assuming it.
    """

    def __init__(self, pivots: Sequence[Pivot]):
        self._pivots = list(pivots)
        self._confirmed = np.array(
            [p.confirmed_at.value for p in self._pivots], dtype="int64"
        )

    def __len__(self) -> int:
        return len(self._pivots)

    def __iter__(self):
        return iter(self._pivots)

    def _cutoff(self, at: pd.Timestamp) -> int:
        """How many pivots had confirmed by `at`, inclusive.

        `side="right"` makes the bound inclusive: a pivot confirmed by the
        bar that closed exactly at `at` is usable on the bar opening at
        `at`. That is the same `<=` the module contract in bars.py states,
        and flipping it to "left" would silently add a one-bar delay to
        every structure read.
        """
        return int(np.searchsorted(self._confirmed, at.value, side="right"))

    def known(self, at: pd.Timestamp) -> List[Pivot]:
        return self._pivots[: self._cutoff(at)]

    def last(self, kind: PivotKind, at: pd.Timestamp) -> Optional[Pivot]:
        """The most recently CONFIRMED pivot of `kind` as of `at`.

        Most recently confirmed, not highest or nearest: market structure
        is a sequence of events, and "the most recent opposing swing" in
        the spec means the last one the market printed and confirmed.
        """
        for pivot in reversed(self._pivots[: self._cutoff(at)]):
            if pivot.kind == kind:
                return pivot
        return None
