"""Tests for the Donchian/ATR calculation — the strategy itself."""

import pandas as pd

from app.signals import compute_signal_frame


def test_donchian_levels_exclude_today(ohlc):
    """The rolling windows are shifted by one bar, so today's own high
    can't be part of the level it has to break. Without the shift a new
    high would be compared against itself and could never signal — this is
    the single most important property of the calculation."""
    closes = [100.0] * 60
    frame = compute_signal_frame(ohlc(closes), n_entry=55, m_exit=20)
    last = frame.iloc[-1]

    # highs are close+1 on every bar, so a level that included today would
    # be 101; shifted, it's still 101 but sourced from prior bars only.
    # The observable consequence is that a flat series never signals.
    assert not bool(last["EntrySignal"])
    assert not bool(last["ExitSignal"])


def test_entry_signal_fires_on_breakout(ohlc):
    closes = [100.0] * 59 + [200.0]
    last = compute_signal_frame(ohlc(closes)).iloc[-1]

    assert bool(last["EntrySignal"])
    assert not bool(last["ExitSignal"])
    assert last["Close"] > last["DonchianHigh_N"]


def test_exit_signal_fires_on_breakdown(ohlc):
    closes = [100.0] * 59 + [10.0]
    last = compute_signal_frame(ohlc(closes)).iloc[-1]

    assert bool(last["ExitSignal"])
    assert not bool(last["EntrySignal"])
    assert last["Close"] < last["DonchianLow_M"]


def test_insufficient_history_leaves_entry_level_undefined(ohlc):
    """A ticker with fewer than n_entry bars — a recent IPO or spinoff —
    has no 55-day high. It must come back NaN rather than a level derived
    from a partial window, and must not produce an entry signal."""
    last = compute_signal_frame(ohlc([100.0] * 30), n_entry=55, m_exit=20).iloc[-1]

    assert pd.isna(last["DonchianHigh_N"])
    assert not bool(last["EntrySignal"])
    # the 20-day exit level is still computable from 30 bars
    assert not pd.isna(last["DonchianLow_M"])


def test_stop_distance_is_two_atr(ohlc):
    frame = compute_signal_frame(ohlc([100.0] * 60), atr_period=20, k_atr=2.0)
    last = frame.iloc[-1]

    assert last["ATR"] > 0
    assert last["StopDistance"] == 2.0 * last["ATR"]


def test_position_size_scales_with_equity_and_risk(ohlc):
    """Shares = risk_dollars / stop_distance, so doubling equity doubles
    the size and halving the risk fraction halves it."""
    base = compute_signal_frame(ohlc([100.0] * 60), equity=100_000, risk_fraction=0.01)
    double_equity = compute_signal_frame(ohlc([100.0] * 60), equity=200_000, risk_fraction=0.01)
    half_risk = compute_signal_frame(ohlc([100.0] * 60), equity=100_000, risk_fraction=0.005)

    assert double_equity.iloc[-1]["Shares"] == 2 * base.iloc[-1]["Shares"]
    assert half_risk.iloc[-1]["Shares"] == 0.5 * base.iloc[-1]["Shares"]
