"""
Persist a completed run into the app database so the dashboard can show it.

Only this module bridges the backtest package and the app's database.
Everything upstream of it — config, engine, metrics, prices — stays pure,
which is what keeps the whole simulation testable without a database.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import func

from ..database import Base, SessionLocal, engine, sync_new_columns
from ..models import BacktestEquityPoint, BacktestRun, BacktestTrade
from .engine import BacktestResult

# A decade of daily bars is ~2,500 points — far more than a dashboard
# chart can resolve, and it would be re-read from the database on every
# request. Downsampling keeps the shape (including the drawdown extremes,
# which are preserved explicitly below) at a fraction of the rows.
MAX_POINTS = 400


def _downsample(equity: pd.Series, benchmark: Optional[pd.Series]) -> pd.DatetimeIndex:
    """Evenly spaced points, plus the first, last, peak and trough.

    Naive striding can step straight over the maximum drawdown's trough,
    which would draw a chart that disagrees with the headline number
    printed beside it.
    """
    if len(equity) <= MAX_POINTS:
        return equity.index

    step = max(1, len(equity) // MAX_POINTS)
    keep = set(equity.index[::step])
    keep.update({equity.index[0], equity.index[-1]})

    drawdown = equity / equity.cummax() - 1.0
    trough = drawdown.idxmin()
    keep.update({trough, equity.loc[:trough].idxmax()})

    return pd.DatetimeIndex(sorted(keep))


def _clean(value) -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def save_result(result: BacktestResult, universe_label: str = "") -> int:
    """Writes the run, its downsampled curve and its trades. Returns run id."""
    Base.metadata.create_all(bind=engine)
    sync_new_columns()

    db = SessionLocal()
    try:
        config = asdict(result.config)
        config["start"] = result.config.start.isoformat()
        config["end"] = result.config.end.isoformat()
        config["tickers"] = list(result.config.tickers)
        config["notes"] = list(result.config.notes)

        run = BacktestRun(
            label=result.config.label or result.config.variant,
            universe=universe_label,
            strategy=result.config.strategy,
            variant=result.config.variant,
            start_date=result.equity.index[0].date(),
            end_date=result.equity.index[-1].date(),
            config_json=json.dumps(config),
            summary_json=json.dumps(result.summary),
            benchmark_summary_json=(
                json.dumps(result.benchmark_summary) if result.benchmark_summary else None
            ),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Replace any previous run of the same strategy+variant+universe:
        # the dashboard shows the latest, and keeping every historical run
        # would grow the database without anything ever reading them.
        #
        # Older rows predate the `strategy` column and are NULL, which is
        # why the comparison coalesces to "donchian".
        stale = (
            db.query(BacktestRun)
            .filter(
                func.coalesce(BacktestRun.strategy, "donchian") == run.strategy,
                BacktestRun.variant == run.variant,
                BacktestRun.universe == run.universe,
                BacktestRun.id != run.id,
            )
            .all()
        )
        for old in stale:
            db.query(BacktestEquityPoint).filter(BacktestEquityPoint.run_id == old.id).delete()
            db.query(BacktestTrade).filter(BacktestTrade.run_id == old.id).delete()
            db.delete(old)

        for stamp in _downsample(result.equity, result.benchmark_equity):
            bench = None
            if result.benchmark_equity is not None and stamp in result.benchmark_equity.index:
                bench = _clean(result.benchmark_equity.loc[stamp])
            exposure = None
            if result.exposure is not None and stamp in result.exposure.index:
                exposure = _clean(result.exposure.loc[stamp])
            db.add(
                BacktestEquityPoint(
                    run_id=run.id,
                    as_of_date=stamp.date(),
                    strategy=_clean(result.equity.loc[stamp]),
                    benchmark=bench,
                    exposure=exposure,
                )
            )

        for trade in result.trades:
            db.add(
                BacktestTrade(
                    run_id=run.id,
                    ticker=trade.ticker,
                    entry_date=trade.entry_date,
                    entry_price=_clean(trade.entry_price),
                    shares=_clean(trade.shares),
                    exit_date=trade.exit_date,
                    exit_price=_clean(trade.exit_price),
                    exit_reason=trade.exit_reason,
                    pnl=_clean(trade.pnl),
                    pnl_pct=_clean(trade.pnl_pct),
                    r_multiple=_clean(trade.r_multiple),
                    bars_held=trade.bars_held,
                )
            )

        db.commit()
        return run.id
    finally:
        db.close()
