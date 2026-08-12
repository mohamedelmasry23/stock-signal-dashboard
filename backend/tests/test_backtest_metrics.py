"""
Metric tests, all against hand-computable values.

Every expected number here is derivable on paper. A metric test that
asserts "whatever the function returned last time" would pass forever
while the formula is wrong.
"""

import numpy as np
import pandas as pd
import pytest

from app.backtest import metrics


def series(values, start="2020-01-01", freq="D"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq), dtype=float)


# --- returns ----------------------------------------------------------


def test_total_return_is_first_to_last():
    assert metrics.total_return(series([100, 150, 200])) == pytest.approx(1.0)


def test_cagr_of_an_exact_doubling_over_two_years():
    """Doubling in two years is sqrt(2)-1 per year, ~41.4%."""
    equity = pd.Series(
        [100.0, 200.0],
        index=pd.to_datetime(["2020-01-01", "2022-01-01"]),
    )
    # 2020 is a leap year, so this window is 731 days = 2.0014 years.
    years = 731 / 365.25
    assert metrics.cagr(equity) == pytest.approx(2 ** (1 / years) - 1)
    assert metrics.cagr(equity) == pytest.approx(np.sqrt(2) - 1, abs=1e-3)


def test_total_return_and_cagr_are_none_on_degenerate_input():
    assert metrics.total_return(series([100])) is None
    assert metrics.cagr(series([100])) is None
    # A wiped-out account has no meaningful growth rate.
    assert metrics.cagr(series([100, 0])) is None


# --- drawdown ---------------------------------------------------------


def test_max_drawdown_is_measured_from_the_running_peak():
    """100 -> 110 -> 99: the drawdown is from the 110 peak, not the 100
    start, so it's -10% rather than -1%."""
    assert metrics.max_drawdown(series([100, 110, 99])) == pytest.approx(-0.10)


def test_drawdown_is_zero_for_a_monotonic_rise():
    assert metrics.max_drawdown(series([100, 110, 120])) == pytest.approx(0.0)


def test_drawdown_detail_reports_peak_trough_and_recovery():
    detail = metrics.drawdown_detail(series([100, 120, 90, 100, 130]))

    assert detail["max_drawdown"] == pytest.approx(-0.25)  # 120 -> 90
    assert detail["peak_date"] == "2020-01-02"
    assert detail["trough_date"] == "2020-01-03"
    assert detail["recovery_date"] == "2020-01-05"  # first close back above 120
    assert detail["longest_underwater_days"] >= 1


def test_drawdown_recovery_is_none_when_never_recovered():
    assert metrics.drawdown_detail(series([100, 120, 90]))["recovery_date"] is None


# --- risk-adjusted ----------------------------------------------------


def test_sharpe_matches_the_hand_computed_formula():
    equity = series([100, 102, 101, 105, 104, 108])
    returns = equity.pct_change().dropna()
    expected = returns.mean() / returns.std(ddof=1) * np.sqrt(252)

    assert metrics.sharpe(equity) == pytest.approx(expected)


def test_sharpe_is_none_when_the_curve_never_moves():
    """Zero variance would divide by zero. `inf` in a report reads as a
    spectacular result rather than as "this never moved"."""
    assert metrics.sharpe(series([100, 100, 100, 100])) is None


def test_sharpe_falls_when_a_risk_free_rate_is_charged():
    equity = series([100, 101, 102, 103, 104])
    assert metrics.sharpe(equity, risk_free_annual=0.05) < metrics.sharpe(equity, 0.0)


def test_sortino_ignores_upside_volatility():
    """A curve that only ever rises has no downside deviation, so Sortino
    is undefined rather than infinite."""
    assert metrics.sortino(series([100, 105, 110, 120])) is None


# --- trades -----------------------------------------------------------


class FakeTrade:
    """Stand-in for engine.Trade — metrics only reads these attributes."""

    def __init__(self, pnl, pnl_pct=0.0, r_multiple=0.0, bars_held=1):
        self.pnl = pnl
        self.pnl_pct = pnl_pct
        self.r_multiple = r_multiple
        self.bars_held = bars_held


def test_trade_stats_on_no_trades_is_none_not_zero():
    """Zero would claim a measured 0% win rate; None says "never traded"."""
    stats = metrics.trade_stats([])
    assert stats["n_trades"] == 0
    assert stats["win_rate"] is None
    assert stats["profit_factor"] is None


def test_win_rate_and_profit_factor():
    trades = [FakeTrade(100), FakeTrade(50), FakeTrade(-60), FakeTrade(-40)]
    stats = metrics.trade_stats(trades)

    assert stats["n_trades"] == 4
    assert stats["win_rate"] == pytest.approx(0.5)
    assert stats["avg_win"] == pytest.approx(75.0)
    assert stats["avg_loss"] == pytest.approx(-50.0)
    assert stats["profit_factor"] == pytest.approx(150 / 100)
    assert stats["expectancy"] == pytest.approx(12.5)


def test_pct_losses_worse_than_1r_counts_only_losers_beyond_their_risk():
    """The system sizes for 1R of risk but has no stop enforcing it, so
    this is the measurement of whether that sizing claim holds. Three
    losers, two of which blew through the intended risk."""
    trades = [
        FakeTrade(200, r_multiple=2.0),    # winner, ignored
        FakeTrade(-100, r_multiple=-1.0),  # exactly at risk, not "worse"
        FakeTrade(-250, r_multiple=-2.5),  # worse
        FakeTrade(-180, r_multiple=-1.8),  # worse
    ]
    stats = metrics.trade_stats(trades)

    assert stats["pct_losses_worse_than_1r"] == pytest.approx(2 / 3)
    assert stats["worst_loss_r"] == pytest.approx(-2.5)


def test_profit_factor_is_none_when_nothing_lost():
    """No losses means dividing by zero — undefined, not infinite."""
    assert metrics.trade_stats([FakeTrade(10), FakeTrade(20)])["profit_factor"] is None


# --- summary ----------------------------------------------------------


def test_summarize_reports_exposure_alongside_sharpe():
    equity = series([100, 102, 104, 103, 107])
    positions = pd.Series([0, 1, 1, 0, 2], index=equity.index)
    exposure = pd.Series([0.0, 0.5, 0.5, 0.0, 0.9], index=equity.index)

    out = metrics.summarize(equity, [], positions_count=positions, exposure=exposure)

    assert out["time_in_market"] == pytest.approx(0.6)  # 3 of 5 bars held something
    assert out["avg_gross_exposure"] == pytest.approx(0.38)
    assert out["bars"] == 5
    assert out["n_trades"] == 0
