"""Bars, timeframes, sessions — and the look-ahead contract.

## The contract: `known_at`

Every bar in this package is labelled by its OPEN time, tz-aware, in UTC.
A bar that opens at `t` on an interval of `D` is not finished until `t + D`,
so nothing derived from it may be used before then. That instant is the
bar's `known_at`, and it is the only thing standing between this harness
and a flattering look-ahead bug.

Every event type in this package (`Pivot`, `LiquidityLevel`, `Sweep`,
`StructureShift`, `FairValueGap`, `Setup`) carries a `known_at`, and the
simulator in `simulate.py` may only read events satisfying

    event.known_at <= execution_bar.open_time

Note the `<=`. An event derived from the bar that just closed at `t` IS
usable on the bar opening at `t` — that is the same one-bar delay the
daily engine gets from its decision/execution split, expressed on a
continuous clock instead of a row index.

This is why higher timeframes are RESAMPLED from the execution series
rather than fetched separately. A 4H bar built here does not exist as a
readable value until its own close, so "used a 4H bar before it closed" —
the second-most-common way this strategy gets coded wrong — is not a bug
that can be written. There is no API through which to ask for it.

## Trading days and sessions

Everything is anchored to New York time, because the levels this strategy
trades are defined in it.

A trading day does NOT start at midnight. It starts at `day_boundary_hour`
ET (default 18:00, the CME convention), so the Asia and London sessions
that precede a New York session belong to the same trading day as that
session. Get this wrong and "previous day high" silently means the high of
a day that had not finished when the level was supposedly known.

    trading day D   18:00 ET on D-1  ->  18:00 ET on D
      asia          20:00 -> 00:00 ET   (spans midnight, still day D)
      london        02:00 -> 05:00 ET
      new york      09:30 -> 16:00 ET

Session windows are half-open, `[start, end)`, so a bar opening exactly at
05:00 ET is London's first non-bar rather than its last bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Dict, Optional
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

OHLC_COLUMNS = ("Open", "High", "Low", "Close", "Volume")

#: Session windows in New York time, half-open [start, end).
#: Asia wraps midnight; `session_of` handles that rather than the caller.
SESSIONS: Dict[str, tuple[time, time]] = {
    "asia": (time(20, 0), time(0, 0)),
    "london": (time(2, 0), time(5, 0)),
    "newyork": (time(9, 30), time(16, 0)),
}

#: Resample rules, as pandas offset aliases keyed by the names used in
#: `params.py`. Kept as a lookup so a typo is a KeyError at configuration
#: time rather than a silently different timeframe at run time.
TIMEFRAMES: Dict[str, pd.Timedelta] = {
    "5min": pd.Timedelta(minutes=5),
    "15min": pd.Timedelta(minutes=15),
    "30min": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
}


def timeframe_delta(name: str) -> pd.Timedelta:
    try:
        return TIMEFRAMES[name]
    except KeyError:
        raise ValueError(
            f"Unknown timeframe {name!r}. Available: {', '.join(TIMEFRAMES)}"
        ) from None


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Force a tz-aware UTC DatetimeIndex, sorted, unique, float OHLCV.

    Naive input is treated as UTC. That is a real assumption and not a
    safe-looking default: a naive index that was actually New York time
    would shift every session window by four or five hours and quietly
    relabel Asia as London. Sources are responsible for localising; this
    function only guarantees the shape.
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError(f"Bars need a DatetimeIndex, got {type(frame.index).__name__}")

    missing = [c for c in ("Open", "High", "Low", "Close") if c not in frame.columns]
    if missing:
        raise ValueError(f"Bars are missing {', '.join(missing)}")

    out = frame.copy()
    index = out.index
    out.index = index.tz_localize(UTC) if index.tz is None else index.tz_convert(UTC)

    if "Volume" not in out.columns:
        out["Volume"] = float("nan")

    out = out[list(OHLC_COLUMNS)].astype(float)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "ts"
    return out


def session_origin(frame: pd.DataFrame, day_boundary_hour: int) -> pd.Timestamp:
    """The anchor that resampling counts from: the last trading-day boundary
    at or before the first bar.

    Resampling without an explicit origin anchors on the epoch, which puts
    4H boundaries at 00:00/04:00/08:00 UTC — no relation to any session a
    trader watches, and off by an hour twice a year when New York changes
    clock and London does not. Anchoring on the session open is what makes
    a "4H bar" the same object the strategy's premise is about.
    """
    first_et = frame.index[0].tz_convert(ET)
    anchor = first_et.replace(hour=day_boundary_hour, minute=0, second=0, microsecond=0)
    if anchor > first_et:
        anchor -= pd.Timedelta(days=1)
    return anchor.tz_convert(UTC)


def resample_ohlc(
    frame: pd.DataFrame, timeframe: str, day_boundary_hour: int = 18
) -> pd.DataFrame:
    """Build a higher timeframe from the execution series.

    `label="left", closed="left"` so the result keeps this package's
    convention: the index is the bar's OPEN. Empty buckets are dropped —
    a 4H window in which nothing traded is not a bar, and carrying it
    forward as a flat candle would manufacture pivots and compress ranges
    exactly the way `_aligned` in the daily engine warns about.
    """
    if not len(frame):
        return frame.copy()

    delta = timeframe_delta(timeframe)
    origin = session_origin(frame, day_boundary_hour)
    out = (
        frame.resample(delta, label="left", closed="left", origin=origin)
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna(subset=["Open", "High", "Low", "Close"])
    )
    out.index.name = "ts"
    return out


def known_at(open_time: pd.Timestamp, timeframe: str) -> pd.Timestamp:
    """When a bar opening at `open_time` becomes readable: its own close."""
    return open_time + timeframe_delta(timeframe)


def trading_day(ts: pd.Timestamp, day_boundary_hour: int = 18) -> date:
    """Which trading day a UTC instant belongs to.

    A bar at 21:00 ET Monday is part of TUESDAY's trading day, because the
    session opened at 18:00 ET Monday. This is the function that makes
    "previous day high" mean what a futures trader means by it.
    """
    et = ts.tz_convert(ET)
    return (et + pd.Timedelta(hours=24 - day_boundary_hour)).date()


def trading_day_start(day: date, day_boundary_hour: int = 18) -> pd.Timestamp:
    """The instant trading day `day` opens, in UTC.

    Built through a tz-aware ET timestamp rather than by arithmetic on UTC,
    so the boundary stays at 18:00 ET across both DST transitions instead
    of drifting an hour twice a year.
    """
    et = pd.Timestamp(
        year=day.year, month=day.month, day=day.day, hour=day_boundary_hour, tz=ET
    ) - pd.Timedelta(days=1)
    return et.tz_convert(UTC)


def session_of(ts: pd.Timestamp) -> Optional[str]:
    """Which named session a UTC instant falls in, or None.

    Asia wraps midnight, so its window is tested as "at or after 20:00 OR
    before 00:00" rather than as an ordinary between.
    """
    local = ts.tz_convert(ET).time()
    for name, (start, end) in SESSIONS.items():
        if start <= end:
            if start <= local < end:
                return name
        elif local >= start or local < end:  # wraps midnight
            return name
    return None


def session_bounds(
    day: date, session: str, day_boundary_hour: int = 18
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The [start, end) instants of `session` within trading day `day`, UTC.

    Anchored to the trading day's own start so a session that runs before
    midnight lands in the right day. Asia on trading day D runs 20:00-00:00
    ET on the calendar day D-1; London and New York run on D itself.
    """
    if session not in SESSIONS:
        raise ValueError(f"Unknown session {session!r}. Available: {', '.join(SESSIONS)}")

    start_t, end_t = SESSIONS[session]
    open_et = trading_day_start(day, day_boundary_hour).tz_convert(ET)

    def at(clock: time, *, after: Optional[pd.Timestamp] = None) -> pd.Timestamp:
        stamp = open_et.replace(
            hour=clock.hour, minute=clock.minute, second=0, microsecond=0
        )
        # Sessions are located by walking FORWARD from the session open, so
        # a clock time earlier in the day than 18:00 lands on the next
        # calendar date -- which is what makes 02:00 London belong to the
        # trading day that opened the evening before.
        floor = after if after is not None else open_et
        while stamp < floor:
            stamp += pd.Timedelta(days=1)
        return stamp

    start = at(start_t)
    end = at(end_t, after=start + pd.Timedelta(seconds=1))
    return start.tz_convert(UTC), end.tz_convert(UTC)


