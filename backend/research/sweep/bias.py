"""Component 2: higher-timeframe directional bias — a filter, not a signal.

Market structure on the bias timeframe (4H by default): bullish once a bar
closes above the most recent confirmed swing high, bearish once one closes
below the most recent confirmed swing low. The strategy then only fades
sweeps in that direction.

Two things this module is careful about, both of which would flatter the
results if got wrong:

* **The bar must have closed.** A bias change is stamped `known_at` = the
  bias bar's own close, never its open. Because higher timeframes here are
  resampled from the execution feed, a 4H bar does not exist as a value
  until then anyway — but the stamp is what the simulator compares, so it
  is set explicitly rather than left implicit.

* **The pivot must have confirmed BEFORE the bar that breaks it.** A pivot
  confirmed by the same bar that breaks it includes that bar in its own
  right-hand side, so "price broke structure" would partly mean "price
  broke a level defined using this very bar". Pivots are therefore
  filtered on `confirmed_at <= bar open`, not `<= bar close`.

`use_bias=False` disables the filter entirely and `bias_at` then returns
None everywhere, which `strategy.py` reads as "no opinion, take both
sides". That variant exists because the spec asks for it: a filter that
only cuts trade count is not adding value, and the only way to tell is to
measure the same rules without it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Sequence

import numpy as np
import pandas as pd

from .bars import known_at
from .swings import PivotSeries

Direction = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class BiasChange:
    direction: Direction
    #: The close that flipped it — and when it became readable.
    known_at: pd.Timestamp
    #: The structure point that was broken, for the report.
    broken_price: float
    broken_pivot_ts: pd.Timestamp


def build_bias(
    frame: pd.DataFrame, pivots: PivotSeries, timeframe: str
) -> List[BiasChange]:
    """Every bias flip on `frame`, in order.

    Only flips are recorded. A run of bullish closes after a bullish flip
    says nothing new, and recording each one would make "the bias changed"
    unanswerable.
    """
    changes: List[BiasChange] = []
    state: Optional[Direction] = None

    closes = frame["Close"].to_numpy(dtype=float)
    for i, open_ts in enumerate(frame.index):
        close = closes[i]
        if not np.isfinite(close):
            continue
        closed_at = known_at(open_ts, timeframe)

        high = pivots.last("high", open_ts)
        low = pivots.last("low", open_ts)

        flipped: Optional[BiasChange] = None
        if high is not None and close > high.price and state != "bullish":
            flipped = BiasChange("bullish", closed_at, high.price, high.ts)
        elif low is not None and close < low.price and state != "bearish":
            flipped = BiasChange("bearish", closed_at, low.price, low.ts)

        if flipped is not None:
            changes.append(flipped)
            state = flipped.direction

    return changes


class BiasSeries:
    """The bias as a step function of time.

    Before the first flip the bias is genuinely unknown, and this returns
    None for it rather than guessing a side. `strategy.py` treats None as
    "no setups allowed" when the filter is on — which costs the warm-up
    period's trades, and is the honest reading of a filter that has not
    formed an opinion yet.
    """

    def __init__(self, changes: Sequence[BiasChange], enabled: bool = True):
        self._changes = list(changes)
        self._enabled = enabled
        self._stamps = np.array([c.known_at.value for c in self._changes], dtype="int64")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __len__(self) -> int:
        return len(self._changes)

    def at(self, ts: pd.Timestamp) -> Optional[Direction]:
        if not self._enabled or not self._changes:
            return None
        position = int(np.searchsorted(self._stamps, ts.value, side="right"))
        if position == 0:
            return None
        return self._changes[position - 1].direction

    def allows(self, ts: pd.Timestamp, direction: Direction) -> bool:
        """May a trade in `direction` be taken at `ts`?

        With the filter off, everything is allowed — that is the variant.
        With it on, an unformed bias allows nothing.
        """
        if not self._enabled:
            return True
        current = self.at(ts)
        return current is not None and current == direction
