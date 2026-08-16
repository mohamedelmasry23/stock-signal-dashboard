"""
Rendering a finished run: a terminal summary, a markdown file, and CSVs.

The caveats block is not decoration. A backtest number without its
assumptions attached is worse than no number, because it gets quoted
later stripped of the context that made it honest.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import pandas as pd

from ..config import is_point_in_time
from .config import BacktestConfig
from .engine import BacktestResult
from .strategies import get_strategy


def _pct(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _money(value: Optional[float]) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _entry_dates(result: BacktestResult) -> str:
    """Distinct entry dates, printed beside the trade count.

    Trades opened on the same day share one market move, so they are not
    independent observations. Mean-reversion strategies enter in bursts on
    market-wide selloffs, which means a four-figure trade count can rest on
    a couple of hundred genuinely separate events. Printing both makes the
    gap visible instead of leaving the trade count to imply a sample size
    it doesn't have.
    """
    if not result.trades:
        return "0"
    return f"{len({t.entry_date for t in result.trades}):,}"


def _total_costs(result: BacktestResult) -> float:
    """Every commission and slippage charge the run paid.

    Turnover is the variable that decides whether a short-holding strategy
    survives contact with reality: the same per-trade cost that is
    negligible over 437 trades is ruinous over ten times as many. This puts
    the total in front of the reader rather than leaving it implied by the
    bps assumption in the caveats.
    """
    return sum(t.entry_cost + t.exit_cost for t in result.trades)


def caveats(result: BacktestResult, universe_label: str, dropped: int = 0) -> list[str]:
    """What a reader must know before quoting any number above."""
    cfg = result.config
    notes = [
        f"Costs assumed: {cfg.cost_bps:.0f} bps commission + {cfg.slippage_bps:.0f} bps "
        f"slippage, charged on entry and exit.",
        f"Fills modelled at the {cfg.fill.replace('_', ' ')} after the signal — a signal "
        f"from a bar's close was not knowable until after that close.",
        f"Prices are {'total-return (dividends reinvested)' if cfg.price_mode == 'adjusted' else 'price-only (dividends EXCLUDED)'}; "
        f"the benchmark is treated identically.",
    ]

    if is_point_in_time(universe_label):
        notes.append(
            "POINT-IN-TIME UNIVERSE: membership is frozen at the snapshot date, so "
            "companies removed from the index later are back in and companies added "
            "later are absent. That removes the survivor filter and introduces the "
            "mirror bias — treat the absolute return as a pessimistic floor, not an "
            "estimate."
        )
    elif "sp500" in universe_label:
        notes.append(
            "SURVIVORSHIP BIAS: the universe is today's S&P 500 membership, so this "
            "tests only companies that both survived and stayed in the index. Real-time "
            "results would have been worse — treat the absolute return as an optimistic "
            "ceiling, not an estimate."
        )
    if dropped:
        notes.append(f"{dropped} ticker(s) returned no data and were silently excluded.")

    if not cfg.use_atr_stop:
        exit_rule = (
            "the RSI bounce" if cfg.strategy == "rsi" else "the Donchian breakdown"
        )
        notes.append(
            f"No stop-loss: the only exit is {exit_rule}, so a position sized to risk "
            f"{cfg.risk_fraction * 100:.1f}% can lose more than that on a gap. See "
            "'losses worse than 1R' below."
        )

    if cfg.strategy == "rsi":
        notes.append(
            "Entries cluster: this strategy buys dips, and dips arrive market-wide. "
            "Trades opened on the same day share one move and are not independent "
            "observations — read 'distinct entry dates' above, not the trade count, "
            "as the sample size."
        )
        if cfg.risk_free_annual == 0:
            notes.append(
                "Idle cash earns 0% here, and Sharpe subtracts a 0% risk-free rate. For "
                "a strategy that spends much of its time in cash these pull the result "
                "in OPPOSITE directions — understating the return, overstating the "
                "risk-adjusted figure. Set --risk-free deliberately before comparing "
                "this against a fully-invested system."
            )

    years = (result.equity.index[-1] - result.equity.index[0]).days / 365.25
    if years < 5:
        notes.append(
            f"Only {years:.1f} years of data — a small sample, so the result is weak "
            "evidence either way."
        )
    return notes


def render_summary(
    result: BacktestResult,
    universe_label: str = "",
    dropped: int = 0,
) -> str:
    """Terminal/markdown summary comparing strategy against benchmark."""
    s = result.summary
    b = result.benchmark_summary or {}
    cfg = result.config

    rows = [
        ("Total return", _pct(s.get("total_return")), _pct(b.get("total_return"))),
        ("CAGR", _pct(s.get("cagr")), _pct(b.get("cagr"))),
        ("Max drawdown", _pct(s.get("max_drawdown")), _pct(b.get("max_drawdown"))),
        ("Volatility (ann.)", _pct(s.get("volatility")), _pct(b.get("volatility"))),
        ("Sharpe", _num(s.get("sharpe")), _num(b.get("sharpe"))),
        ("Sortino", _num(s.get("sortino")), _num(b.get("sortino"))),
        ("Final equity", _money(s.get("final_equity")), _money(b.get("final_equity"))),
    ]

    width = max(len(r[0]) for r in rows) + 2
    lines = [
        f"# Backtest — {cfg.label or cfg.variant}",
        "",
        f"{s.get('start')} to {s.get('end')}  ({s.get('bars')} bars, "
        f"universe: {universe_label or 'custom'})",
        f"Strategy: {get_strategy(cfg.strategy).label}",
        f"Sizing: "
        + (
            f"equal weight, 1/{cfg.target_positions} of equity per position"
            if cfg.sizing_mode == "equal_weight"
            else f"risk-based, {cfg.risk_fraction:.1%} per position "
                 f"(capped at {cfg.max_position_fraction:.0%})"
        )
        + (
            f" · cash earns {cfg.cash_yield_annual:.1%}"
            if cfg.cash_yield_annual
            else " · cash earns 0%"
        ),
        f"Variant: {cfg.variant}"
        + (
            "  (strategy's own exit only — no stop-loss)"
            if not cfg.use_atr_stop
            else f"  (with {cfg.k_atr:g}x ATR stop)"
        ),
        "",
        f"{'':<{width}}{'STRATEGY':>14}{'BENCHMARK':>14}",
    ]
    for label, strat, bench in rows:
        lines.append(f"{label:<{width}}{strat:>14}{bench:>14}")

    lines += [
        "",
        "## Trades",
        f"{'Number of trades':<{width}}{s.get('n_trades', 0):>14}",
        f"{'Win rate':<{width}}{_pct(s.get('win_rate')):>14}",
        f"{'Profit factor':<{width}}{_num(s.get('profit_factor')):>14}",
        f"{'Avg win / loss':<{width}}{_money(s.get('avg_win')) + ' / ' + _money(s.get('avg_loss')):>14}",
        f"{'Avg win / loss (R)':<{width}}{_num(s.get('avg_win_r')) + ' / ' + _num(s.get('avg_loss_r')):>14}",
        f"{'Avg bars held':<{width}}{_num(s.get('avg_bars_held'), 1):>14}",
        f"{'Distinct entry dates':<{width}}{_entry_dates(result):>14}",
        f"{'Costs paid':<{width}}{_money(_total_costs(result)):>14}",
        "",
        "## Risk model reality check",
        f"{'Losses worse than 1R':<{width}}{_pct(s.get('pct_losses_worse_than_1r')):>14}",
        f"{'Worst loss (R)':<{width}}{_num(s.get('worst_loss_r')):>14}",
        "",
        "## Exposure",
        f"{'Time in market':<{width}}{_pct(s.get('time_in_market')):>14}",
        f"{'Avg gross exposure':<{width}}{_pct(s.get('avg_gross_exposure')):>14}",
        f"{'Signals skipped (capital)':<{width}}{result.skipped_for_capital:>14}",
        "",
        "## Drawdown detail",
        f"  Peak {s.get('peak_date')} → trough {s.get('trough_date')} "
        f"→ recovery {s.get('recovery_date') or 'never'}",
        f"  Longest underwater: {s.get('longest_underwater_days')} days",
        "",
        "## Caveats",
    ]
    lines += [f"  - {note}" for note in caveats(result, universe_label, dropped)]
    return "\n".join(lines)


def write_artifacts(result: BacktestResult, out_dir: Path, universe_label: str = "") -> Path:
    """Writes equity.csv, trades.csv and summary.md; returns the directory."""
    out_dir.mkdir(parents=True, exist_ok=True)

    equity = pd.DataFrame({"equity": result.equity, "cash": result.cash,
                           "exposure": result.exposure, "positions": result.positions_count})
    if result.benchmark_equity is not None:
        equity["benchmark"] = result.benchmark_equity
    equity.to_csv(out_dir / "equity.csv", index_label="date")

    with (out_dir / "trades.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "ticker", "entry_date", "entry_price", "shares", "exit_date", "exit_price",
            "exit_reason", "pnl", "pnl_pct", "r_multiple", "bars_held",
        ])
        for t in result.trades:
            writer.writerow([
                t.ticker, t.entry_date, f"{t.entry_price:.4f}", f"{t.shares:.6f}",
                t.exit_date, f"{t.exit_price:.4f}", t.exit_reason,
                f"{t.pnl:.2f}", f"{t.pnl_pct:.6f}", f"{t.r_multiple:.4f}", t.bars_held,
            ])

    (out_dir / "summary.md").write_text(render_summary(result, universe_label))
    return out_dir
