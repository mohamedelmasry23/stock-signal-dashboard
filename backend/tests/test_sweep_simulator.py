"""The simulator's four pessimistic conventions, and short accounting.

Each of these is a place a backtest can quietly pay itself. They are
tested on hand-built setups rather than on whatever the detectors find,
because a convention has to be checked on the exact bar that triggers it.
"""

from __future__ import annotations

import pandas as pd
import pytest

from research.sweep.instruments import Instrument
from research.sweep.params import SweepParams
from research.sweep.simulate import SimConfig, run_simulation
from research.sweep.testing import bars, make_context, make_setup

FREE = Instrument(symbol="X", tick_size=0.01, cost_bps=0.0, slippage_bps=0.0, borrow_annual=0.0)


def frictionless(**overrides) -> SimConfig:
    """Costs off, so a test asserts on the rule and not on the fee.

    `max_gross=100` removes the exposure cap too: these tests are about
    when an order fills and at what price, and a cap that binds would make
    every assertion depend on sizing as well.
    """
    base = dict(
        instruments={"X": FREE}, initial_equity=100_000.0,
        max_gross=100.0, max_position_fraction=1.0, min_notional=0.0,
    )
    base.update(overrides)
    return SimConfig(**base)


def run(frame, setups, config=None):
    context = make_context("X", frame, setups)
    return run_simulation([context], config or frictionless())


# --- convention 1: a limit fills on a trade THROUGH, not on a touch --------

def test_a_bar_that_only_touches_the_limit_does_not_fill():
    # Bar 1 makes the setup valid; bar 2's low is exactly the limit.
    frame = bars([
        (100, 101, 99, 100),
        (100, 101, 95.00, 100),     # low == limit
        (100, 101, 99, 100),
    ])
    setup = make_setup(frame, "bullish", entry_limit=95.0, stop=94.0, target=99.0, valid_from_bar=0)
    result = run(frame, [setup])
    assert result.trades == []
    assert result.order_outcomes.get("filled", 0) == 0


def test_a_bar_that_trades_through_the_limit_fills():
    frame = bars([
        (100, 101, 99, 100),
        (100, 101, 94.99, 100),     # low < limit
        (100, 101, 99, 100),
    ])
    setup = make_setup(frame, "bullish", entry_limit=95.0, stop=94.0, target=99.0, valid_from_bar=0)
    result = run(frame, [setup])
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == pytest.approx(95.0)


def test_a_bar_that_opens_below_a_buy_limit_fills_at_the_open():
    frame = bars([
        (100, 101, 99, 100),
        (93.0, 94.0, 92.0, 93.5),   # gapped past the limit before it could rest
        (93, 94, 92, 93),
    ])
    setup = make_setup(frame, "bullish", entry_limit=95.0, stop=91.0, target=99.0, valid_from_bar=0)
    result = run(frame, [setup])
    assert result.trades[0].entry_price == pytest.approx(93.0)


# --- convention 2: same-bar stop and target resolve to the stop -----------

def test_a_bar_covering_both_stop_and_target_resolves_to_the_stop():
    frame = bars([
        (100, 101, 99, 100),
        (100, 101, 94.5, 100),      # fills at 95
        (100, 120, 80, 100),        # covers target 110 AND stop 90
    ])
    setup = make_setup(frame, "bullish", entry_limit=95.0, stop=90.0, target=110.0, valid_from_bar=0)
    result = run(frame, [setup])
    assert [t.exit_reason for t in result.trades] == ["stop"]
    assert result.trades[0].pnl < 0


# --- convention 3: the entry bar is checked for its own stop --------------

def test_a_position_is_stopped_on_its_entry_bar_when_the_bar_reaches_the_stop():
    frame = bars([
        (100, 101, 99, 100),
        (100, 101, 89.0, 92.0),     # fills at 95, then reaches 89 < stop 90
        (92, 93, 91, 92),
    ])
    setup = make_setup(frame, "bullish", entry_limit=95.0, stop=90.0, target=110.0, valid_from_bar=0)
    stopped = run(frame, [setup])
    assert [t.exit_reason for t in stopped.trades] == ["stop_same_bar"]
    assert stopped.trades[0].bars_held == 0

    # And the optimistic reading, kept switchable so the cost of the
    # convention can be measured rather than argued about.
    lenient = run(frame, [setup], frictionless(check_stop_on_entry_bar=False))
    assert [t.exit_reason for t in lenient.trades] == ["end_of_run"]


# --- convention 4: gaps fill at the open ----------------------------------

