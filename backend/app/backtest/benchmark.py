"""
Buy-and-hold benchmark.

The comparison only means something if both sides are measured the same
way: identical calendar, identical cost treatment, identical price
adjustment. Running an adjusted (total-return) strategy against a
price-only benchmark hands the strategy roughly 1.5%/yr of free alpha
purely from the benchmark's missing dividends — which would be enough to
manufacture an edge out of nothing.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .config import BacktestConfig


def buy_and_hold(
    frame: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    config: BacktestConfig,
) -> Optional[pd.Series]:
    """Equity curve for putting everything into one ticker on the first
    tradeable bar and holding to the end.

    Entry happens on `calendar[1]`, matching the earliest bar on which the
    strategy could possibly have filled anything — the strategy cannot act
    on bar 0 because it has no prior bar to have decided from.
    """
    aligned = frame.reindex(calendar)
    fill_col = "Open" if config.fill == "next_open" else "Close"
    prices = aligned[fill_col].to_numpy(dtype=float)
    closes = aligned["Close"].to_numpy(dtype=float)

    tradeable = np.flatnonzero(np.isfinite(prices) & (prices > 0))
    tradeable = tradeable[tradeable >= 1]
    if len(tradeable) == 0:
        return None

    entry_i = int(tradeable[0])
    cost_rate = config.total_cost_rate
    entry_price = prices[entry_i] * (1 + cost_rate)
    shares = config.initial_equity / entry_price

    equity = np.empty(len(calendar), dtype=float)
    equity[:entry_i] = config.initial_equity

    last_known = prices[entry_i]
    for i in range(entry_i, len(calendar)):
        if np.isfinite(closes[i]):
            last_known = closes[i]
        equity[i] = shares * last_known

    # Charge the exit once, at the end — the position is closed on the
    # final bar just as the strategy's open positions are, so neither side
    # gets a free untaxed exit the other pays for.
    equity[-1] -= shares * last_known * cost_rate

    return pd.Series(equity, index=calendar, name="benchmark")
