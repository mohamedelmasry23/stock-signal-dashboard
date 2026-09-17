"""Component 1: the levels stops rest behind.

The premise is that resting stop orders cluster just past levels that are
obvious to everybody, so the levels this module builds are deliberately
the plainest ones on the chart: yesterday's high and low, the Asia and
London session extremes, and confirmed swing highs and lows. A level only
a backtest could find is not a stop magnet, and would be measuring
something else entirely.

Three properties decide whether a level is tradeable at an instant:

    known_at      when it finished forming. Session levels are computed
                  ONLY from bars inside a window that has already closed;
                  a bar may never contribute to a level its own trade is
                  judged against.
    mitigated_at  when price first traded through it. A level whose stops
                  have already been taken is not a pool any more.
    expires_at    how long it stays "obvious". Unbounded memory is a
                  different claim from the one being tested.

The mitigation scan is bounded by expiry rather than running to the end of
the series. That is an optimisation, but it is also the honest scope: a
level that expired before it was touched was never going to be traded, so
when it was eventually touched is not a fact this strategy uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence

import numpy as np
import pandas as pd

from .bars import ET, session_bounds, trading_day, trading_day_start
from .params import SweepParams
from .swings import Pivot

#: 'high' levels sit above price and are swept upward — fading one is a
#: SHORT. 'low' levels are the mirror. Side, not direction, because the
#: direction of the resulting trade is the opposite of the sweep.
LevelSide = Literal["high", "low"]


@dataclass(frozen=True)
class LiquidityLevel:
    kind: str
    side: LevelSide
    price: float
    #: When this level finished forming and could first be traded against.
    known_at: pd.Timestamp
    #: The bar (or session end) it came from — for reporting, never timing.
    source_ts: pd.Timestamp
    expires_at: pd.Timestamp
    #: Close time of the first bar to trade through it, if any.
    mitigated_at: Optional[pd.Timestamp] = None
    #: OPEN of that same bar. Kept rather than derived by subtracting a
    #: bar length, because that subtraction is wrong across a weekend gap
    #: and `sweep.py` uses this to look the bar up.
    mitigated_bar_ts: Optional[pd.Timestamp] = None

    def available_at(self, ts: pd.Timestamp) -> bool:
        """Is this level live for a bar OPENING at `ts`?

        `mitigated_at > ts` rather than `>=` is load-bearing: the bar that
        sweeps a level is also the bar that mitigates it, and its
        mitigation stamp is its own close. A `>=` here would make every
        level unavailable on precisely the bar that sweeps it, and the
        strategy would find no setups at all — a silent zero rather than
        an error.
        """
        if self.known_at > ts:
            return False
        if self.expires_at <= ts:
            return False
        return self.mitigated_at is None or self.mitigated_at > ts


def _day_groups(frame: pd.DataFrame, boundary: int) -> Dict:
    days = np.array([trading_day(ts, boundary) for ts in frame.index])
    order: List = []
    seen = set()
    for day in days:
        if day not in seen:
            seen.add(day)
            order.append(day)
    return {"days": days, "order": order}


def _session_levels(
    frame: pd.DataFrame, params: SweepParams, groups: Dict
) -> List[LiquidityLevel]:
    """Asia and London extremes, from bars strictly inside a closed window.

    The window is looked up by clock time and then the bars are SELECTED
    from it — rather than the bars being grouped and the group named
    afterwards — so a day on which the session produced no bars produces
    no level, instead of a level built from whatever traded nearest to it.
    """
    out: List[LiquidityLevel] = []
    index = frame.index
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    max_age = pd.Timedelta(days=params.level_max_age_days)

    for day in groups["order"]:
        for session in ("asia", "london"):
            if not any(
                f"{session}_{s}" in params.level_types for s in ("high", "low")
            ):
                continue
            start, end = session_bounds(day, session, params.day_boundary_hour)
            lo, hi = np.searchsorted(index.values, [start.to_datetime64(), end.to_datetime64()])
            if hi <= lo:
                continue
            window_high = float(np.nanmax(highs[lo:hi]))
            window_low = float(np.nanmin(lows[lo:hi]))
            if not (np.isfinite(window_high) and np.isfinite(window_low)):
                continue
            # known_at is the session's END, never the bar the extreme
            # printed on: until the session closes, that extreme can still
            # be exceeded by a later bar in the same session.
            for side, price in (("high", window_high), ("low", window_low)):
                kind = f"{session}_{side}"
                if kind not in params.level_types:
                    continue
                out.append(
                    LiquidityLevel(
                        kind=kind,
                        side=side,
                        price=price,
                        known_at=end,
                        source_ts=index[lo],
                        expires_at=end + max_age,
                    )
                )
    return out


def _previous_day_levels(
    frame: pd.DataFrame, params: SweepParams, groups: Dict
) -> List[LiquidityLevel]:
    """Yesterday's high and low, knowable the moment today opens."""
    if not any(k.startswith("prev_day") for k in params.level_types):
        return []

    days, order = groups["days"], groups["order"]
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    max_age = pd.Timedelta(days=params.level_max_age_days)

    stats = {}
    for day in order:
        mask = days == day
        stats[day] = (float(np.nanmax(highs[mask])), float(np.nanmin(lows[mask])))

    out: List[LiquidityLevel] = []
    for previous, current in zip(order, order[1:]):
        prev_high, prev_low = stats[previous]
        # The instant today's session opens is the instant yesterday's
        # range is final — which is exactly why the trading day boundary
        # is a parameter and not midnight.
        opens = trading_day_start(current, params.day_boundary_hour)
        for side, price in (("high", prev_high), ("low", prev_low)):
            kind = f"prev_day_{side}"
            if kind not in params.level_types or not np.isfinite(price):
                continue
            out.append(
                LiquidityLevel(
                    kind=kind,
                    side=side,
                    price=price,
                    known_at=opens,
                    source_ts=trading_day_start(previous, params.day_boundary_hour),
                    expires_at=opens + max_age,
                )
            )
    return out


