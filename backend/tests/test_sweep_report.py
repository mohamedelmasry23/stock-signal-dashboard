"""The verdict line, which is the part of the report that stops a misreading.

A sensitivity table's top row is always the best row. Whether that row is
an edge or an artefact is decided by the shape of its neighbours, and the
verdict line is the only place the output says which. It is tested because
its first version got the sweep veto wrong: judging absolute returns, it
called a set of variants scattered around a 164% baseline "a plateau".
"""

from __future__ import annotations

from research.sweep.report import _verdict, format_sensitivity, format_funnel


def test_a_scattered_effect_around_a_baseline_reads_as_noise():
    # The real veto numbers: one window better, the rest worse.
    returns = [1.612, 1.252, 1.454, 1.811, 0.840]
    verdict = _verdict(returns, baseline=1.636)
    assert "noise around the baseline" in verdict
    assert "1 better" in verdict and "4 worse" in verdict


def test_a_consistent_improvement_reads_as_a_plateau():
    assert "plateau of improvement" in _verdict([1.8, 1.9, 1.85, 1.88], baseline=1.636)


def test_a_rule_that_never_helps_says_so_plainly():
    assert "every setting was worse" in _verdict([1.2, 1.4, 1.5], baseline=1.636)


def test_without_a_baseline_a_lone_winner_reads_as_curve_fitting():
    assert "spike" in _verdict([-0.2, -0.1, 0.9, -0.3])


def test_too_few_settings_refuses_to_judge():
    assert "too few" in _verdict([0.1, 0.2])


def test_the_funnel_reports_zero_setups_without_dividing_by_zero():
    assert format_funnel({}) == ""
    text = format_funnel({"sweeps": 0, "setups": 0, "no_structure_shift": 0})
    assert "setups produced" in text


def test_a_sensitivity_table_keeps_settings_that_produced_no_trades():
    rows = [
        ("min_r", 1.0, {"total_return": 0.1, "sharpe": 0.5, "max_drawdown": -0.1, "n_trades": 9, "win_rate": 0.4}),
        ("min_r", 2.0, {"total_return": 0.0, "sharpe": None, "max_drawdown": 0.0, "n_trades": 0, "win_rate": None}),
        ("min_r", 3.0, {"total_return": -0.1, "sharpe": -0.2, "max_drawdown": -0.2, "n_trades": 3, "win_rate": 0.0}),
    ]
    text = format_sensitivity(rows)
    # A setting that produced nothing is a fact about the setting; dropping
    # the row would make the grid look uniformly tradeable.
    assert text.count("min_r") >= 1
    assert "0" in text and "—" in text
