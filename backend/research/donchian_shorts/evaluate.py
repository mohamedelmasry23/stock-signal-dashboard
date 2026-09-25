"""Every number in experiment 8, from one command.

    ../.venv/bin/python -m research.donchian_shorts.evaluate

Same universe, period, costs and borrow as experiments 1-6, so the rows
here sit directly beside those. Three ways of combining each short rule
with the Donchian longs are reported, because they answer different
questions:

  alone       the short rule on the whole account — does it have an edge?
  one book    longs and shorts sharing cash and the 20 slots, longs funded
              first — what happens to the account you actually run?
  sleeves     half the capital each, never interacting — the diversification
              case, computed from the two separate curves.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd

from app.backtest.metrics import summarize
from .engine import ShortConfig, run_long_short
from .rules import (
    BreakdownParams, DEFAULT_PARAMS, FakeoutParams, LABELS, PullbackParams, RULES,
)

SPEC = "constituents:data/universes/sp500_constituents.csv,etfs"
START, END = dt.date(2016, 1, 1), dt.date(2026, 8, 11)

GRID = {
    "breakdown": [("stall_bars", v) for v in (0, 10, 20, 40)],
    "fakeout": [("fail_window", v) for v in (0, 1, 3, 5)] + [("buffer_atr", v) for v in (0.25, 0.5)],
    "pullback": [("far_atr", v) for v in (2.0, 3.0, 5.0)]
    + [("touch_atr", v) for v in (0.1, 0.5)]
    + [("use_volume", False)],
}

_STATE: dict = {}


def _load() -> None:
    from app.backtest.__main__ import resolve_costs, resolve_membership, resolve_universe
    from app.backtest.config import BacktestConfig
    from app.backtest.prices import load_prices

    schedule = resolve_membership(SPEC, START)
    tickers, _ = resolve_universe(SPEC, START)
    cost_bps, slip_bps = resolve_costs(SPEC, None, None)
    prices = load_prices(sorted(set(tickers) | {"SPY"}), START, END, offline=True, verbose=False)
    bench = prices.pop("SPY", None)
    _STATE.update(
        prices=prices, bench=bench, schedule=schedule,
        config=BacktestConfig(start=START, end=END, tickers=tuple(sorted(prices)),
                              benchmark="SPY", cost_bps=cost_bps, slippage_bps=slip_bps),
    )


def _run(job):
    rule_name, params, mode = job
    shorts = ShortConfig(
        rule=RULES[rule_name] if rule_name else None,
        params=params,
        enable_longs=(mode != "alone"),
    )
    r = run_long_short(_STATE["prices"], _STATE["config"], shorts,
                       _STATE["bench"], _STATE["schedule"])
    return job, r.equity, r.trades_frame(), r.summary, r.short_outcomes


def _curve_stats(equity: pd.Series) -> dict:
    return summarize(equity, [], None, None)


def _pct(x, p=1):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:+.{p}f}%"


def _num(x, p=2):
    return "—" if x is None or not np.isfinite(x) else f"{x:.{p}f}"


def _side_stats(trades: pd.DataFrame, direction: str) -> dict:
    t = trades[trades["direction"] == direction] if len(trades) else trades
    if not len(t):
        return {"n": 0, "win": np.nan, "mean_r": np.nan, "pf": np.nan, "pnl": 0.0, "bars": np.nan}
    wins, losses = t.pnl[t.pnl > 0].sum(), -t.pnl[t.pnl < 0].sum()
    return {
        "n": len(t), "win": float((t.pnl > 0).mean()), "mean_r": float(t.r_multiple.mean()),
        "pf": float(wins / losses) if losses else np.nan, "pnl": float(t.pnl.sum()),
        "bars": float(t.bars_held.mean()),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="research.donchian_shorts.evaluate")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-grid", action="store_true")
    args = parser.parse_args(argv)

    _load()
    from app.backtest.engine import run_backtest

    # --- parity on the real universe -------------------------------------
    official = run_backtest(_STATE["prices"], _STATE["config"], _STATE["bench"], membership=_STATE["schedule"])
    jobs = [(None, None, "long")]
    for name in RULES:
        jobs += [(name, DEFAULT_PARAMS[name], "alone"), (name, DEFAULT_PARAMS[name], "book")]
    if not args.no_grid:
        for name, grid in GRID.items():
            for field, value in grid:
                p = replace(DEFAULT_PARAMS[name], **{field: value})
                if p == DEFAULT_PARAMS[name]:
                    continue
                jobs += [(name, p, "alone"), (name, p, "book")]

    with ProcessPoolExecutor(max_workers=args.workers, initializer=_load) as pool:
        results = {(j[0], j[1], j[2]): rest for j, *rest in pool.map(_run, jobs)}

    long_eq, long_trades, long_sum, _ = results[(None, None, "long")]
    drift = float(np.max(np.abs(long_eq.to_numpy() - official.equity.to_numpy())))
    print(f"[parity] longs alone vs app.backtest.run_backtest: max equity difference ${drift:.4f} "
          f"({'OK' if drift < 0.01 else 'FAILED — do not trust anything below'})")
    if drift >= 0.01:
        return 1

    bench = _STATE["bench"]["Close"].reindex(long_eq.index).ffill()
    spy = bench / bench.iloc[0] * long_eq.iloc[0]
    spy_s = _curve_stats(spy)
    long_monthly = long_eq.resample("ME").last().pct_change().dropna()

    print("\n## Headline\n")
    print("| Run | Total | CAGR | Max DD | Sharpe | Shorts | Short win | Short mean R | Short P&L | Long P&L |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    lp = _side_stats(long_trades, "bullish")
    print(f"| **Donchian long only** | **{_pct(long_sum['total_return'], 0)}** | {_pct(long_sum['cagr'])} | "
          f"{_pct(long_sum['max_drawdown'])} | {_num(long_sum['sharpe'])} | — | — | — | — | ${lp['pnl']:,.0f} |")
    print(f"| SPY buy & hold | {_pct(spy_s['total_return'], 0)} | {_pct(spy_s['cagr'])} | "
          f"{_pct(spy_s['max_drawdown'])} | {_num(spy_s['sharpe'])} | | | | | |")

    detail = {}
    for name in RULES:
        p = DEFAULT_PARAMS[name]
        a_eq, a_tr, a_sum, a_out = results[(name, p, "alone")]
        b_eq, b_tr, b_sum, b_out = results[(name, p, "book")]
        sleeves = 0.5 * long_eq + 0.5 * a_eq
        s_sum = _curve_stats(sleeves)
        sa, sb, lb = _side_stats(a_tr, "bearish"), _side_stats(b_tr, "bearish"), _side_stats(b_tr, "bullish")
        corr = long_monthly.corr(a_eq.resample("ME").last().pct_change().dropna())
        detail[name] = (a_eq, a_tr, a_out, corr, sa)
        label = LABELS[name]
        print(f"| {label} — alone | {_pct(a_sum['total_return'], 0)} | {_pct(a_sum['cagr'])} | "
              f"{_pct(a_sum['max_drawdown'])} | {_num(a_sum['sharpe'])} | {sa['n']} | {_pct(sa['win'], 0)} | "
              f"{_num(sa['mean_r'], 3)} | ${sa['pnl']:,.0f} | — |")
        print(f"| {label} — one book with longs | {_pct(b_sum['total_return'], 0)} | {_pct(b_sum['cagr'])} | "
              f"{_pct(b_sum['max_drawdown'])} | {_num(b_sum['sharpe'])} | {sb['n']} | {_pct(sb['win'], 0)} | "
              f"{_num(sb['mean_r'], 3)} | ${sb['pnl']:,.0f} | ${lb['pnl']:,.0f} |")
        print(f"| {label} — 50/50 sleeves | {_pct(s_sum['total_return'], 0)} | {_pct(s_sum['cagr'])} | "
              f"{_pct(s_sum['max_drawdown'])} | {_num(s_sum['sharpe'])} | | | | | |")

    print("\n## How each short book relates to the Donchian book\n")
    print("| Rule | Monthly correlation with Donchian | Avg days held | Exit reasons | Signals not taken |")
    print("|---|---|---|---|---|")
    for name, (a_eq, a_tr, a_out, corr, sa) in detail.items():
        shorts = a_tr[a_tr.direction == "bearish"]
        reasons = shorts.exit_reason.str.replace("_same_bar", "").value_counts().to_dict()
        skipped = {k: v for k, v in a_out.items() if k != "filled"}
        print(f"| {LABELS[name]} | {corr:+.2f} | {sa['bars']:.0f} | {reasons} | {skipped} |")

    print("\n## Short book alone, by year\n")
    years = range(2016, 2027)
    print("| Year | Donchian | " + " | ".join(LABELS[n].split(' ')[0] for n in RULES) + " |")
    print("|---|---|" + "---|" * len(RULES))
    ly = long_eq.resample("YE").last()
    ly = ly.pct_change().fillna(ly.iloc[0] / long_eq.iloc[0] - 1)
    ys = {}
    for name, (a_eq, *_rest) in detail.items():
        y = a_eq.resample("YE").last()
        ys[name] = y.pct_change().fillna(y.iloc[0] / a_eq.iloc[0] - 1)
    for yr in years:
        cells = [_pct(ly[ly.index.year == yr].iloc[0]) if (ly.index.year == yr).any() else "—"]
        for name in RULES:
            s = ys[name]
            cells.append(_pct(s[s.index.year == yr].iloc[0]) if (s.index.year == yr).any() else "—")
        print(f"| {yr} | " + " | ".join(cells) + " |")

    if not args.no_grid:
        print("\n## Sensitivity — each judgement call moved, the rest at default\n")
        print("| Rule | Setting | Alone: total | Alone: Sharpe | Shorts | Mean R | One book: total | One book: Sharpe |")
        print("|---|---|---|---|---|---|---|---|")
        for name, grid in GRID.items():
            p0 = DEFAULT_PARAMS[name]
            rows = [("default", p0)] + [
                (f"{f}={v}", replace(p0, **{f: v})) for f, v in grid if replace(p0, **{f: v}) != p0
            ]
            for label, p in rows:
                _, a_tr, a_sum, _ = results[(name, p, "alone")]
                _, _, b_sum, _ = results[(name, p, "book")]
                sa = _side_stats(a_tr, "bearish")
                print(f"| {LABELS[name].split(' ')[0]} | {label} | {_pct(a_sum['total_return'], 0)} | "
                      f"{_num(a_sum['sharpe'])} | {sa['n']} | {_num(sa['mean_r'], 3)} | "
                      f"{_pct(b_sum['total_return'], 0)} | {_num(b_sum['sharpe'])} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
