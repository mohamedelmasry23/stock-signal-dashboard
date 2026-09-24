"""Command line for the sweep-reversal research harness.

    python3 -m research.sweep.sweepcli --mode donchian_veto \
        --universe constituents:data/universes/sp500_constituents.csv,etfs

    python3 -m research.sweep.sweepcli --mode sweep_only --symbols SPY,QQQ,GLD
    python3 -m research.sweep.sweepcli --mode sweep_only --symbols SPY --sweep

Two families of mode, because the question splits in two:

`donchian_veto` runs the DAILY engine, unmodified, on the existing price
cache. It answers "does the sweep premise improve the long book" without
any new data, any shorting, or any new simulator — and because it is the
real `run_backtest`, its numbers sit directly beside the +164% recorded in
research/README.md.

`sweep_only`, `parallel` and `combined` run the intraday simulator in
`simulate.py`. They are the strategy proper. Until the Databento feed
exists they can only be pointed at `--source daily`, which cannot express
the method — there are no Asia or London sessions inside a daily bar — so
the CLI says so out loud rather than letting a daily number be quoted as
if it were a test of the strategy.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from .params import SENSITIVITY_GRID, SweepParams

DEFAULT_START = dt.date(2016, 1, 1)
DEFAULT_END = dt.date(2026, 8, 11)

MODES = ("donchian_veto", "sweep_only", "parallel", "combined")

DAILY_CAVEAT = (
    "NOTE: --source daily cannot express this strategy. A daily bar has no Asia\n"
    "or London session inside it, and the 4H/1H structure separation the method\n"
    "depends on collapses onto the daily bar itself. Treat any number below as a\n"
    "test of the machinery and of the sweep-and-reclaim PATTERN on daily bars —\n"
    "not as a test of the method. The faithful run needs the 5-minute feed.\n"
)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="research.sweep.sweepcli", description=__doc__)
    p.add_argument("--mode", choices=MODES, default="donchian_veto")
    p.add_argument("--source", choices=("daily", "databento"), default="daily")
    p.add_argument("--symbols", default="", help="comma-separated, for the sweep modes")
    p.add_argument(
        "--universe",
        default="constituents:data/universes/sp500_constituents.csv,etfs",
        help="for --mode donchian_veto; same syntax as app.backtest",
    )
    p.add_argument("--start", type=dt.date.fromisoformat, default=DEFAULT_START)
    p.add_argument("--end", type=dt.date.fromisoformat, default=DEFAULT_END)
    p.add_argument("--equity", type=float, default=100_000.0)
    p.add_argument("--risk", type=float, default=0.01)
    p.add_argument(
        "--windows", default="1,2,3,5,10,20",
        help="veto windows to compare, in bars",
    )
    p.add_argument(
        "--veto-mode", default="skip", choices=("skip", "exit", "both"),
        help="skip the breakout, exit on the sweep, or both",
    )
    p.add_argument(
        "--sweep", action="store_true",
        help="walk SENSITIVITY_GRID one parameter at a time and report shape",
    )
    p.add_argument("--no-bias", action="store_true", help="the no-filter variant")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.mode == "donchian_veto":
        return run_veto(args)
    return run_sweep_modes(args)


# --------------------------------------------------------------------------
# donchian_veto: the daily engine, unmodified
# --------------------------------------------------------------------------
def run_veto(args: argparse.Namespace) -> int:
    from app.backtest.__main__ import resolve_costs, resolve_membership, resolve_universe
    from app.backtest.engine import run_backtest
    from app.backtest.prices import load_prices

    from .donchian_veto import register_veto, veto_config

    schedule = resolve_membership(args.universe, args.start)
    tickers, excluded = resolve_universe(args.universe, args.start)
    if not tickers:
        print(f"[sweep] no tickers resolved from --universe {args.universe!r}")
        return 1
    if excluded:
        print(f"[sweep] excluded futures symbols (roll gaps are not returns): {excluded}")

    cost_bps, slippage_bps = resolve_costs(args.universe, None, None)
    prices = load_prices(
        sorted(set(tickers) | {"SPY"}), args.start, args.end, offline=True, verbose=False
    )
    benchmark = prices.pop("SPY", None)
    print(f"[sweep] {len(prices)} tickers, {args.start} to {args.end}, "
          f"costs {cost_bps}+{slippage_bps} bps/side")

    base_kwargs = dict(
        start=args.start, end=args.end, tickers=tuple(sorted(prices)), benchmark="SPY",
        initial_equity=args.equity, risk_fraction=args.risk,
        cost_bps=cost_bps, slippage_bps=slippage_bps,
    )

    def run(strategy: str):
        # Never saved. See the warning in `veto_config` — a research
        # variant in the backtest table would be served as the dashboard's
        # own headline.
        return run_backtest(prices, veto_config(base_kwargs, strategy), benchmark, membership=schedule)

    rows: List[Tuple[str, object]] = [("donchian (baseline)", run("donchian"))]
    windows = [int(w) for w in args.windows.split(",") if w.strip()]
    for window in windows:
        name = register_veto(window, args.veto_mode)
        rows.append((f"{args.veto_mode}, {window}-bar window", run(name)))

    header = f"{'variant':30} {'total':>9} {'CAGR':>7} {'maxDD':>8} {'Sharpe':>7} {'trades':>7} {'win%':>6} {'PF':>5}"
    print("\n" + header)
    returns = []
    for label, result in rows:
        s = result.summary
        returns.append(s["total_return"])
        print(f"{label:30} {s['total_return'] * 100:8.1f}% {s['cagr'] * 100:6.2f}% "
              f"{s['max_drawdown'] * 100:7.2f}% {s['sharpe']:7.2f} {s['n_trades']:7d} "
              f"{s['win_rate'] * 100:5.1f} {s['profit_factor']:5.2f}")

    from .report import _verdict

    print()
    # The baseline is the yardstick, not one of the settings: the question
    # is whether the veto's EFFECT survives its neighbours, not whether
    # the variants happen to be profitable.
    print(f"  shape across veto windows: {_verdict(returns[1:], baseline=returns[0])}")
    if windows and windows[0] == 1 and abs(returns[1] - returns[0]) < 1e-9:
        print("  parity: a 1-bar window reproduces the baseline exactly, as it must — "
              "a bar cannot both close above the level and close back under it.")
    return 0


# --------------------------------------------------------------------------
# the intraday simulator
# --------------------------------------------------------------------------
def run_sweep_modes(args: argparse.Namespace) -> int:
    from .instruments import resolve
    from .report import (
        format_funnel, format_instruments, format_outcomes, format_params,
        format_sensitivity, format_summary,
    )
    from .simulate import SimConfig, run_simulation
    from .sources import DailyCacheSource, daily_params
    from .strategy import build_context

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("[sweep] --symbols is required for this mode")
        return 1

    if args.source == "databento":
        # The intraday evaluation has its own runner: futures need their own
        # sizing, contract specs and R-denominated reporting, all of which
        # live there rather than being bolted onto the daily path below.
        from .futures_eval import main as futures_main

        return futures_main(["--symbols", ",".join(symbols)])

    print(DAILY_CAVEAT)
    frames = DailyCacheSource().load(symbols, args.start, args.end)
    missing = [s for s in symbols if s not in frames]
    if missing:
        print(f"[sweep] no cached data for: {', '.join(missing)}")
    if not frames:
        return 1

    params = daily_params(use_bias=not args.no_bias)
    config = SimConfig(initial_equity=args.equity, risk_fraction=args.risk)

    if args.sweep:
        rows = []
        for name, values in SENSITIVITY_GRID.items():
            for value in values:
                try:
                    variant = params.replaced(**{name: value})
                except (ValueError, TypeError):
                    continue
                summary = _run_once(frames, variant, config, args.mode)
                rows.append((name, value, summary))
        print(format_sensitivity(rows))
        return 0

    contexts = [build_context(s, f, params) for s, f in frames.items()]
    result = run_simulation(contexts, config)
    print(format_summary(result, title=f"Sweep reversal — {args.mode}"))
    print(format_funnel(result.funnel))
    print(format_outcomes(result.order_outcomes))
    print(format_instruments([config.instrument(s) for s in sorted(frames)]))
    print(format_params(params))
    return 0


def _run_once(frames: Dict[str, "object"], params: SweepParams, config, mode: str) -> dict:
    from .simulate import run_simulation
    from .strategy import build_context

    contexts = [build_context(s, f, params) for s, f in frames.items()]
    if not any(c.setups for c in contexts):
        # An empty run still needs a row in the table: "this setting
        # produced no trades" is a result about the parameter, and
        # dropping it would make a grid look uniformly positive.
        return {"total_return": 0.0, "sharpe": None, "max_drawdown": 0.0,
                "n_trades": 0, "win_rate": None}
    return run_simulation(contexts, config).summary


if __name__ == "__main__":
    sys.exit(main())
