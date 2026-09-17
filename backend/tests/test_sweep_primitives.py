"""The five detection components, one behaviour at a time.

Every test here names the specific way this family of strategies is
usually coded wrong, because that is what each one exists to prevent —
see the list at the end of the spec in research/README.md.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from research.sweep.bars import (
    ET, build_timeframes, known_at, resample_ohlc, session_bounds, session_of,
    trading_day, normalize_bars,
)
from research.sweep.fvg import find_entry_gap
from research.sweep.levels import build_levels
from research.sweep.mss import body_fraction, find_structure_shift
from research.sweep.params import SweepParams
from research.sweep.sweep import find_sweeps
from research.sweep.swings import PivotSeries, find_pivots
from research.sweep.testing import bars, walk


def et(stamp: str) -> pd.Timestamp:
    return pd.Timestamp(stamp, tz=ET).tz_convert("UTC")


# --- sessions and the trading day -----------------------------------------

def test_the_trading_day_starts_in_the_evening_not_at_midnight():
    # 21:00 ET Monday belongs to TUESDAY's session, because the CME day
    # opened at 18:00 that evening. Getting this wrong makes "previous day
    # high" the high of a day that had not finished.
    assert trading_day(et("2026-03-02 17:59")) == dt.date(2026, 3, 2)
    assert trading_day(et("2026-03-02 18:00")) == dt.date(2026, 3, 3)
    assert trading_day(et("2026-03-02 23:30")) == dt.date(2026, 3, 3)
    assert trading_day(et("2026-03-03 10:00")) == dt.date(2026, 3, 3)


def test_asia_wraps_midnight_and_still_belongs_to_the_next_days_session():
    start, end = session_bounds(dt.date(2026, 3, 3), "asia")
    assert start.tz_convert(ET).strftime("%Y-%m-%d %H:%M") == "2026-03-02 20:00"
    assert end.tz_convert(ET).strftime("%Y-%m-%d %H:%M") == "2026-03-03 00:00"
    assert session_of(et("2026-03-02 23:30")) == "asia"
    assert session_of(et("2026-03-03 03:00")) == "london"
    assert session_of(et("2026-03-03 10:00")) == "newyork"
    # 01:00 ET is between Asia's close and London's open: no session.
    assert session_of(et("2026-03-03 01:00")) is None


def test_the_day_boundary_holds_across_a_dst_change():
    # US clocks move on 2026-03-08. An 18:00 boundary computed by UTC
    # arithmetic would drift an hour here and relabel a session.
    before = session_bounds(dt.date(2026, 3, 6), "london")[0].tz_convert(ET)
    after = session_bounds(dt.date(2026, 3, 10), "london")[0].tz_convert(ET)
    assert before.strftime("%H:%M") == after.strftime("%H:%M") == "02:00"


def test_a_session_level_is_built_only_from_bars_inside_a_closed_window():
    # One bar inside Asia with a distinctive high, one inside New York
    # that is higher. The Asia level must be the FIRST, and it must become
    # knowable at Asia's close, not at the bar that printed it.
    frame = bars(
        [(100, 101, 99, 100)] * 3,
        start="2026-03-02 20:00",
        freq="1h",
    )
    frame = pd.concat([
        frame,
        bars([(100, 120, 99, 100)], start="2026-03-03 10:00", freq="1h"),
    ])
    frame.index = pd.DatetimeIndex([et(str(t)[:16]) for t in frame.index.tz_localize(None)])
    params = SweepParams(execution_timeframe="1h", level_types=("asia_high",), tick_size=0.01)
    book = build_levels(normalize_bars(frame), [], params)
    asia = [l for l in book if l.kind == "asia_high"]
    assert len(asia) == 1
    assert asia[0].price == 101.0          # not 120: that bar is New York
    assert asia[0].known_at == et("2026-03-03 00:00")


# --- swing pivots ----------------------------------------------------------

def test_a_pivot_is_not_knowable_until_its_right_side_completes():
    frame = bars([(h, h, h - 1, h) for h in [1, 2, 3, 9, 3, 2, 1, 2, 3]], freq="1h")
    pivots = find_pivots(frame, lookback=3, timeframe="1h")
    high = next(p for p in pivots if p.kind == "high")
    assert high.price == 9.0
    # The pivot sits on bar 3; it confirms only once bar 6 has closed.
    assert high.ts == frame.index[3]
    assert high.confirmed_at == known_at(frame.index[6], "1h")

    series = PivotSeries(pivots)
    assert series.last("high", high.confirmed_at - pd.Timedelta(minutes=1)) is None
    assert series.last("high", high.confirmed_at) is high


def test_a_plateau_does_not_produce_two_pivots_at_one_price():
    frame = bars([(h, h, h - 1, h) for h in [1, 2, 9, 9, 2, 1, 2, 3, 4]], freq="1h")
    assert [p for p in find_pivots(frame, 2, "1h") if p.kind == "high"] == []


# --- higher timeframes -----------------------------------------------------

def test_a_higher_timeframe_bar_is_not_readable_before_it_closes():
    frame = walk(24 * 12, seed=1)          # one day of 5-minute bars
    built = build_timeframes(frame, "5min", ("1h", "4h"))
    four_hour = built.get("4h")
    first = four_hour.index[0]
    assert known_at(first, "4h") == first + pd.Timedelta(hours=4)


def test_a_higher_timeframe_cannot_be_finer_than_the_feed():
    with pytest.raises(ValueError, match="finer than the feed"):
        build_timeframes(walk(50), "1h", ("5min",))


def test_resampling_anchors_on_the_session_open_not_the_epoch():
    frame = walk(24 * 12 * 3, seed=2, start="2026-03-02 22:00")
    four_hour = resample_ohlc(normalize_bars(frame), "4h", day_boundary_hour=18)
    hours = {t.tz_convert(ET).hour for t in four_hour.index}
    assert hours <= {18, 22, 2, 6, 10, 14}


# --- the sweep ------------------------------------------------------------

def _level_frame(rows):
    """A previous-day level of 110, then `rows` on the next session."""
    day_one = bars([(100, 110, 90, 100)] * 4, start="2026-03-02 18:00", freq="1h")
    day_two = bars(rows, start="2026-03-03 18:00", freq="1h")
    frame = pd.concat([day_one, day_two])
    return normalize_bars(frame)


def test_a_close_back_inside_is_a_sweep_but_a_close_that_holds_is_a_breakout():
    params = SweepParams(execution_timeframe="1h", level_types=("prev_day_high",), tick_size=0.01)

    swept = _level_frame([(105, 115, 104, 108)])     # through 110, closed under
    book = build_levels(swept, [], params)
    sweeps = find_sweeps(swept, book, params)
    assert [s.direction for s in sweeps] == ["bearish"]
    assert sweeps[0].extreme == 115.0
    assert sweeps[0].known_at == known_at(swept.index[-1], "1h")

    held = _level_frame([(105, 115, 104, 114)])      # through 110, closed above
    assert find_sweeps(held, build_levels(held, [], params), params) == []


def test_a_poke_smaller_than_the_penetration_is_not_a_sweep():
    frame = _level_frame([(105, 110.005, 104, 108)])
    params = SweepParams(
        execution_timeframe="1h", level_types=("prev_day_high",),
        tick_size=0.01, sweep_penetration_ticks=1.0,
    )
    assert find_sweeps(frame, build_levels(frame, [], params), params) == []


# --- the structure shift ---------------------------------------------------

def test_a_drifting_break_is_not_a_structure_shift():
    # body_fraction is what separates displacement from a drift.
    assert body_fraction(0, 20, 0, 20) == 1.0            # body is the whole range
    assert body_fraction(10, 20, 0, 20) == 0.5           # half of it is upper wick
    assert body_fraction(10, 20, 0, 11) == pytest.approx(0.05)
    # A zero-range bar is undefined, not maximally decisive.
    assert body_fraction(10, 10, 10, 10) == 0.0


def test_the_shift_must_break_a_pivot_confirmed_before_the_breaking_bar():
    frame = walk(400, seed=3)
    params = SweepParams(execution_timeframe="5min", min_body_frac=0.0, sweep_window=50)
    pivots = PivotSeries(find_pivots(frame, params.pivot_lookback, "5min"))
    book = build_levels(frame, [], params.replaced(level_types=("prev_day_high", "prev_day_low")))
    for sweep in find_sweeps(frame, book, params):
        shift = find_structure_shift(frame, sweep, pivots, params)
        if shift is None:
            continue
        # The broken pivot must have confirmed no later than the bar that
        # broke it opened — never on the breaking bar itself.
        assert shift.broken.confirmed_at <= shift.bar_ts
        assert shift.bar_index > sweep.bar_index


# --- the fair value gap ----------------------------------------------------

def test_a_gap_is_detected_at_candle_three_not_candle_two():
    # Candle 3 is bar 4 (low 106); candle 1 is bar 2 (high 104). They do
    # not overlap, so the gap is 104..106.
    frame = bars([
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 104, 99, 103),
        (103, 108, 102, 107),
        (107, 110, 106, 109),
    ], freq="5min")
    gap = find_entry_gap(frame, "bullish", sweep_index=0, shift_index=3, params=SweepParams(min_fvg_ticks=0))
    assert gap is not None
    assert gap.bottom == 104.0 and gap.top == 106.0
    assert gap.bar_index == 4                       # candle 3, not candle 2
    assert gap.known_at == known_at(frame.index[4], "5min")


def test_touching_wicks_leave_no_gap():
    frame = bars([
        (100, 101, 99, 100),
        (100, 104, 99, 103),
        (103, 108, 101, 107),      # low == previous-previous high
    ], freq="5min")
    assert find_entry_gap(frame, "bullish", 0, 1, SweepParams(min_fvg_ticks=0)) is None


def test_a_gap_below_the_minimum_size_is_rejected():
    frame = bars([
        (100, 101, 99, 100),
        (100, 104, 99, 103),
        (103, 108, 101.02, 107),
    ], freq="5min")
    tight = SweepParams(min_fvg_ticks=10.0, tick_size=0.01)   # needs 0.10
    assert find_entry_gap(frame, "bullish", 0, 1, tight) is None
    loose = SweepParams(min_fvg_ticks=1.0, tick_size=0.01)    # needs 0.01
    assert find_entry_gap(frame, "bullish", 0, 1, loose) is not None
