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

from .config import BacktestConfig
from .engine import BacktestResult


def _pct(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _money(value: Optional[float]) -> str:
    return "—" if value is None else f"${value:,.2f}"


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

    if "sp500" in universe_label:
        notes.append(
            "SURVIVORSHIP BIAS: the universe is today's S&P 500 membership, so this "
            "tests only companies that both survived and stayed in the index. Real-time "
            "results would have been worse — treat the absolute return as an optimistic "
            "ceiling, not an estimate."
        )
    if dropped:
        notes.append(f"{dropped} ticker(s) returned no data and were silently excluded.")

    if not cfg.use_atr_stop:
        notes.append(
            "No stop-loss: the only exit is the Donchian breakdown, so a position sized "
            "to risk "
            f"{cfg.risk_fraction * 100:.1f}% can lose more than that on a gap. See "
            "'losses worse than 1R' below."
        )

    years = (result.equity.index[-1] - result.equity.index[0]).days / 365.25
    if years < 5:
        notes.append(
            f"Only {years:.1f} years of data. A 55-day breakout system trades rarely, so "
            "this is a small sample — the result is weak evidence either way."
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
        f"Variant: {cfg.variant}"
        + ("  (Donchian exit only — no stop-loss)" if not cfg.use_atr_stop else f"  (with {cfg.k_atr:g}x ATR stop)"),
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