@dataclass(frozen=True)
class Timeframes:
    """One instrument's bars at every timeframe the strategy reads.

    Built once per instrument so the resampling cost is paid once, and so
    every component is provably looking at the same series. `execution` is
    the native feed; everything else is derived from it.
    """

    execution_name: str
    execution: pd.DataFrame
    frames: Dict[str, pd.DataFrame]

    def get(self, timeframe: str) -> pd.DataFrame:
        if timeframe == self.execution_name:
            return self.execution
        try:
            return self.frames[timeframe]
        except KeyError:
            raise ValueError(
                f"Timeframe {timeframe!r} was not built. Built: "
                f"{', '.join([self.execution_name, *self.frames])}"
            ) from None


def build_timeframes(
    frame: pd.DataFrame,
    execution: str,
    higher: tuple[str, ...],
    day_boundary_hour: int = 18,
) -> Timeframes:
    """Resample one instrument up to every timeframe the strategy needs.

    Refuses to build a higher timeframe finer than the execution one: a
    "4H bar" reconstructed from daily bars would be the daily bar wearing a
    different label, and every structure read off it would be a fiction.
    """
    bars = normalize_bars(frame)
    base = timeframe_delta(execution)
    built: Dict[str, pd.DataFrame] = {}
    for name in higher:
        if timeframe_delta(name) < base:
            raise ValueError(
                f"Cannot build {name} from {execution} bars — it is finer than the feed."
            )
        if name == execution:
            continue
        built[name] = resample_ohlc(bars, name, day_boundary_hour)
    return Timeframes(execution_name=execution, execution=bars, frames=built)
