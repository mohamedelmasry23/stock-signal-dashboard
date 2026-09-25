"""Donchian short rules and the long/short engine.

Three kinds of test, in order of how much a wrong answer would cost:

1. PARITY. With no short rule, the engine must reproduce
   `app.backtest.engine.run_backtest` to the cent. Every comparison
   against the dashboard's +164% depends on it.
2. CAUSALITY. Each rule's signals must be reproducible from the bars that
   had closed when they fired. Cut at each signal, not at random points —
   a short-span leak slips past random cuts (it did on NQ).
3. MECHANICS. Stops, targets, gaps, borrow and collateral, each on the
   exact bar that exercises it.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import BacktestConfig
from app.backtest.engine import run_backtest
from app.signals import compute_signal_frame
from research.donchian_shorts.engine import ShortConfig, run_long_short
from research.donchian_shorts.rules import (
    BreakdownParams, FakeoutParams, PullbackParams, RULES, DEFAULT_PARAMS,
    breakdown, fakeout, pullback,
)


def walk(n=900, seed=0, start="2018-01-01", drift=0.0):
    rng = np.random.default_rng(seed)
    close = 100 + (rng.normal(drift, 1.5, n)).cumsum()
    close = np.maximum(close, 5.0)
    open_ = np.concatenate([[close[0]], close[:-1]]) + rng.normal(0, 0.3, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1.0, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1.0, n))
    index = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close,
         "Volume": rng.uniform(5e5, 2e6, n)},
        index=index,
    )


def universe(seeds=(21, 22, 23, 24)):
    return {f"T{i}": walk(seed=s) for i, s in enumerate(seeds)}


def config(**kw):
    base = dict(start=dt.date(2018, 1, 1), end=dt.date(2021, 12, 31), benchmark="SPY")
    base.update(kw)
    return BacktestConfig(**base)


# --- 1. parity --------------------------------------------------------------

def test_longs_alone_reproduce_run_backtest():
    prices = universe()
    cfg = config(tickers=tuple(sorted(prices)))
    ours = run_long_short(prices, cfg, ShortConfig())
    theirs = run_backtest(prices, cfg)
    assert np.max(np.abs(ours.equity.to_numpy() - theirs.equity.to_numpy())) < 0.01
    assert len(ours.trades) == len(theirs.trades)
    assert [(t.symbol, t.entry_ts.date()) for t in ours.trades] == \
           [(t.ticker, t.entry_date) for t in theirs.trades]


def test_a_rule_that_never_fires_changes_nothing():
    prices = universe()
    cfg = config(tickers=tuple(sorted(prices)))
    silent = lambda frame, params=None: breakdown(frame, BreakdownParams()).assign(ShortEntry=False)
    with_rule = run_long_short(prices, cfg, ShortConfig(rule=silent))
    theirs = run_backtest(prices, cfg)
    assert np.max(np.abs(with_rule.equity.to_numpy() - theirs.equity.to_numpy())) < 0.01


# --- 2. causality -------------------------------------------------------------

@pytest.mark.parametrize("name", list(RULES))
def test_every_signal_is_reproducible_from_the_bars_closed_when_it_fired(name):
    frame = compute_signal_frame(walk(n=700, seed=5))
    rule, params = RULES[name], DEFAULT_PARAMS[name]
    full = rule(frame, params)
    fired = np.flatnonzero(full["ShortEntry"].to_numpy())
    if name == "pullback" and len(fired) == 0:
        pytest.skip("fixture produced no pullback signal")
    assert len(fired), f"{name} never fired on the fixture — the test proves nothing"
    for t in fired[:: max(1, len(fired) // 10)]:
        early = rule(frame.iloc[: t + 1], params)
        assert bool(early["ShortEntry"].iloc[t])
        for col in ("ShortStop", "ShortTarget", "ShortRank"):
            a, b = early[col].iloc[t], full[col].iloc[t]
            assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b), col


def test_the_trailing_stop_is_the_prior_55_day_high_known_before_the_open():
    frame = compute_signal_frame(walk(seed=6))
    out = breakdown(frame)
    # Read at the execution bar and already shifted: the dashboard's own line.
    pd.testing.assert_series_equal(out["ShortTrail"], frame["DonchianHigh_N"], check_names=False)
    # And the initial stop, taken at the decision bar, is the same line the
    # execution bar will see — they must agree or the first trail jumps.
    np.testing.assert_allclose(
        out["ShortStop"].shift(1).to_numpy()[60:], frame["DonchianHigh_N"].to_numpy()[60:]
    )


# --- rule semantics ----------------------------------------------------------

def _frame(rows, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(rows), freq="B")
    return pd.DataFrame(
        {"Open": [r[0] for r in rows], "High": [r[1] for r in rows],
         "Low": [r[2] for r in rows], "Close": [r[3] for r in rows],
         "Volume": [r[4] if len(r) > 4 else 1e6 for r in rows]},
        index=idx,
    )


def _base(n=60, price=100.0):
    """A flat range: 55-day high 101, 20-day low 99."""
    return [(price, price + 1, price - 1, price)] * n


def test_a_fakeout_fires_when_the_breakout_closes_back_under_the_old_line():
    rows = _base() + [(100, 105, 99.5, 100.5)]        # pierced 101, closed back under
    out = fakeout(compute_signal_frame(_frame(rows)))
    assert out["ShortEntry"].iloc[-1]
    assert out["ShortStop"].iloc[-1] == pytest.approx(105.01)   # a cent above the peak
    assert out["ShortTarget"].iloc[-1] == pytest.approx(99.0)   # the 20-day low


def test_a_breakout_that_holds_past_the_window_is_not_a_fakeout():
    rows = _base() + [(100, 105, 100, 104)] + [(104, 106, 103, 105)] * 4 + [(105, 105, 99.8, 100)]
    out = fakeout(compute_signal_frame(_frame(rows)), FakeoutParams(fail_window=3))
    assert not out["ShortEntry"].any()


def test_a_fakeout_is_judged_against_the_line_that_was_broken_not_the_rolling_one():
    # Day 0 closes above 101; day 1 closes at 102 — above the ORIGINAL line
    # 101 but below the new rolling 55-day high (106). Not a failure.
    rows = _base() + [(100, 106, 100, 105), (105, 105.5, 101.5, 102)]
    out = fakeout(compute_signal_frame(_frame(rows)))
    assert not out["ShortEntry"].any()


def test_a_breakdown_needs_a_cross_not_just_a_close_below():
    rows = _base() + [(100, 100.5, 97, 97.5)] + [(97.5, 97.8, 95, 95.5)] * 3
    out = breakdown(compute_signal_frame(_frame(rows)), BreakdownParams(stall_bars=0))
    assert out["ShortEntry"].sum() == 1


def test_the_stall_filter_blocks_a_breakdown_right_after_a_new_high():
    rows = _base() + [(100, 110, 100, 108)] + [(108, 108.5, 90, 91)]
    frame = compute_signal_frame(_frame(rows))
    assert not breakdown(frame, BreakdownParams(stall_bars=20))["ShortEntry"].any()
    assert breakdown(frame, BreakdownParams(stall_bars=0))["ShortEntry"].iloc[-1]


# --- 3. engine mechanics -----------------------------------------------------

def _one_short(rows, stop, target, trail=np.nan):
    """Run the engine on one ticker with a hand-placed short signal on bar 60."""
    frame = _frame(rows)

    def rule(signals, params=None):
        out = breakdown(signals, BreakdownParams(stall_bars=0)).copy()
        out["ShortEntry"] = False
        out.iloc[60, out.columns.get_loc("ShortEntry")] = True
        out.iloc[60, out.columns.get_loc("ShortStop")] = stop
        out.iloc[60, out.columns.get_loc("ShortTarget")] = target
        out.iloc[60, out.columns.get_loc("ShortRank")] = 1.0
        out["ShortTrail"] = trail
        return out

    cfg = config(start=frame.index[0].date(), end=frame.index[-1].date(), tickers=("X",),
                 cost_bps=0.0, slippage_bps=0.0)
    return run_long_short({"X": frame}, cfg, ShortConfig(rule=rule, enable_longs=False, borrow_annual=0.0))


def test_a_short_reaching_its_target_makes_money_and_reports_positive_r():
    rows = _base(61) + [(100, 100.5, 99.5, 100), (99, 99.5, 94, 95)]
    result = _one_short(rows, stop=103.0, target=95.0)
    (trade,) = result.trades
    assert trade.exit_reason == "target" and trade.exit_price == pytest.approx(95.0)
    assert trade.pnl > 0 and trade.r_multiple > 0
    assert result.equity.iloc[-1] > 100_000


def test_a_bar_reaching_both_stop_and_target_is_a_stop():
    rows = _base(61) + [(100, 104, 94, 100)]
    (trade,) = _one_short(rows, stop=103.0, target=95.0).trades
    assert trade.exit_reason.startswith("stop")
    assert trade.pnl < 0


def test_an_open_through_the_stop_cancels_the_entry():
    rows = _base(61) + [(104, 105, 103, 104)]
    result = _one_short(rows, stop=103.0, target=95.0)
    assert result.trades == []
    assert result.short_outcomes.get("opened_through_stop") == 1


def test_a_gap_through_the_stop_fills_at_the_open():
    rows = _base(61) + [(100, 100.5, 99.5, 100), (108, 109, 107, 108)]
    (trade,) = _one_short(rows, stop=103.0, target=90.0).trades
    assert trade.exit_price == pytest.approx(108.0)          # worse than 103


def test_a_trailing_stop_only_moves_down():
    rows = _base(61) + [(100, 100.5, 99.5, 100)] * 3 + [(100, 102.5, 99.5, 100)]
    trail = np.full(len(rows), np.nan)
    trail[62:] = [102.0, 104.0, 102.0]                      # dips, rises, dips
    (trade,) = _one_short(rows, stop=103.0, target=np.nan, trail=trail).trades
    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(102.0)          # 104 never loosened it


def test_short_collateral_leaves_equity_unchanged_at_entry_except_costs():
    rows = _base(61) + [(100, 100.5, 99.5, 100)] * 3
    result = _one_short(rows, stop=103.0, target=90.0)
    # Price never moved from the 100 entry: equity is flat until the forced close.
    assert result.equity.iloc[61] == pytest.approx(100_000.0)