def _swing_levels(pivots: Sequence[Pivot], params: SweepParams) -> List[LiquidityLevel]:
    max_age = pd.Timedelta(days=params.level_max_age_days)
    out: List[LiquidityLevel] = []
    for pivot in pivots:
        kind = f"swing_{pivot.kind}"
        if kind not in params.level_types:
            continue
        out.append(
            LiquidityLevel(
                kind=kind,
                side=pivot.kind,
                price=pivot.price,
                # The confirmation lag carries straight through: a swing
                # level is not a level until its pivot is a pivot.
                known_at=pivot.confirmed_at,
                source_ts=pivot.ts,
                expires_at=pivot.confirmed_at + max_age,
            )
        )
    return out


def mark_mitigation(
    levels: Sequence[LiquidityLevel], frame: pd.DataFrame
) -> List[LiquidityLevel]:
    """Stamp each level with the close of the first bar that traded through.

    Bounded by the level's own expiry — see the module docstring. The scan
    starts at the first bar opening at or after `known_at`, so a level can
    never be mitigated by the bars that built it.
    """
    index = frame.index
    if not len(index):
        return list(levels)

    values = index.values
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    # Close time of bar i is the open of bar i+1 for a gapless feed; using
    # the next open would break across a weekend, so the bar's own span is
    # measured from the series' modal step instead.
    step = _modal_step(index)

    out: List[LiquidityLevel] = []
    for level in levels:
        start = int(np.searchsorted(values, level.known_at.to_datetime64(), side="left"))
        stop = int(np.searchsorted(values, level.expires_at.to_datetime64(), side="left"))
        if stop <= start:
            out.append(level)
            continue
        if level.side == "high":
            hit = np.flatnonzero(highs[start:stop] > level.price)
        else:
            hit = np.flatnonzero(lows[start:stop] < level.price)
        if not len(hit):
            out.append(level)
            continue
        bar = index[start + int(hit[0])]
        out.append(
            LiquidityLevel(
                kind=level.kind,
                side=level.side,
                price=level.price,
                known_at=level.known_at,
                source_ts=level.source_ts,
                expires_at=level.expires_at,
                mitigated_at=bar + step,
                mitigated_bar_ts=bar,
            )
        )
    return out


def _modal_step(index: pd.DatetimeIndex) -> pd.Timedelta:
    """The feed's own bar length, taken as the most common gap.

    The mode rather than the minimum or the mean: a 5-minute feed has
    weekend gaps and the odd missing bar, and both of those would drag a
    mean far from the truth while the mode ignores them.
    """
    if len(index) < 2:
        return pd.Timedelta(minutes=5)
    diffs = pd.Series(index[1:].values - index[:-1].values)
    return pd.Timedelta(diffs.mode().iloc[0])


class LevelBook:
    """Every level, queryable as of an instant.

    Held in price order per side so the "next opposing pool" lookup a
    target needs is a scan over sorted prices rather than over time.
    """

    def __init__(self, levels: Sequence[LiquidityLevel]):
        self._levels = sorted(levels, key=lambda l: (l.known_at, l.price, l.kind))
        self._by_side: Dict[LevelSide, List[LiquidityLevel]] = {"high": [], "low": []}
        for level in self._levels:
            self._by_side[level.side].append(level)
        for side in self._by_side:
            self._by_side[side].sort(key=lambda l: l.price)

    def __len__(self) -> int:
        return len(self._levels)

    def __iter__(self):
        return iter(self._levels)

    def available(self, ts: pd.Timestamp, side: Optional[LevelSide] = None):
        pool = self._levels if side is None else self._by_side[side]
        return [level for level in pool if level.available_at(ts)]

    def nearest_above(
        self, price: float, ts: pd.Timestamp, side: LevelSide = "high"
    ) -> Optional[LiquidityLevel]:
        """The next pool of stops above `price` — a long's target."""
        for level in self._by_side[side]:
            if level.price > price and level.available_at(ts):
                return level
        return None

    def nearest_below(
        self, price: float, ts: pd.Timestamp, side: LevelSide = "low"
    ) -> Optional[LiquidityLevel]:
        for level in reversed(self._by_side[side]):
            if level.price < price and level.available_at(ts):
                return level
        return None


def build_levels(
    frame: pd.DataFrame, pivots: Sequence[Pivot], params: SweepParams
) -> LevelBook:
    """Every liquidity level for one instrument, mitigation already stamped."""
    groups = _day_groups(frame, params.day_boundary_hour)
    levels: List[LiquidityLevel] = []
    levels += _previous_day_levels(frame, params, groups)
    levels += _session_levels(frame, params, groups)
    levels += _swing_levels(pivots, params)
    return LevelBook(mark_mitigation(levels, frame))
