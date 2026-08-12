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

from ..config import DATA_DIR
from ..pipeline import get_commodity_tickers, get_extra_equity_tickers, get_sp500_tickers
from .benchmark import buy_and_hold
from .config import BacktestConfig
from .engine import build_calendar, run_backtest
from .prices import load_prices
from .report import render_summary, write_artifacts

# Futures symbols are excluded from the TRADED universe. A continuous
# front-month series like GC=F contains roll gaps that are contract
# changes, not profit — simulating trading it books each roll as a
# return. Using it to VALUE bullion in the live dashboard stays correct;
# GLD is the tradeable gold proxy.
FUTURES_SUFFIX = "=F"


def resolve_universe(spec: str) -> tuple[list[str], list[str]]:
    """Returns (tradeable tickers, names of anything excluded)."""
    names = {p.strip().lower() for p in spec.split(",") if p.strip()}
    tickers: list[str] = []

    if names & {"sp500", "all"}:
        tickers += get_sp500_tickers()
    if names & {"etfs", "all"}:
        tickers += get_extra_equity_tickers()
    if names & {"commodities", "all"}:
        tickers += get_commodity_tickers()

    unknown = names - {"sp500", "etfs", "commodities", "all"}
    for name in unknown:
        tickers.append(name.upper())

    excluded = sorted({t for t in tickers if t.endswith(FUTURES_SUFFIX)})
    tradeable = sorted({t for t in tickers if not t.endswith(FUTURES_SUFFIX)})
    return tradeable, excluded


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m app.backtest", description=__doc__)
    p.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2016, 1, 1))
    p.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date.today())
    p.add_argument("--universe", default="commodities,etfs",
                   help="sp500 | etfs | commodities | all | comma-separated tickers")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--equity", type=float, default=100_000.0)
    p.add_argument("--risk", type=float, default=0.01)
    p.add_argument("--cost-bps", type=float, default=5.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--fill", choices=["next_open", "next_close"], default="next_open")
    p.add_argument("--max-positions", type=int, default=20)
    p.add_argument("--max-position-frac", type=float, default=0.20)
    p.add_argument("--price-mode", choices=["adjusted", "raw"], default="adjusted")
    p.add_argument("--risk-free", type=float, default=0.0)
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

    tickers, excluded = resolve_universe(args.universe)
    if not tickers:
        print(f"[backtest] No tickers resolved from --universe {args.universe!r}.")
        return 1
    if excluded:
        print(f"[backtest] Excluded futures symbols (roll gaps are not returns): {excluded}")

    print(f"[backtest] {len(tickers)} tickers, {args.start} to {args.end}")
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

    def build(use_stop: bool) -> BacktestConfig:
        return BacktestConfig(
            start=args.start, end=args.end, tickers=tuple(sorted(prices)),
            benchmark=args.benchmark, initial_equity=args.equity, risk_fraction=args.risk,
            use_atr_stop=use_stop, cost_bps=args.cost_bps, slippage_bps=args.slippage_bps,
            fill=args.fill, max_positions=args.max_positions,
            max_position_fraction=args.max_position_frac, min_notional=args.min_notional,
            allow_fractional=not args.no_fractional, price_mode=args.price_mode,
            risk_free_annual=args.risk_free,
            label=f"{args.universe} {'with ATR stop' if use_stop else 'faithful'}",
        )

    variants = [False, True] if args.both_variants else [args.atr_stop]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []

    for use_stop in variants:
        config = build(use_stop)
        result = run_backtest(prices, config, benchmark_frame)

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
        print(render_summary(result, args.universe, dropped))
        out = write_artifacts(result, args.out / f"{stamp}-{config.variant}", args.universe)
        print(f"\n[backtest] artifacts: {out}")
        results.append(result)

        if args.save:
            from .store import save_result

            run_id = save_result(result, universe_label=args.universe)
            print(f"[backtest] saved as run {run_id} (visible in the dashboard)")

    if len(results) == 2:
        faithful, stopped = results
        print("\n" + "=" * 60)
        print("VARIANT COMPARISON — what the missing stop-loss costs")
        print("=" * 60)
        for label, key, fmt in (
            ("Total return", "total_return", "pct"),
            ("Max drawdown", "max_drawdown", "pct"),
            ("Sharpe", "sharpe", "num"),
            ("Losses worse than 1R", "pct_losses_worse_than_1r", "pct"),
            ("Number of trades", "n_trades", "int"),
        ):
            a, b = faithful.summary.get(key), stopped.summary.get(key)
            render = (
                (lambda v: "—" if v is None else f"{v * 100:.2f}%") if fmt == "pct"
                else (lambda v: "—" if v is None else f"{v:,.2f}") if fmt == "num"
                else (lambda v: str(v))
            )
            print(f"  {label:<26}{render(a):>14}{render(b):>14}")
        print(f"  {'':<26}{'no stop':>14}{'ATR stop':>14}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
