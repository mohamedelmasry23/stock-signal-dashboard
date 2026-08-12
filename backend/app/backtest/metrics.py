"""
Performance statistics over an equity curve and a trade list.

Pure functions on pandas/numpy — no config, no engine, no I/O — so each
one can be checked against a hand-computed value.

Every function returns None rather than inf or NaN on degenerate input
(zero variance, a single point, no trades). A Sharpe of `inf` printed in
a report reads as a spectacular result rather than as "this series never
moved", and that distinction is the whole point of measuring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .engine import Trade

TRADING_DAYS = 252


def _clean(value: float) -> Optional[float]:
    return float(value) if np.isfinite(value) else None


def total_return(equity: pd.Series) -> Optional[float]:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return None
    return _clean(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series) -> Optional[float]:
    """Compound annual growth rate.

    Uses calendar days rather than bars/252: it doesn't drift with market
    holidays, and it matches the window a reader would state out loud.
    """
    if len(equity) < 2 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return None
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return None
    return _clean((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Fractional decline from the running peak. Zero at every new high."""
    return equity / equity.cummax() - 1.0


def max_drawdown(equity: pd.Series) -> Optional[float]:
    if len(equity) < 2:
        return None
    return _clean(drawdown_series(equity).min())


def drawdown_detail(equity: pd.Series) -> dict:
    """Worst drawdown plus when it happened and how long it lasted.

    The depth alone understates the experience: a 25% drawdown recovered
    in a month and one that grinds on for two years are very different
    things to actually live through, and the second is what makes people
    abandon a system.
    """
    if len(equity) < 2:
        return {"max_drawdown": None, "peak_date": None, "trough_date": None,
                "recovery_date": None, "longest_underwater_days": None}

    dd = drawdown_series(equity)
    trough = dd.idxmin()
    peak = equity.loc[:trough].idxmax()

    after = equity.loc[trough:]
    recovered = after[after >= equity.loc[peak]]
    recovery = recovered.index[0] if len(recovered) else None

    underwater = dd < 0
    longest = current = 0
    run_start = None
    for stamp, is_under in underwater.items():
        if is_under:
            run_start = stamp if run_start is None else run_start
            current = (stamp - run_start).days
            longest = max(longest, current)
        else:
            run_start = None

    return {
        "max_drawdown": _clean(dd.min()),
        "peak_date": peak.date().isoformat(),
        "trough_date": trough.date().isoformat(),
        "recovery_date": recovery.date().isoformat() if recovery is not None else None,
        "longest_underwater_days": longest,
    }


def sharpe(equity: pd.Series, risk_free_annual: float = 0.0) -> Optional[float]:
    """Annualised Sharpe of daily returns.

    A note that matters for reading the result: with risk_free_annual=0 a
    strategy that sits in cash gets credited with a risk-free zero rather
    than the real yield on that cash. Over 2022-2024 that flatters a
    low-exposure trend system considerably, which is why the report prints
    time-in-market immediately next to this number.
    """
    returns = equity.pct_change().dropna()
    if len(returns) < 2:
        return None

    rf_daily = (1.0 + risk_free_annual) ** (1.0 / TRADING_DAYS) - 1.0
    excess = returns - rf_daily
    sd = excess.std(ddof=1)
    if not np.isfinite(sd) or sd == 0:
        return None
    return _clean(excess.mean() / sd * np.sqrt(TRADING_DAYS))


def sortino(equity: pd.Series, risk_free_annual: float = 0.0) -> Optional[float]:
    """Like Sharpe but penalising only downside deviation."""
    returns = equity.pct_change().dropna()
    if len(returns) < 2:
        return None

    rf_daily = (1.0 + risk_free_annual) ** (1.0 / TRADING_DAYS) - 1.0
    excess = returns - rf_daily
    downside = np.sqrt((excess.clip(upper=0) ** 2).mean())
    if not np.isfinite(downside) or downside == 0:
        return None
    return _clean(excess.mean() / downside * np.sqrt(TRADING_DAYS))


def volatility(equity: pd.Series) -> Optional[float]:
    returns = equity.pct_change().dropna()
    if len(returns) < 2:
        return None
    sd = returns.std(ddof=1)
    return _clean(sd * np.sqrt(TRADING_DAYS)) if np.isfinite(sd) else None


