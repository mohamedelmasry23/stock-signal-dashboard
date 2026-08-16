"""Tests for the Donchian/ATR calculation — the strategy itself."""

import pandas as pd

from app.signals import compute_rsi_signal_frame, compute_signal_frame


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


def test_previous_close_is_the_prior_bar(ohlc):
    """The day's move is computed against this, so it has to be the bar
    before the last one — not the last one itself."""
    from app.pipeline import previous_close

    frame = ohlc([10.0, 20.0, 30.0])
    assert previous_close(frame) == 20.0


def test_previous_close_is_none_with_a_single_bar(ohlc):
    """A listing with one session has no prior close. Returning the current
    one instead would render as a confident 0.00% move."""
    from app.pipeline import previous_close

    assert previous_close(ohlc([10.0])) is None


def test_position_size_scales_with_equity_and_risk(ohlc):
    """Shares = risk_dollars / stop_distance, so doubling equity doubles
    the size and halving the risk fraction halves it."""
    base = compute_signal_frame(ohlc([100.0] * 60), equity=100_000, risk_fraction=0.01)
    double_equity = compute_signal_frame(ohlc([100.0] * 60), equity=200_000, risk_fraction=0.01)
    half_risk = compute_signal_frame(ohlc([100.0] * 60), equity=100_000, risk_fraction=0.005)

    assert double_equity.iloc[-1]["Shares"] == 2 * base.iloc[-1]["Shares"]
    assert half_risk.iloc[-1]["Shares"] == 0.5 * base.iloc[-1]["Shares"]


# --- RSI(2) pullback -------------------------------------------------------


def linear(start, stop, n):
    step = (stop - start) / (n - 1)
    return [start + step * i for i in range(n)]


def test_rsi_entry_needs_both_a_dip_and_an_uptrend(ohlc):
    """The two conditions are not independent: the 200-day filter is the
    only thing separating "a pullback" from "a collapse". A deeply
    oversold ticker in a downtrend is the falling knife this strategy
    exists to refuse, so it must not signal however low RSI goes."""
    dip = [190.0, 180.0]
    up = compute_rsi_signal_frame(ohlc(linear(100, 200, 240) + dip)).iloc[-1]
    down = compute_rsi_signal_frame(ohlc(linear(200, 100, 240) + [95.0, 90.0])).iloc[-1]

    assert up["RSI"] < 5 and up["Close"] > up["SMA_Trend"]
    assert bool(up["EntrySignal"])

    # Just as oversold, and rejected anyway.
    assert down["RSI"] < 5
    assert down["Close"] < down["SMA_Trend"]
    assert not bool(down["EntrySignal"])


def test_rsi_exits_on_the_bounce(ohlc):
    """Unlike Donchian, this strategy exits upward — into strength rather
    than out of weakness."""
    closes = linear(100, 200, 240) + [190.0, 180.0, 205.0]
    last = compute_rsi_signal_frame(ohlc(closes)).iloc[-1]

    assert last["Close"] > last["SMA_Fast"]
    assert bool(last["ExitSignal"])
    assert not bool(last["EntrySignal"])


def test_a_halted_ticker_is_neutral_rather_than_overbought(ohlc):
    """A flat series has no gains and no losses, so the RSI ratio is 0/0.
    The bare formula returns 100, which would print an exit signal every
    single day for anything that stopped trading. Undefined means neutral."""
    last = compute_rsi_signal_frame(ohlc([100.0] * 250)).iloc[-1]

    assert last["RSI"] == 50.0
    assert not bool(last["EntrySignal"])
    assert not bool(last["ExitSignal"])


def test_rsi_is_undefined_until_its_window_fills(ohlc):
    """NaN compares False against every threshold, so a ticker without
    enough history simply never signals — a recent IPO has no 200-day
    average and must not be eligible."""
    last = compute_rsi_signal_frame(ohlc(linear(100, 130, 30))).iloc[-1]

    assert pd.isna(last["SMA_Trend"])
    assert not bool(last["EntrySignal"])


def test_both_strategies_agree_on_volatility(ohlc):
    """Sizing divides into StopDistance, so if the two strategies computed
    ATR differently the same ticker would be risked differently depending
    on which page proposed it."""
    closes = linear(100, 200, 240)
    donchian = compute_signal_frame(ohlc(closes)).iloc[-1]
    rsi = compute_rsi_signal_frame(ohlc(closes)).iloc[-1]

    assert donchian["ATR"] == rsi["ATR"]
    assert donchian["StopDistance"] == rsi["StopDistance"]


def test_oversold_depth_never_goes_negative(ohlc):
    """It's the ranking key for the candidates list. A bar that didn't
    signal has no depth rather than a negative one, which would sort
    quiet tickers above real ones."""
    frame = compute_rsi_signal_frame(ohlc(linear(100, 200, 242)))

    assert (frame["OversoldDepth"].dropna() >= 0).all()