def test_a_bar_that_gaps_through_the_stop_fills_at_the_open_not_the_stop():
    frame = bars([
        (100, 101, 99, 100),
        (100, 101, 94.5, 100),      # fills at 95
        (85.0, 86.0, 84.0, 85.0),   # opened below the 90 stop
    ])
    setup = make_setup(frame, "bullish", entry_limit=95.0, stop=90.0, target=110.0, valid_from_bar=0)
    result = run(frame, [setup])
    assert result.trades[0].exit_price == pytest.approx(85.0)
    assert result.trades[0].exit_reason == "stop"


# --- expiry ----------------------------------------------------------------

def test_an_order_that_never_fills_expires_rather_than_entering_at_market():
    frame = bars([(100, 101, 99, 100)] * 6)
    setup = make_setup(
        frame, "bullish", entry_limit=95.0, stop=94.0, target=99.0,
        valid_from_bar=0, expires_bar=3,
    )
    result = run(frame, [setup])
    assert result.trades == []
    assert result.order_outcomes.get("expired_unfilled") == 1


# --- shorts ---------------------------------------------------------------

def test_a_winning_short_increases_equity_and_a_losing_one_does_not():
    down = bars([
        (100, 101, 99, 100),
        (100, 106.0, 99, 100),      # trades through the 105 limit
        (100, 101, 94.0, 95.0),     # reaches the 95 target
    ])
    setup = make_setup(down, "bearish", entry_limit=105.0, stop=110.0, target=95.0, valid_from_bar=0)
    won = run(down, [setup])
    assert [t.exit_reason for t in won.trades] == ["target"]
    assert won.trades[0].pnl > 0
    assert won.trades[0].r_multiple > 0
    assert won.equity.iloc[-1] > 100_000.0

    up = bars([
        (100, 101, 99, 100),
        (100, 106.0, 99, 100),
        (100, 111.0, 99, 110.0),    # reaches the 110 stop
    ])
    lost = run(up, [make_setup(up, "bearish", 105.0, 110.0, 95.0, valid_from_bar=0)])
    assert [t.exit_reason for t in lost.trades] == ["stop"]
    assert lost.trades[0].pnl < 0
    assert lost.trades[0].r_multiple < 0
    assert lost.equity.iloc[-1] < 100_000.0


def test_borrow_accrues_on_shorts_only_and_over_calendar_time():
    frame = bars([
        (100, 101, 99, 100),
        (100, 106.0, 99, 100),
        *[(100, 101, 99, 100)] * 20,
    ], freq="1D")
    borrowed = Instrument(symbol="X", cost_bps=0.0, slippage_bps=0.0, borrow_annual=0.10)
    config = frictionless(instruments={"X": borrowed})
    short = run(frame, [make_setup(frame, "bearish", 105.0, 110.0, 95.0, valid_from_bar=0)], config)
    assert short.trades[0].borrow_cost > 0

    long_frame = bars([
        (100, 101, 99, 100),
        (100, 101, 94.0, 100),
        *[(100, 101, 99, 100)] * 20,
    ], freq="1D")
    went_long = run(long_frame, [make_setup(long_frame, "bullish", 95.0, 90.0, 120.0, valid_from_bar=0)], config)
    assert went_long.trades[0].borrow_cost == 0.0


# --- contract specs --------------------------------------------------------

def test_point_value_scales_pnl_and_exposure():
    frame = bars([
        (100, 101, 99, 100),
        (100, 101, 94.5, 100),
        (100, 111.0, 99, 100),
    ])
    setup = make_setup(frame, "bullish", entry_limit=95.0, stop=90.0, target=110.0, valid_from_bar=0)
    single = run(frame, [setup], frictionless(instruments={"X": FREE}))
    fifty = run(
        frame, [setup],
        frictionless(instruments={"X": Instrument("X", 0.01, 50.0, 0.0, 0.0, 0.0, 0.0, 0.0)}),
    )
    # Same trade, same fraction of equity risked, so the same P&L in
    # dollars — the point value changes how many units that is, not how
    # much is at stake. That equivalence is the whole reason sizing is
    # expressed in exposure.
    assert fifty.trades[0].shares == pytest.approx(single.trades[0].shares / 50.0)
    assert fifty.trades[0].pnl == pytest.approx(single.trades[0].pnl)


def test_sharpe_is_computed_on_daily_equity_not_on_five_minute_bars():
    frame = bars([(100, 101, 99, 100)] * (12 * 24 * 3))     # three days of 5m bars
    result = run(frame, [])
    assert len(result.equity) == 12 * 24 * 3
    # One point per trading day, whatever the bar size.
    assert len(result.daily_equity) <= 5
    assert result.summary["bars"] == len(result.daily_equity)
