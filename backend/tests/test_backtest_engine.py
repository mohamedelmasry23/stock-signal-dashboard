"""
Engine tests. All offline — the engine takes a plain dict of frames, so
nothing here touches the network, the cache, or a database.

The first test is the important one. `test_truncation_invariance` catches
any future leak anywhere in the loop, including ones nobody thought to
test for individually; the rest pin down specific behaviours so that when
it fails, the reason is obvious.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import BacktestConfig
from app.backtest.engine import run_backtest
from app.signals import compute_signal_frame

# Short windows so a test series doesn't need 60 bars of warm-up.
FAST = dict(n_entry=5, m_exit=3, atr_period=3)


def cfg(**overrides):
    base = dict(
        start=date(2000, 1, 1),
        end=date(2100, 1, 1),
        initial_equity=100_000.0,
        risk_fraction=0.01,
        cost_bps=0.0,
        slippage_bps=0.0,
        max_position_fraction=1.0,
        min_notional=0.0,
        **FAST,
    )
    base.update(overrides)
    return BacktestConfig(**base)


def flat_then_breakout(n_flat=12, level=100.0, spike=200.0):
    """A series that does nothing, then breaks out once."""
    return [level] * n_flat + [spike]


# --- look-ahead -------------------------------------------------------


def test_truncation_invariance(ohlc):
    """The general look-ahead detector.

    Run over bars [0..N], then over [0..N+3] where the appended bars
    contain a violent spike. If the engine peeks at any future bar, the
    overlapping portion of the equity curve will differ. It must be
    identical element for element.
    """
    closes = flat_then_breakout(12) + [205.0, 210.0]
    # The fixture anchors on `end`, so the longer series needs a later end
    # date for its first len(closes) bars to fall on the same dates. Without
    # that the two runs are simply different data, not a prefix and an
    # extension, and the comparison means nothing.
    short_end = pd.Timestamp("2026-08-11")
    long_end = short_end + pd.tseries.offsets.BDay(3)
    short = ohlc(closes, end=short_end)
    long = ohlc(closes + [2000.0, 2500.0, 3000.0], end=long_end)
    assert short.index[0] == long.index[0]  # the prefix really is aligned

    a = run_backtest({"AAA": short}, cfg())
    b = run_backtest({"AAA": long}, cfg())

    overlap = a.equity.index
    pd.testing.assert_series_equal(a.equity, b.equity.loc[overlap], check_freq=False)


def test_no_position_exists_on_the_signal_bar(ohlc):
    """A breakout is only known after its close, so equity must still be
    untouched at that close."""
    closes = flat_then_breakout(12)
    frame = ohlc(closes)
    signals = compute_signal_frame(frame, **FAST)
    signal_bar = signals.index[signals["EntrySignal"]][0]

    result = run_backtest({"AAA": frame}, cfg())

    assert result.equity.loc[signal_bar] == pytest.approx(100_000.0)


def test_entry_fills_at_next_open_not_the_signal_close(ohlc):
    """The signal bar closes at 200; the next bar OPENS at 150. Filling at
    the signal close would record 200 and a much smaller share count."""
    closes = flat_then_breakout(12) + [160.0]
    opens = [100.0] * 12 + [100.0, 150.0]
    frame = ohlc(closes, opens=opens)

    result = run_backtest({"AAA": frame}, cfg())

    assert len(result.trades) == 1
    assert result.trades[0].entry_price == pytest.approx(150.0)


def test_sizing_uses_the_decision_bars_stop_distance(ohlc):
    """The execution bar is a huge-range outlier, so its ATR is far larger
    than the decision bar's. Sizing must use the decision bar's."""
    closes = flat_then_breakout(12) + [160.0]
    # Execution bar (index 13) gets a 600-wide range vs 2 elsewhere, so its
    # ATR is unmistakably different from the decision bar's.
    highs = [c + 1 for c in closes[:13]] + [closes[13] + 300]
    lows = [c - 1 for c in closes[:13]] + [closes[13] - 300]
    frame = ohlc(closes, highs=highs, lows=lows)

    signals = compute_signal_frame(frame, **FAST)
    decision_stop = signals["StopDistance"].iloc[12]
    execution_stop = signals["StopDistance"].iloc[13]
    assert execution_stop > decision_stop * 2  # the trap is real

    result = run_backtest({"AAA": frame}, cfg())

    assert result.trades[0].stop_distance == pytest.approx(decision_stop)
    assert result.trades[0].shares == pytest.approx(0.01 * 100_000 / decision_stop)


def test_sizing_uses_prior_close_equity_not_the_gap_up(ohlc):
    """Two tickers: one already held and gapping up hard on the execution
    bar, one entering. The entry must be sized off the equity marked at the
    previous close, before that gap was visible."""
    held = ohlc([100.0] * 12 + [101.0, 400.0])
    entering = ohlc(flat_then_breakout(12) + [160.0])

    result = run_backtest({"AAA": held, "BBB": entering}, cfg())

    entry = next(t for t in result.trades if t.ticker == "BBB")
    # Equity at the decision bar was still ~100k; a gap-aware engine would
    # have sized off a much larger number.
    assert entry.risk_dollars == pytest.approx(0.01 * 100_000, rel=0.05)


# --- off-by-one -------------------------------------------------------


def test_donchian_levels_are_not_shifted_twice(ohlc):
    """The entry's decision bar must equal the first bar where
    compute_signal_frame reports EntrySignal. An extra .shift(1) inside the
    engine moves it by one and fails here."""
    frame = ohlc(flat_then_breakout(12) + [160.0])
    signals = compute_signal_frame(frame, **FAST)
    expected_decision_bar = signals.index[signals["EntrySignal"]][0]

    result = run_backtest({"AAA": frame}, cfg())
    entry_bar = pd.Timestamp(result.trades[0].entry_date)

    calendar = frame.index
    decision_bar = calendar[calendar.get_loc(entry_bar) - 1]
    assert decision_bar == expected_decision_bar


def test_ranking_matches_the_dashboard_formula(ohlc):
    """Capital for one position, three simultaneous breakouts. The winner
    must be argmax((close - donchian_high) / atr) — the same expression
    /api/entry-candidates ranks by."""
    weak = ohlc([100.0] * 12 + [101.0, 101.0])
    strong = ohlc([100.0] * 12 + [180.0, 180.0])
    middle = ohlc([100.0] * 12 + [120.0, 120.0])

    result = run_backtest(
        {"WEAK": weak, "STRONG": strong, "MIDDLE": middle},
        cfg(max_positions=1),
    )

    assert [t.ticker for t in result.trades] == ["STRONG"]


# --- ordering and capital --------------------------------------------


def test_cash_never_goes_negative(ohlc):
    """The live app sizes without any cash cap and routinely suggests
    positions larger than the whole account. The backtest must not inherit
    that: this is the regression test for it."""
    # Tiny pad => tiny ATR => tiny stop distance => risk-based sizing asks
    # for an enormous share count. That is exactly the situation where the
    # live app's uncapped formula suggests more than the whole account.
    frames = {
        f"T{i}": ohlc([100.0] * 12 + [101.0, 101.0, 101.0], pad=0.01)
        for i in range(6)
    }

    result = run_backtest(
        frames,
        cfg(initial_equity=1_000.0, max_position_fraction=1.0, risk_fraction=0.5),
    )

    assert (result.cash >= -1e-9).all()
    assert (result.equity > 0).all()
    assert result.skipped_for_capital > 0  # and it recorded that it couldn't afford them


def test_exit_frees_cash_before_entries_on_the_same_bar(ohlc):
    """Swapping the exit and entry steps starves the entry. Ticker A is
    bought, then breaks down on the same bar B breaks out; the account can
    only fund one position at a time."""
    a = ohlc([100.0] * 12 + [200.0, 200.0, 200.0, 90.0, 90.0, 90.0])
    b = ohlc([100.0] * 17 + [300.0, 300.0])

    result = run_backtest(
        {"AAA": a, "BBB": b},
        cfg(initial_equity=2_000.0, max_position_fraction=1.0, risk_fraction=0.5),
    )

    assert {t.ticker for t in result.trades} == {"AAA", "BBB"}


def test_position_is_capped_by_max_position_fraction(ohlc):
    """A very low-volatility breakout asks for far more than the account.
    The fraction cap, not just cash, has to bind."""
    frame = ohlc([100.0] * 12 + [100.5, 100.5], pad=0.01)

    result = run_backtest({"AAA": frame}, cfg(max_position_fraction=0.25))

    notional = result.trades[0].shares * result.trades[0].entry_price
    assert notional <= 0.25 * 100_000 + 1e-6


# --- data-shape hazards ----------------------------------------------


def test_nan_atr_never_produces_nan_equity(ohlc):
    """suggested_shares() returns NaN (not None) for a NaN stop distance,
    because NaN <= 0 is False. One NaN share count would turn cash and
    every later equity point into NaN, silently."""
    healthy = ohlc(flat_then_breakout(12) + [160.0])
    # Only 4 bars — too few for a 3-period ATR plus the 5-bar entry window.
    stub = ohlc([50.0, 51.0, 52.0, 53.0])

    result = run_backtest({"AAA": healthy, "SHORT": stub}, cfg())

    assert np.isfinite(result.equity.to_numpy()).all()
    assert np.isfinite(result.cash.to_numpy()).all()


def test_ticker_entering_midway_changes_nothing_before_it_exists(ohlc):
    """A ticker whose history starts late must not affect earlier bars."""
    early = ohlc([100.0] * 12 + [200.0, 200.0, 200.0, 200.0], end="2026-08-11")
    late = ohlc([100.0] * 6, end="2026-08-11")  # only the last 6 bars

    alone = run_backtest({"AAA": early}, cfg())
    together = run_backtest({"AAA": early, "LATE": late}, cfg())

    first_late_bar = late.index[0]
    before = alone.equity.index < first_late_bar
    np.testing.assert_allclose(
        alone.equity.to_numpy()[before], together.equity.to_numpy()[before]
    )


def test_open_positions_are_closed_at_the_end(ohlc):
    """Otherwise every position still underwater when the run ends simply
    vanishes from the win rate."""
    frame = ohlc(flat_then_breakout(12) + [160.0, 170.0])

    result = run_backtest({"AAA": frame}, cfg())

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "end_of_backtest"


def test_delisted_position_is_force_liquidated(ohlc):
    """Data stops mid-position: the holding must be sold at its last known
    price rather than carried at a frozen mark forever."""
    dying_end = pd.Timestamp("2026-08-11")
    dying = ohlc(flat_then_breakout(12) + [160.0], end=dying_end)
    # The companion must end LATER, or AAA's final bar is also the
    # calendar's final bar and the stale sweep never gets a bar to count.
    alive = ohlc([100.0] * 25, end=dying_end + pd.tseries.offsets.BDay(6))

    result = run_backtest({"AAA": dying, "ALIVE": alive}, cfg(max_stale_bars=2))

    aaa = [t for t in result.trades if t.ticker == "AAA"]
    assert aaa and aaa[0].exit_reason == "stale"


# --- costs and determinism -------------------------------------------


def test_costs_reduce_return_monotonically(ohlc):
    frame = ohlc(flat_then_breakout(12) + [160.0, 170.0, 180.0])

    free = run_backtest({"AAA": frame}, cfg(cost_bps=0.0, slippage_bps=0.0))
    dear = run_backtest({"AAA": frame}, cfg(cost_bps=25.0, slippage_bps=25.0))

    assert dear.equity.iloc[-1] < free.equity.iloc[-1]


def test_runs_are_deterministic(ohlc):
    frames = {
        "AAA": ohlc(flat_then_breakout(12) + [160.0]),
        "BBB": ohlc([100.0] * 12 + [180.0, 180.0]),
    }

    first = run_backtest(frames, cfg())
    second = run_backtest(frames, cfg())

    pd.testing.assert_series_equal(first.equity, second.equity)
    assert [t.ticker for t in first.trades] == [t.ticker for t in second.trades]


def test_atr_stop_variant_exits_earlier_than_the_donchian_alone(ohlc):
    """The comparison run.

    The breakout bar itself inflates ATR, which pushes the stop a long way
    below the entry — so the decline has to be genuinely deep to reach it.
    That is itself the point of running the comparison: a 2x-ATR stop
    measured right after a violent breakout is much looser than it sounds.
    """
    closes = flat_then_breakout(12) + [160.0, 80.0, 78.0]
    frame = ohlc(closes)

    faithful = run_backtest({"AAA": frame}, cfg(use_atr_stop=False))
    stopped = run_backtest({"AAA": frame}, cfg(use_atr_stop=True))

    assert stopped.trades[0].exit_reason == "atr_stop"
    assert faithful.trades[0].exit_reason == "donchian_exit"
    # The stop fires intrabar; the Donchian exit waits for the next bar's fill.
    assert stopped.trades[0].exit_date < faithful.trades[0].exit_date


# --- allocation model and cash yield ---------------------------------


def test_equal_weight_deploys_its_slice(ohlc):
    """Each position targets 1/N of equity, where `risk` sizing would have
    been capped at max_position_fraction."""
    # Tiny ATR, so risk sizing asks for far more than the cap allows.
    frame = ohlc([100.0] * 12 + [100.5, 100.5], pad=0.01)

    risked = run_backtest({"AAA": frame}, cfg(sizing_mode="risk", max_position_fraction=0.20))
    equal = run_backtest({"AAA": frame}, cfg(sizing_mode="equal_weight", target_positions=4))

    risk_notional = risked.trades[0].shares * risked.trades[0].entry_price
    equal_notional = equal.trades[0].shares * equal.trades[0].entry_price

    # 1/4 of 100k, against 20% of 100k.
    assert equal_notional == pytest.approx(25_000, rel=0.02)
    assert risk_notional == pytest.approx(20_000, rel=0.02)


def test_equal_weight_never_spends_cash_it_does_not_have(ohlc):
    """The slice is a target, not a promise: with more concurrent signals
    than slots, later entries are bounded by remaining cash."""
    frames = {
        f"T{i}": ohlc([100.0] * 12 + [100.5 + i, 100.5 + i], pad=0.01) for i in range(6)
    }
    result = run_backtest(frames, cfg(sizing_mode="equal_weight", target_positions=4))

    assert (result.cash >= -1e-6).all()
    assert (result.equity > 0).all()


def test_equal_weight_r_multiples_come_from_the_position(ohlc):
    """R has to keep meaning "one stop_distance of adverse move". Under
    equal weight the size came from the allocation, not a risk target, so
    recording the intended 1%-of-equity budget would make every R-multiple
    fiction."""
    result = run_backtest(
        {"AAA": ohlc([100.0] * 12 + [100.5, 100.5], pad=0.01)},
        cfg(sizing_mode="equal_weight", target_positions=4),
    )
    trade = result.trades[0]

    expected_risk = trade.shares * trade.stop_distance
    assert trade.risk_dollars == pytest.approx(expected_risk)
    assert trade.r_multiple == pytest.approx(trade.pnl / expected_risk)


def test_cash_yield_defaults_to_zero_and_changes_nothing(ohlc):
    """Every result recorded before cash interest existed must reproduce
    exactly — including the 863.29% Donchian run this pins in miniature."""
    frame = ohlc(flat_then_breakout(12) + [205.0, 210.0])

    a = run_backtest({"AAA": frame}, cfg())
    b = run_backtest({"AAA": frame}, cfg(cash_yield_annual=0.0))

    pd.testing.assert_series_equal(a.equity, b.equity)


def test_cash_yield_only_pays_on_idle_cash(ohlc):
    """A strategy holding nothing compounds at the cash rate; the same
    strategy fully invested barely notices."""
    flat = ohlc([100.0] * 40)  # never signals, so the account stays in cash

    idle = run_backtest({"AAA": flat}, cfg(cash_yield_annual=0.05))
    no_yield = run_backtest({"AAA": flat}, cfg(cash_yield_annual=0.0))

    assert not idle.trades  # nothing was bought
    assert idle.equity.iloc[-1] > no_yield.equity.iloc[-1]
    assert no_yield.equity.iloc[-1] == pytest.approx(100_000.0)