def trade_stats(trades: Sequence["Trade"]) -> dict:
    """Win rate, average win/loss, and how often realised losses exceeded
    the risk the position was sized for.

    `pct_losses_worse_than_1R` is the headline of this block, not a
    footnote. The strategy sizes every position so a move of one stop
    distance costs `risk_fraction` of equity — but its only exit is the
    Donchian breakdown, with no stop order behind it. So the intended risk
    is a claim, and this is the measurement of that claim. A high number
    means the sizing model is describing a system that doesn't exist.
    """
    empty = {
        "n_trades": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
        "avg_win_pct": None, "avg_loss_pct": None, "avg_win_r": None,
        "avg_loss_r": None, "profit_factor": None, "expectancy": None,
        "avg_bars_held": None, "median_bars_held": None,
        "pct_losses_worse_than_1r": None, "worst_loss_r": None,
    }
    if not trades:
        return empty

    pnl = np.array([t.pnl for t in trades], dtype=float)
    pnl_pct = np.array([t.pnl_pct for t in trades], dtype=float)
    r_multiples = np.array([t.r_multiple for t in trades], dtype=float)
    bars = np.array([t.bars_held for t in trades], dtype=float)

    wins, losses = pnl > 0, pnl < 0
    gross_win = pnl[wins].sum()
    gross_loss = abs(pnl[losses].sum())

    # Only losing trades can exceed the intended risk, and r_multiple is
    # negative for them, so "worse than 1R" is r < -1.
    losing_r = r_multiples[losses]
    losing_r = losing_r[np.isfinite(losing_r)]

    return {
        "n_trades": len(trades),
        "win_rate": _clean(wins.sum() / len(trades)),
        "avg_win": _clean(pnl[wins].mean()) if wins.any() else None,
        "avg_loss": _clean(pnl[losses].mean()) if losses.any() else None,
        "avg_win_pct": _clean(pnl_pct[wins].mean()) if wins.any() else None,
        "avg_loss_pct": _clean(pnl_pct[losses].mean()) if losses.any() else None,
        "avg_win_r": _clean(r_multiples[wins].mean()) if wins.any() else None,
        "avg_loss_r": _clean(r_multiples[losses].mean()) if losses.any() else None,
        "profit_factor": _clean(gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": _clean(pnl.mean()),
        "avg_bars_held": _clean(bars.mean()),
        "median_bars_held": _clean(np.median(bars)),
        "pct_losses_worse_than_1r": (
            _clean((losing_r < -1.0).sum() / len(losing_r)) if len(losing_r) else None
        ),
        "worst_loss_r": _clean(losing_r.min()) if len(losing_r) else None,
    }


def summarize(
    equity: pd.Series,
    trades: Sequence["Trade"],
    positions_count: Optional[pd.Series] = None,
    exposure: Optional[pd.Series] = None,
    risk_free_annual: float = 0.0,
) -> dict:
    """Everything the report and the API need, in one flat dict."""
    out = {
        "start": equity.index[0].date().isoformat() if len(equity) else None,
        "end": equity.index[-1].date().isoformat() if len(equity) else None,
        "bars": int(len(equity)),
        "initial_equity": _clean(equity.iloc[0]) if len(equity) else None,
        "final_equity": _clean(equity.iloc[-1]) if len(equity) else None,
        "total_return": total_return(equity),
        "cagr": cagr(equity),
        "volatility": volatility(equity),
        "sharpe": sharpe(equity, risk_free_annual),
        "sortino": sortino(equity, risk_free_annual),
    }
    out.update(drawdown_detail(equity))
    out.update(trade_stats(trades))

    # Reported together on purpose: a strategy in cash 80% of the time has
    # cash drag that time_in_market alone hides, and a Sharpe computed at
    # a 0% risk-free rate quietly rewards exactly that.
    out["time_in_market"] = (
        _clean((positions_count > 0).mean()) if positions_count is not None and len(positions_count) else None
    )
    out["avg_gross_exposure"] = (
        _clean(exposure.mean()) if exposure is not None and len(exposure) else None
    )
    return out
