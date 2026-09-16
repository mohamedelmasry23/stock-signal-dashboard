"""
Backtest CLI.

    python -m app.backtest --universe commodities,etfs --start 2016-01-01

Mirrors `python -m app.pipeline`. Unlike the pipeline this never opens
`real.db` or `demo.db` for price data — bars come from a separate cache —
so there is no DEMO_MODE guard and none should be added. Results are
optionally persisted into the app database with `--save` so the dashboard
can display them.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..config import DATA_DIR
from ..pipeline import (
    get_commodity_tickers,
    get_extra_equity_tickers,
    get_index_etf_tickers,
    get_sp500_tickers,
)
from ..signals import RSI_ENTRY_THRESHOLD, RSI_EXIT_THRESHOLD
from .benchmark import buy_and_hold
from .config import (
    DEFAULT_RSI_EXIT_MA_PERIOD,
    DEFAULT_RSI_PERIOD,
    DEFAULT_RSI_TREND_PERIOD,
    DEFAULT_TARGET_POSITIONS,
    BacktestConfig,
)
from .engine import build_calendar, run_backtest
from .membership import MembershipSchedule, load_interval_schedule, load_schedule
from .prices import load_prices
from .report import render_summary, write_artifacts
from .strategies import STRATEGIES

DEFAULT_START = date(2016, 1, 1)

# Futures symbols are excluded from the TRADED universe. A continuous
# front-month series like GC=F contains roll gaps that are contract
# changes, not profit — simulating trading it books each roll as a
# return. Using it to VALUE bullion in the live dashboard stays correct;
# GLD is the tradeable gold proxy.
FUTURES_SUFFIX = "=F"


# The S&P universe for every backtest from 2026-09-14 on:
#   --universe constituents:data/universes/sp500_constituents.csv,etfs
SP500_CONSTITUENTS = "constituents:data/universes/sp500_constituents.csv"

PATH_PREFIXES = ("file:", "events:", "constituents:")
SCHEDULE_PREFIXES = ("events:", "constituents:")


def _paths(spec: str, prefix: str) -> list[str]:
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    return [p[len(prefix):] for p in parts if p.lower().startswith(prefix)]


def resolve_membership(spec: str, start: date) -> Optional[MembershipSchedule]:
    """The schedule behind an `events:<csv>` or `constituents:<csv>`
    universe, or None without one.

    Only one per run: two schedules would each claim to be the index."""
    events, intervals = _paths(spec, "events:"), _paths(spec, "constituents:")
    if len(events) + len(intervals) > 1:
        raise ValueError("a universe can name at most one events: or constituents: file")
    if events:
        return load_schedule(Path(events[0]), start)
    if intervals:
        return load_interval_schedule(Path(intervals[0]), start)
    return None


def has_announcements(spec: str) -> bool:
    """Only an events: file dates changes by announcement; a constituents:
    file switches on effective dates and gives the overlay nothing to trade."""
    return bool(_paths(spec, "events:"))


def resolve_universe(spec: str, start: date = DEFAULT_START) -> tuple[list[str], list[str]]:
    """Returns (tradeable tickers, names of anything excluded).

    Accepts `file:<path>` for a saved point-in-time membership list — the
    survivorship-bias mitigation described in app/backtest/universe.py —
    and `events:<path>` (a dated change file) or `constituents:<path>`
    (membership intervals), either of which contributes every ticker that
    was an index member at any point from `start` on (see
    app/backtest/membership.py). Anything else unrecognised is treated as
    a literal ticker.
    """
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    names = {p.lower() for p in parts if not p.lower().startswith(PATH_PREFIXES)}
    tickers: list[str] = []

    for path in _paths(spec, "file:"):
        from .universe import load_membership

        tickers += load_membership(Path(path))

    schedule = resolve_membership(spec, start)
    if schedule is not None:
        tickers += sorted(schedule.universe)

    if names & {"sp500", "all"}:
        tickers += get_sp500_tickers()
    if names & {"etfs", "all"}:
        tickers += get_extra_equity_tickers()
    if names & {"commodities", "all"}:
        tickers += get_commodity_tickers()
    # The RSI(2) scan universe. Deliberately NOT folded into `etfs`: that
    # name is part of the stored Donchian runs' identity, and quietly
    # widening it would change what `sp500,etfs` means, invalidating the
    # comparison those runs exist for.
    if names & {"index_etfs", "all"}:
        tickers += get_index_etf_tickers()

    unknown = names - {"sp500", "etfs", "commodities", "index_etfs", "all"}
    for name in unknown:
        tickers.append(name.upper())

    excluded = sorted({t for t in tickers if t.endswith(FUTURES_SUFFIX)})
    tradeable = sorted({t for t in tickers if not t.endswith(FUTURES_SUFFIX)})
    return tradeable, excluded


def universe_label(spec: str) -> str:
    """Display form of a `--universe` spec: `file:`, `events:` and `constituents:` parts
    collapse to the file's stem, so a run is stored and shown as
    `sp500_2016,etfs` rather than `file:data/universes/sp500_2016.txt,etfs`.

    The dashboard keys the point-in-time run off this label, so it has to
    survive into the database — see `get_backtest` in app/main.py.
    """
    parts = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        prefix = next((p for p in PATH_PREFIXES if part.lower().startswith(p)), None)
        parts.append(Path(part[len(prefix):]).stem if prefix else part)
    return ",".join(parts)


def resolve_costs(spec: str, cost_bps=None, slippage_bps=None) -> tuple[float, float]:
    """Per-side commission and slippage, defaulted to fit the universe.

    A single blanket rate cannot be right for both a 500-name single-stock
    universe and a handful of penny-spread index funds, and the difference
    is not marginal: costed at the single-stock 10bps per side, the RSI
    basket strategy returned 8.4%; at a rate appropriate to SPY/QQQ/XLF it
    returned 87.4% over the same bars. That one assumption was the largest
    single distortion in the measurement.

    5+5 stays the default everywhere else. It is deliberately NOT applied
    per-ticker: the commodity funds in this universe (PALL, UGA, CPER) are
    far thinner than the index ETFs, so a blanket low rate flatters them —
    which is why the report prints the rate it used.
    """
    liquid = "index_etfs" in {p.strip().lower() for p in spec.split(",")}
    default = 0.75 if liquid else 5.0
    return (
        default if cost_bps is None else cost_bps,
        default if slippage_bps is None else slippage_bps,
    )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m app.backtest", description=__doc__)
    p.add_argument("--start", type=lambda s: date.fromisoformat(s), default=DEFAULT_START)
    p.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date.today())
    p.add_argument("--universe", default="commodities,etfs",
                   help="sp500 | etfs | index_etfs | commodities | all | file:<list> | "
                        "events:<csv> | constituents:<csv> | comma-separated tickers. "
                        f"S&P backtests use {SP500_CONSTITUENTS},etfs")
    p.add_argument("--index-overlay", action="store_true",
                   help="with an events: universe, run twice — membership only, then also "
                        "buying S&P additions / selling removals on the announcement — and "
                        "compare")
    p.add_argument("--strategy", choices=sorted(STRATEGIES), default="donchian",
                   help="which system to measure (default: donchian)")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--equity", type=float, default=100_000.0)
    p.add_argument("--risk", type=float, default=0.01)
    # Left as None so an unset flag can pick up a universe-appropriate
    # default; see `resolve_costs`. Pass either explicitly to override.
    p.add_argument("--cost-bps", type=float, default=None)
    p.add_argument("--slippage-bps", type=float, default=None)
    p.add_argument("--fill", choices=["next_open", "next_close"], default="next_open")
    p.add_argument("--max-positions", type=int, default=20)
    p.add_argument("--max-position-frac", type=float, default=0.20)
    p.add_argument("--sizing", choices=["risk", "equal_weight"], default=None,
                   help="how much to buy. Defaults to whatever the chosen strategy "
                        "is actually traded with: risk-based for Donchian, "
                        "equal-weight for RSI(2)")
    p.add_argument("--target-positions", type=int, default=DEFAULT_TARGET_POSITIONS,
                   help="equal-weight slots: each position targets 1/N of equity")
    p.add_argument("--cash-yield", type=float, default=0.0,
                   help="annual interest on uninvested cash. Matters most for "
                        "strategies that sit in cash; set it equal to --risk-free "
                        "so the curve and the Sharpe denominator agree")
    p.add_argument("--price-mode", choices=["adjusted", "raw"], default="adjusted")
    p.add_argument("--risk-free", type=float, default=0.0,
                   help="annual risk-free rate for Sharpe/Sortino. The default of 0 "
                        "flatters any strategy that sits in cash — which RSI(2) does "
                        "most of the time, so set it deliberately when comparing")
    # RSI(2) overrides, for sensitivity work. Defaults are Connors' published
    # values; changing them to maximise a result turns an out-of-sample
    # parameter into a fitted one, so vary them to check the shape of the
    # surface, not to pick a winner.
    p.add_argument("--rsi-period", type=int, default=DEFAULT_RSI_PERIOD)
    p.add_argument("--rsi-entry", type=float, default=RSI_ENTRY_THRESHOLD)
    p.add_argument("--rsi-exit", type=float, default=RSI_EXIT_THRESHOLD)
    p.add_argument("--rsi-trend", type=int, default=DEFAULT_RSI_TREND_PERIOD)
    p.add_argument("--rsi-exit-ma", type=int, default=DEFAULT_RSI_EXIT_MA_PERIOD)
    p.add_argument("--no-fractional", action="store_true")
    p.add_argument("--min-notional", type=float, default=100.0)
    p.add_argument("--both-variants", action="store_true",
                   help="also run with a hard ATR stop and print the comparison")
    p.add_argument("--atr-stop", action="store_true", help="use a hard ATR stop in the single run")
    p.add_argument("--offline", action="store_true", help="fail rather than hit the network")
    p.add_argument("--refresh", action="store_true", help="ignore the cache and refetch")
    p.add_argument("--save", action="store_true", help="persist results for the dashboard")
    p.add_argument("--out", type=Path, default=DATA_DIR / "backtests")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    schedule = resolve_membership(args.universe, args.start)
    if args.index_overlay and not has_announcements(args.universe):
        print("[backtest] --index-overlay needs an events: universe to know what was announced "
              "(a constituents: file has effective dates only).")
        return 2
    if args.index_overlay and args.both_variants:
        print("[backtest] --index-overlay and --both-variants each make a two-run comparison; "
              "pick one.")
        return 2
    if args.index_overlay and args.save:
        # A membership-only run is saved as its own series (the API keys it
        # off the `sp500_events` label and draws it beside the headline).
        # The overlay run has no such place, and it measures a system the
        # live pipeline can't run — there is no announcement feed.
        print("[backtest] --save is refused with --index-overlay: the dashboard has no place "
              "for the announcement-trading run, and the live pipeline can't trade it. Run "
              "without --index-overlay to save the membership line. Artifacts are still "
              "written under --out.")
        return 2

    tickers, excluded = resolve_universe(args.universe, args.start)
    universe = universe_label(args.universe)
    # Unless overridden, each strategy is measured with the allocation
    # model it is actually traded with — see `sizing_mode` in config.py.
    sizing = args.sizing or STRATEGIES[args.strategy].default_sizing
    cost_bps, slippage_bps = resolve_costs(args.universe, args.cost_bps, args.slippage_bps)
    if not tickers:
        print(f"[backtest] No tickers resolved from --universe {args.universe!r}.")
        return 1
    if excluded:
        print(f"[backtest] Excluded futures symbols (roll gaps are not returns): {excluded}")

    print(
        f"[backtest] {STRATEGIES[args.strategy].label} — "
        f"{len(tickers)} tickers, {args.start} to {args.end}"
    )
    needed = sorted(set(tickers) | {args.benchmark})
    prices = load_prices(
        needed, args.start, args.end,
        refresh=args.refresh, offline=args.offline, price_mode=args.price_mode,
    )

    benchmark_frame = prices.pop(args.benchmark, None)
    if benchmark_frame is None:
        print(f"[backtest] WARNING: no data for benchmark {args.benchmark}; comparison omitted.")
    dropped = len(tickers) - len(prices)
    if dropped:
        print(f"[backtest] {dropped} ticker(s) returned no data.")
    if schedule is not None:
        members = schedule.universe
        print(f"[backtest] membership: {len(members)} companies were S&P 500 members at some "
              f"point since {args.start}; {len(members - set(prices))} have no price data. "
              f"{len(schedule.switches)} membership changes"
              f"{' (announcement-dated)' if has_announcements(args.universe) else ' (effective-dated)'}.")
        for warning in schedule.warnings:
            print(f"[backtest]   note: {warning}")

    def build(use_stop: bool, overlay: bool = False) -> BacktestConfig:
        return BacktestConfig(
            start=args.start, end=args.end, tickers=tuple(sorted(prices)),
            benchmark=args.benchmark, initial_equity=args.equity, risk_fraction=args.risk,
            strategy=args.strategy,
            sizing_mode=sizing, target_positions=args.target_positions,
            cash_yield_annual=args.cash_yield,
            rsi_period=args.rsi_period, rsi_entry_threshold=args.rsi_entry,
            rsi_exit_threshold=args.rsi_exit, rsi_trend_period=args.rsi_trend,
            rsi_exit_ma_period=args.rsi_exit_ma,
            use_atr_stop=use_stop, cost_bps=cost_bps, slippage_bps=slippage_bps,
            fill=args.fill, max_positions=args.max_positions,
            max_position_fraction=args.max_position_frac, min_notional=args.min_notional,
            allow_fractional=not args.no_fractional, price_mode=args.price_mode,
            risk_free_annual=args.risk_free, index_overlay=overlay,
            label=f"{args.strategy} {universe} {'with ATR stop' if use_stop else 'faithful'}"
                  + (" + index overlay" if overlay else ""),
        )

    # (use_stop, overlay) per run.
    if args.index_overlay:
        variants = [(args.atr_stop, False), (args.atr_stop, True)]
    elif args.both_variants:
        variants = [(False, False), (True, False)]
    else:
        variants = [(args.atr_stop, False)]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []

    for use_stop, overlay in variants:
        config = build(use_stop, overlay)
        result = run_backtest(prices, config, benchmark_frame, membership=schedule)

        if benchmark_frame is not None:
            from . import metrics

            calendar = build_calendar(prices, config, benchmark_frame)
            bench = buy_and_hold(benchmark_frame, calendar, config)
            if bench is not None:
                result.benchmark_equity = bench
                result.benchmark_summary = metrics.summarize(
                    bench, [], risk_free_annual=config.risk_free_annual
                )

        print()
        print(render_summary(result, universe, dropped))
        # Both overlay runs are the same variant, so the directory name has
        # to say which is which or the second overwrites the first.
        suffix = "-index_overlay" if overlay else ("-membership" if schedule is not None else "")
        out = write_artifacts(result, args.out / f"{stamp}-{config.variant}{suffix}", universe)
        print(f"\n[backtest] artifacts: {out}")
        results.append(result)

        if args.save:
            from .store import save_result

            run_id = save_result(result, universe_label=universe)
            print(f"[backtest] saved as run {run_id} (visible in the dashboard)")

    if len(results) == 2:
        first, second = results
        title, columns = (
            ("OVERLAY COMPARISON — what trading the announcements adds", ("membership", "+ overlay"))
            if args.index_overlay
            else ("VARIANT COMPARISON — what the missing stop-loss costs", ("no stop", "ATR stop"))
        )
        print("\n" + "=" * 60)
        print(title)
        print("=" * 60)
        for label, key, fmt in (
            ("Total return", "total_return", "pct"),
            ("CAGR", "cagr", "pct"),
            ("Max drawdown", "max_drawdown", "pct"),
            ("Sharpe", "sharpe", "num"),
            ("Losses worse than 1R", "pct_losses_worse_than_1r", "pct"),
            ("Number of trades", "n_trades", "int"),
        ):
            a, b = first.summary.get(key), second.summary.get(key)
            render = (
                (lambda v: "—" if v is None else f"{v * 100:.2f}%") if fmt == "pct"
                else (lambda v: "—" if v is None else f"{v:,.2f}") if fmt == "num"
                else (lambda v: str(v))
            )
            print(f"  {label:<26}{render(a):>14}{render(b):>14}")
        print(f"  {'':<26}{columns[0]:>14}{columns[1]:>14}")
        bench = first.benchmark_summary or {}
        if bench:
            print(f"  Benchmark ({args.benchmark}) total return "
                  f"{bench.get('total_return', 0) * 100:.2f}%, Sharpe {bench.get('sharpe') or 0:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
