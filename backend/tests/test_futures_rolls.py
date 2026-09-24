"""Roll detection and Panama back-adjustment.

These run without databento installed — `fetch_futures_data` imports it
only inside the functions that talk to the network, so the whole test
suite still runs on the system interpreter the rest of this repo uses.

The reason this file exists: a mis-measured roll does not raise. It
produces a continuous series with a step in it, and the sweep strategy
reads that step as price trading decisively through a level. Every roll
would manufacture exactly the event the strategy is built to detect.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.sweep.fetch_futures_data import (
    OUTPUT_HEADER, detect_rolls, panama_adjust, to_output_frame,
)


def series(rows, start="2024-01-02 00:00", freq="1min"):
    """rows = (instrument_id, open, high, low, close)."""
    index = pd.date_range(start=start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "instrument_id": [r[0] for r in rows],
            "open": [float(r[1]) for r in rows],
            "high": [float(r[2]) for r in rows],
            "low": [float(r[3]) for r in rows],
            "close": [float(r[4]) for r in rows],
            "volume": [100] * len(rows),
        },
        index=index,
    )


# Front rolls from contract 1 to contract 2 at bar 3. The old contract's
# last close is 100; the new contract is trading 40 higher.
FRONT = series([
    (1, 99, 101, 98, 100),
    (1, 100, 102, 99, 101),
    (1, 101, 102, 99, 100),      # last bar of contract 1
    (2, 141, 143, 139, 140),     # first bar of contract 2
    (2, 140, 142, 139, 141),
])
SECOND = series([
    (2, 139, 141, 138, 140),
    (2, 140, 142, 139, 141),
    (2, 141, 142, 139, 140),     # contract 2's close at the SAME timestamp
    (3, 180, 182, 179, 181),
    (3, 181, 183, 180, 182),
])


def test_the_gap_is_the_two_contracts_closes_at_one_instant():
    rolls = detect_rolls(FRONT, SECOND, {1: "NQH4", 2: "NQM4"})
    assert len(rolls) == 1
    roll = rolls[0]
    assert roll.method == "v1_close_minus_v0_close"
    # 140 (contract 2 at T) - 100 (contract 1 at T), not 141 - 100 which
    # would span a minute of real price movement.
    assert roll.gap == pytest.approx(40.0)
    assert roll.old_symbol == "NQH4" and roll.new_symbol == "NQM4"
    assert roll.flagged == ""


def test_a_missing_second_series_bar_falls_back_and_says_so():
    thin = SECOND.drop(SECOND.index[2])
    roll = detect_rolls(FRONT, thin, {})[0]
    assert roll.method == "new_open_minus_old_close"
    assert roll.gap == pytest.approx(41.0)          # open 141 - close 100
    assert "no_v1_bar" in roll.flagged


def test_a_second_series_holding_the_wrong_contract_falls_back():
    # .v.1 has already moved on to contract 3, so its close is not the
    # close of the contract the front series is rolling into. Using it
    # would price the gap against a contract that is not involved.
    wrong = SECOND.copy()
    wrong.iloc[2, wrong.columns.get_loc("instrument_id")] = 3
    roll = detect_rolls(FRONT, wrong, {})[0]
    assert roll.method == "new_open_minus_old_close"
    assert "v1_held_3_not_2" in roll.flagged


def test_adjustment_lands_before_the_roll_and_never_on_it():
    rolls = detect_rolls(FRONT, SECOND, {})
    adjusted = panama_adjust(FRONT, rolls)
    # Bars 0-2 are the old contract and get the +40 shift.
    assert list(adjusted["adjustment"][:3]) == [40.0, 40.0, 40.0]
    # The roll bar is already on the new contract's scale.
    assert list(adjusted["adjustment"][3:]) == [0.0, 0.0]
    # The most recent segment is untouched, so the right-hand edge of the
    # series is still the price that actually traded.
    assert adjusted["close"].iloc[-1] == FRONT["close"].iloc[-1]


def test_adjustment_removes_the_step_at_the_boundary():
    adjusted = panama_adjust(FRONT, detect_rolls(FRONT, SECOND, {}))
    # Before: a 40-point jump between bar 2 and bar 3 that no trade made.
    assert FRONT["close"].iloc[3] - FRONT["close"].iloc[2] == pytest.approx(40.0)
    # After: the step is the real one-minute move contract 2 actually had
    # (140 -> its own prior close of 140 at T), i.e. nothing.
    assert adjusted["close"].iloc[3] - adjusted["close"].iloc[2] == pytest.approx(0.0)


def test_several_rolls_accumulate_backwards():
    front = series([
        (1, 99, 101, 98, 100),
        (1, 100, 102, 99, 100),     # roll 1 here
        (2, 150, 152, 149, 150),
        (2, 150, 152, 149, 150),    # roll 2 here
        (3, 210, 212, 209, 210),
    ])
    second = series([
        (2, 149, 151, 148, 150),
        (2, 150, 152, 149, 150),    # contract 2 at T1 -> gap +50
        (3, 209, 211, 208, 210),
        (3, 210, 212, 209, 210),    # contract 3 at T2 -> gap +60
        (4, 260, 262, 259, 260),
    ])
    rolls = detect_rolls(front, second, {})
    assert [r.gap for r in rolls] == [pytest.approx(50.0), pytest.approx(60.0)]
    adjusted = panama_adjust(front, rolls)
    # The oldest segment carries BOTH later gaps; the middle one carries
    # only the later of the two. Getting this cumulative direction
    # backwards is the classic Panama bug and is invisible in any average.
    assert list(adjusted["adjustment"]) == [110.0, 110.0, 60.0, 60.0, 0.0]


def test_a_series_that_never_rolls_is_returned_unchanged():
    quiet = series([(1, 99, 101, 98, 100)] * 4)
    rolls = detect_rolls(quiet, quiet, {})
    assert rolls == []
    adjusted = panama_adjust(quiet, rolls)
    pd.testing.assert_series_equal(adjusted["close"], quiet["close"])


def test_ohlc_shift_together_so_bar_shape_survives():
    adjusted = panama_adjust(FRONT, detect_rolls(FRONT, SECOND, {}))
    for i in range(len(FRONT)):
        raw, adj = FRONT.iloc[i], adjusted.iloc[i]
        assert adj["high"] - adj["low"] == pytest.approx(raw["high"] - raw["low"])
        assert adj["close"] - adj["open"] == pytest.approx(raw["close"] - raw["open"])
    # Volume is a count, not a price, and must not be shifted.
    pd.testing.assert_series_equal(adjusted["volume"], FRONT["volume"])


# --- output format --------------------------------------------------------

def test_timestamps_carry_a_new_york_offset_in_iso_8601():
    adjusted = panama_adjust(FRONT, detect_rolls(FRONT, SECOND, {}))
    out = to_output_frame(adjusted)
    assert list(out.columns) == OUTPUT_HEADER.split(",")
    first = out["timestamp"].iloc[0]
    # 2024-01-02 00:00 UTC is 19:00 the previous evening in New York.
    assert first == "2024-01-01T19:00:00-05:00"
    assert first[-3] == ":", "offset must be -05:00, not -0500"


def test_the_november_dst_hour_stays_unambiguous():
    """1:00-2:00 repeats in New York. The offset is what separates them.

    Without it the file would contain two different bars both labelled
    01:30:00, and every consumer that indexes on the timestamp would
    silently drop one of them.
    """
    index = pd.date_range("2024-11-03 05:00", periods=4, freq="30min", tz="UTC")
    frame = pd.DataFrame(
        {"instrument_id": 1, "open": 1.0, "high": 1.0, "low": 1.0,
         "close": 1.0, "volume": 1, "adjustment": 0.0},
        index=index,
    )
    stamps = to_output_frame(frame)["timestamp"].tolist()
    local = [s[:19] for s in stamps]
    assert local.count("2024-11-03T01:00:00") == 2      # the hour really does repeat
    assert len(set(stamps)) == 4                        # but the offsets separate them
    assert "2024-11-03T01:00:00-04:00" in stamps        # EDT
    assert "2024-11-03T01:00:00-05:00" in stamps        # EST
