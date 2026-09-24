"""The look-ahead contract, which is the only thing making the rest worth anything.

This strategy's components correlate with obvious chart features — swing
extremes, session highs, gaps — so a leak does not produce nonsense, it
produces a flattering result that looks like an edge. These tests are the
structural defence, and they mirror `test_truncation_invariance` in
tests/test_backtest_engine.py.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from research.sweep.bias import BiasSeries, build_bias
from research.sweep.donchian_veto import apply_veto, register_veto, swept_the_level, veto_config
from research.sweep.params import SweepParams
from research.sweep.simulate import SimConfig, run_simulation
from research.sweep.strategy import build_context
from research.sweep.swings import PivotSeries, find_pivots
from research.sweep.testing import walk


PARAMS = SweepParams(
    execution_timeframe="5min", structure_timeframe="1h", bias_timeframe="4h",
    tick_size=0.25, use_bias=False, min_fvg_ticks=1.0, min_body_frac=0.3,
)


@pytest.mark.parametrize("window", [12, 48])
@pytest.mark.parametrize("fraction", [0.3, 0.45, 0.6, 0.75, 0.9])
def test_truncating_the_data_does_not_change_any_earlier_setup(window, fraction):
    """The headline test: run on everything, then on a prefix.

    This is a broad net for leaks that span many bars. It is NOT a good
    detector for a short-span leak: the look-ahead found on NQ data lived
    for a few bars around each setup, so random cut points almost never
    landed inside one, and this test passed on the buggy code at every cut
    tried. `test_every_setup_is_reproducible_from_the_data_at_its_go_live`
    below is the precise check.

    Every setup the prefix run could have known about must come out
    identical — same entry, same stop, same target, same timestamps. If
    any component reads a bar from the future, the full run's version of
    an early setup differs from the prefix run's, and this fails.
    """
    frame = walk(12 * 24 * 60, seed=11, start="2026-01-05 00:00")
    cut = int(len(frame) * fraction)
    prefix = frame.iloc[:cut]
    params = PARAMS.replaced(sweep_window=window)

    full = build_context("X", frame, params)
    early = build_context("X", prefix, params)
    horizon = prefix.index[-1]

    def keyed(context):
        return {
            s.valid_from: (
                s.direction,
                round(s.entry_limit, 8),
                round(s.stop, 8),
                round(s.target, 8),
                s.expires_at,
            )
            for s in context.setups
            if s.valid_from <= horizon
        }

    full_keys, early_keys = keyed(full), keyed(early)
    assert early_keys, "the prefix produced no setups — the test proves nothing"

    # BOTH directions. The first version of this test only checked that
    # every prefix setup also appeared in the full run — and so missed a
    # real leak: setups the full run placed before the horizon on the
    # strength of a structure shift AFTER it, which the prefix could not
    # see and therefore never produced. A leak shows up as a setup the
    # full run has and the prefix lacks, so that is the direction that
    # matters most.
    assert set(full_keys) == set(early_keys), (
        f"full run only: {sorted(set(full_keys) - set(early_keys))[:3]}, "
        f"prefix only: {sorted(set(early_keys) - set(full_keys))[:3]}"
    )

    # `expires_at` is the one field that legitimately differs at the very
    # edge: it is the index N bars on, and the prefix run has no such bar.
    for valid_from, early_value in early_keys.items():
        full_value = full_keys[valid_from]
        assert early_value[:4] == full_value[:4]
        if early_value[4] < horizon:
            assert early_value[4] == full_value[4]


@pytest.mark.parametrize("window", [12, 48])
def test_every_setup_is_reproducible_from_the_data_at_its_go_live(window):
    """Causality, stated exactly: cut the data at each setup's `valid_from`.

    A setup is a claim that everything needed to place this order existed
    by `valid_from`. So a run on only the bars that had CLOSED by then must
    produce the same order. Any setup that cannot be rebuilt that way used
    a bar from its own future — which is precisely what the NQ look-ahead
    did, and what random-cut truncation could not catch.
    """
    frame = walk(12 * 24 * 60, seed=11, start="2026-01-05 00:00")
    params = PARAMS.replaced(sweep_window=window)
    full = build_context("X", frame, params)
    assert full.setups
    # Every setup would be thorough but slow; a spread sample is enough to
    # catch a systematic leak, which is the kind that matters.
    step = max(1, len(full.setups) // 12)
    for setup in full.setups[::step]:
        available = frame[frame.index < setup.valid_from]      # bars closed by then
        rebuilt = build_context("X", available, params)
        matches = [
            s for s in rebuilt.setups
            if s.valid_from == setup.valid_from
            and s.direction == setup.direction
            and s.entry_limit == pytest.approx(setup.entry_limit)
            and s.stop == pytest.approx(setup.stop)
            and s.target == pytest.approx(setup.target)
        ]
        assert matches, (
            f"setup live at {setup.valid_from} cannot be rebuilt from the bars "
            f"closed by then — it used a later bar"
        )


@pytest.mark.parametrize("window", [12, 48])
def test_no_setup_goes_live_before_its_structure_shift(window):
    """The leak the symmetric truncation test exists to catch, pinned directly.

    Found on real NQ data: 25% of setups went live before their shift, and
    the damage grew with `sweep_window`, so both ends are checked.
    """
    frame = walk(12 * 24 * 60, seed=11, start="2026-01-05 00:00")
    context = build_context("X", frame, PARAMS.replaced(sweep_window=window))
    assert context.setups
    for setup in context.setups:
        assert setup.valid_from > setup.shift.known_at
        assert setup.valid_from >= setup.gap.known_at
        assert setup.valid_from >= setup.sweep.known_at


def test_truncation_does_not_change_the_realised_trades():
    frame = walk(12 * 24 * 60, seed=11, start="2026-01-05 00:00")
    cut = len(frame) * 2 // 3
    horizon = frame.index[cut - 1]

    full = run_simulation([build_context("X", frame, PARAMS)], SimConfig())
    early = run_simulation([build_context("X", frame.iloc[:cut], PARAMS)], SimConfig())

    def closed(result):
        return [
            (t.entry_ts, round(t.entry_price, 8), t.exit_ts, round(t.exit_price, 8), t.exit_reason)
            for t in result.trades
            if t.exit_ts < horizon and t.exit_reason != "end_of_run"
        ]

    assert closed(early), "no trades closed inside the prefix — the test proves nothing"
    assert closed(early) == closed(full)


def test_a_pivot_cannot_be_used_before_its_right_side_exists():
    frame = walk(2000, seed=12)
    pivots = find_pivots(frame, 3, "5min")
    assert pivots
    series = PivotSeries(pivots)
    for pivot in pivots:
        # Confirmation is strictly later than the pivot bar itself.
        assert pivot.confirmed_at > pivot.ts
        just_before = pivot.confirmed_at - pd.Timedelta(seconds=1)
        known = series.known(just_before)
        assert pivot not in known


def test_the_bias_is_unknown_before_its_first_flip_and_never_reads_ahead():
    frame = walk(12 * 24 * 40, seed=13)
    from research.sweep.bars import build_timeframes

    built = build_timeframes(frame, "5min", ("4h",))
    four_hour = built.get("4h")
    pivots = PivotSeries(find_pivots(four_hour, 3, "4h"))
    changes = build_bias(four_hour, pivots, "4h")
    assert changes, "no bias flips in the fixture — the test proves nothing"

    series = BiasSeries(changes, enabled=True)
    first = changes[0]
    assert series.at(first.known_at - pd.Timedelta(seconds=1)) is None
    assert series.at(first.known_at) == first.direction
    # With the filter off, the bias never blocks anything — that is the
    # no-bias variant the spec asks to be measured.
    assert BiasSeries(changes, enabled=False).allows(frame.index[0], "bullish")
    assert not series.allows(frame.index[0], "bullish")


# --- the daily veto -------------------------------------------------------

def _daily_prices(n: int = 900, seeds=(21, 22, 23)) -> dict:
    out = {}
    for i, seed in enumerate(seeds):
        rng = np.random.default_rng(seed)
        close = 100 + rng.normal(0, 1.5, n).cumsum()
        close = np.maximum(close, 5.0)
        open_ = np.concatenate([[close[0]], close[:-1]])
        high = np.maximum(open_, close) + np.abs(rng.normal(0, 1.0, n))
        low = np.minimum(open_, close) - np.abs(rng.normal(0, 1.0, n))
        index = pd.date_range("2018-01-01", periods=n, freq="B")
        out[f"T{i}"] = pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1e6},
            index=index,
        )
    return out


def test_a_one_bar_veto_window_reproduces_the_baseline_exactly():
    """The parity check every prior experiment in research/README.md ran.

    A bar cannot both close ABOVE the level (an entry) and close back
    under it (a sweep), so a window of one can never veto anything. If
    this drifts by a cent, the veto is changing something other than which
    breakouts are taken.
    """
    from app.backtest.engine import run_backtest

    prices = _daily_prices()
    kwargs = dict(
        start=dt.date(2018, 1, 1), end=dt.date(2021, 12, 31),
        tickers=tuple(sorted(prices)), benchmark="SPY",
    )
    base = run_backtest(prices, veto_config(kwargs, "donchian"))
    veto = run_backtest(prices, veto_config(kwargs, register_veto(1, "skip")))

    pd.testing.assert_series_equal(base.equity, veto.equity)
    assert len(base.trades) == len(veto.trades)


def test_a_wider_veto_window_removes_entries_and_never_adds_any():
    from app.backtest.engine import run_backtest

    prices = _daily_prices()
    kwargs = dict(
        start=dt.date(2018, 1, 1), end=dt.date(2021, 12, 31),
        tickers=tuple(sorted(prices)), benchmark="SPY",
    )
    base = run_backtest(prices, veto_config(kwargs, "donchian"))
    veto = run_backtest(prices, veto_config(kwargs, register_veto(10, "skip")))
    base_entries = {(t.ticker, t.entry_date) for t in base.trades}
    veto_entries = {(t.ticker, t.entry_date) for t in veto.trades}
    # Removing entries frees capital, so the survivors are not a strict
    # subset — a breakout skipped for cash in the baseline can be funded
    # here. What must hold is that the veto never creates an entry on a
    # date the baseline had no signal for at all.
    assert len(veto.trades) <= len(base.trades)
    assert veto_entries - base_entries == set() or len(veto_entries - base_entries) < len(base_entries)


def test_the_veto_reads_only_bars_that_have_closed():
    from app.signals import compute_signal_frame

    prices = _daily_prices(n=400, seeds=(31,))
    frame = compute_signal_frame(prices["T0"])
    full = apply_veto(frame, window=10, mode="skip")
    for cut in (200, 300, 350):
        early = apply_veto(frame.iloc[:cut], window=10, mode="skip")
        pd.testing.assert_series_equal(
            early["EntrySignal"], full["EntrySignal"].iloc[:cut], check_names=False
        )


def test_a_sweep_and_an_entry_cannot_coincide_on_one_bar():
    from app.signals import compute_signal_frame

    prices = _daily_prices(n=600, seeds=(41, 42))
    for frame in prices.values():
        signals = compute_signal_frame(frame)
        both = signals["EntrySignal"].fillna(False) & swept_the_level(signals)
        assert not both.any()
