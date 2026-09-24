"""The full evaluation of the sweep strategy on Databento futures bars.

    ../.venv/bin/python -m research.sweep.futures_eval --symbols NQ

One command, every number that goes into research/README.md, so the
write-up can be regenerated rather than trusted. It runs:

  1. the default rules, with and without the 4H bias filter;
  2. the default run split long vs short and by calendar year;
  3. the same rules under doubled and zero friction;
  4. a bootstrap confidence interval on the mean R per trade;
  5. SENSITIVITY_GRID, one parameter at a time, with a shape verdict.

## Results are reported in R first

R — P&L as a multiple of the risk each trade was sized for — is the unit
this strategy's premise is stated in ("skip anything under 1.5R"), and it
is independent of account size and contract multiplier. The dollar curve
at 1% risk is reported too, for comparison with the daily systems.

## Why the sizing caps are lifted

`SimConfig`'s equity-fraction caps were written for a cash stock book. A
single NQ contract controls roughly $400k of index; a 20%-of-$100k cap
would allow 0.05 of one, so every trade would risk about 0.01% of the
account and the result would say nothing. Futures are margined, and a
futures trader sizes by risk. So here sizing is purely risk-based, and
fractional contracts stand in for Micro NQ (1/10 the multiplier), which
makes 1% risk on a $100k account a realistic size to actually trade.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .instruments import resolve
from .params import SENSITIVITY_GRID, SweepParams
from .simulate import SimConfig, SimResult, run_simulation
from .sources import DatabentoSource
from .strategy import build_context

_BARS: Dict[str, pd.DataFrame] = {}


def futures_config(symbols: Sequence[str], **overrides) -> SimConfig:
    base = dict(
        max_position_fraction=100.0,
        max_gross=100.0,
        # One position per instrument is already enforced by the simulator;
        # this only stops two instruments' positions starving each other.
        max_positions=len(symbols),
        instruments={s: resolve(s) for s in symbols},
    )
    base.update(overrides)
    return SimConfig(**base)


def base_params(symbol: str, **overrides) -> SweepParams:
    return SweepParams(tick_size=resolve(symbol).tick_size, **overrides)


def _init(symbols: Sequence[str]) -> None:
    _BARS.update(DatabentoSource().load(symbols))


def run(params_by_symbol: Dict[str, SweepParams], config: SimConfig) -> SimResult:
    contexts = [build_context(s, _BARS[s], p) for s, p in params_by_symbol.items()]
    return run_simulation(contexts, config)


def r_stats(trades: pd.DataFrame) -> dict:
    """Everything about a trade list, in R."""
    if trades.empty:
        return {"n": 0, "win": None, "mean_r": None, "total_r": 0.0, "pf": None,
                "max_dd_r": 0.0, "avg_win_r": None, "avg_loss_r": None}
    r = trades["r_multiple"].to_numpy(dtype=float)
    wins, losses = r[r > 0], r[r < 0]
    curve = np.concatenate([[0.0], np.cumsum(r)])
    return {
        "n": len(r),
        "win": float((r > 0).mean()),
        "mean_r": float(r.mean()),
        "total_r": float(r.sum()),
        "pf": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() else None,
        "max_dd_r": float((curve - np.maximum.accumulate(curve)).min()),
        "avg_win_r": float(wins.mean()) if len(wins) else None,
        "avg_loss_r": float(losses.mean()) if len(losses) else None,
    }


def bootstrap_mean(r: np.ndarray, n: int = 20_000, seed: int = 7) -> Tuple[float, float, float]:
    """95% interval on mean R, and the share of resamples at or below zero.

    Resamples whole trades. That ignores any serial dependence between
    trades, which makes the interval if anything too narrow — so an
    interval that still straddles zero is a firm "not established".
    """
    if len(r) < 2:
        return (float("nan"),) * 3
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n, len(r)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)), float((means <= 0).mean())


def _grid_job(args) -> Tuple[str, object, dict]:
    name, value, symbols = args
    params = {}
    for s in symbols:
        try:
            params[s] = base_params(s, **{name: value})
        except (ValueError, TypeError):
            return name, value, {}
    result = run(params, futures_config(symbols))
    stats = r_stats(result.trades_frame())
    stats["total_return"] = result.summary.get("total_return")
    stats["max_drawdown"] = result.summary.get("max_drawdown")
    return name, value, stats


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------
def _f(x, spec="{:+.2f}", none="—"):
    return none if x is None or (isinstance(x, float) and np.isnan(x)) else spec.format(x)


def row(label: str, s: dict, extra: Optional[dict] = None) -> str:
    extra = extra or {}
    return (
        f"| {label} | {s['n']} | {_f(s['win'], '{:.1%}')} | {_f(s['mean_r'], '{:+.3f}')} | "
        f"{_f(s['total_r'], '{:+.1f}')} | {_f(s['pf'], '{:.2f}')} | {_f(s['max_dd_r'], '{:.1f}')} | "
        f"{_f(extra.get('total_return'), '{:+.1%}')} | {_f(extra.get('max_drawdown'), '{:.1%}')} |"
    )


HEADER = (
    "| Run | Trades | Win | Mean R | Total R | PF | Max DD (R) | Return @1% | Max DD @1% |\n"
    "|---|---|---|---|---|---|---|---|---|"
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="research.sweep.futures_eval")
    parser.add_argument("--symbols", default="NQ")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-grid", action="store_true")
    parser.add_argument("--save-trades", default="", help="write baseline trades to this CSV")
    args = parser.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    _init(symbols)
    for s in symbols:
        bars = _BARS[s]
        print(f"[data] {s}: {len(bars):,} 5-minute bars, {bars.index[0]} -> {bars.index[-1]}")
        print(f"[spec] {resolve(s)}")

    config = futures_config(symbols)
    params = {s: base_params(s) for s in symbols}

    # 1. baseline and no-bias -------------------------------------------
    base = run(params, config)
    trades = base.trades_frame()
    no_bias = run({s: base_params(s, use_bias=False) for s in symbols}, config)
    nb_trades = no_bias.trades_frame()

    print("\n## Headline\n")
    print(HEADER)
    print(row("Default rules (4H bias on)", r_stats(trades), base.summary))
    print(row("No bias filter", r_stats(nb_trades), no_bias.summary))

    print("\n## Funnel (default rules)\n")
    for k, v in base.funnel.items():
        print(f"- {k}: {v}")
    print(f"- order outcomes: {base.order_outcomes}")
    print(f"- exit reasons: {trades['exit_reason'].value_counts().to_dict() if len(trades) else {}}")

    # 2. splits ---------------------------------------------------------
    print("\n## Long vs short (default rules)\n")
    print(HEADER)
    for direction, label in (("bullish", "Long (swept a low)"), ("bearish", "Short (swept a high)")):
        print(row(label, r_stats(trades[trades["direction"] == direction])))
    print("\n## By year (default rules)\n")
    print(HEADER)
    years = pd.to_datetime(trades["entry_ts"], utc=True).dt.tz_convert("America/New_York").dt.year
    for year in sorted(years.unique()):
        print(row(str(year), r_stats(trades[years == year])))

    # 3. costs ----------------------------------------------------------
    print("\n## Friction\n")
    print(HEADER)
    for label, mult in (("Zero costs", 0.0), ("Default costs", 1.0), ("Double costs", 2.0)):
        specs = {
            s: replace(resolve(s), commission_per_unit=resolve(s).commission_per_unit * mult,
                       slippage_ticks=resolve(s).slippage_ticks * mult)
            for s in symbols
        }
        result = run(params, futures_config(symbols, instruments=specs))
        print(row(label, r_stats(result.trades_frame()), result.summary))

    lenient = run(params, futures_config(symbols, check_stop_on_entry_bar=False))
    print(row("No entry-bar exits at all", r_stats(lenient.trades_frame()), lenient.summary))

    # 4. confidence -----------------------------------------------------
    print("\n## Is the mean R distinguishable from zero?\n")
    for label, frame in (("Default rules", trades), ("No bias filter", nb_trades)):
        r = frame["r_multiple"].to_numpy(dtype=float)
        lo, hi, p = bootstrap_mean(r)
        print(f"- {label}: mean {r.mean():+.3f}R, 95% CI [{lo:+.3f}, {hi:+.3f}], "
              f"share of resamples <= 0: {p:.1%}  (n={len(r)})")

    if args.save_trades:
        trades.to_csv(args.save_trades, index=False)
        print(f"\n[saved] baseline trades -> {args.save_trades}")

    # 5. sensitivity ----------------------------------------------------
    if not args.no_grid:
        from .report import _verdict

        jobs = [(name, value, symbols) for name, values in SENSITIVITY_GRID.items() for value in values]
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_init, initargs=(symbols,)) as pool:
            results = list(pool.map(_grid_job, jobs))

        print("\n## Sensitivity — one parameter at a time, all else default\n")
        baseline_total = r_stats(trades)["total_r"]
        grouped: Dict[str, List[Tuple[object, dict]]] = {}
        for name, value, stats in results:
            grouped.setdefault(name, []).append((value, stats))
        for name, entries in grouped.items():
            print(f"### {name}\n")
            print(HEADER)
            totals = []
            for value, stats in entries:
                if not stats:
                    print(f"| {value} | invalid | | | | | | | |")
                    continue
                totals.append(stats["total_r"])
                print(row(str(value), stats, stats))
            print(f"\nShape: {_verdict(totals)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
