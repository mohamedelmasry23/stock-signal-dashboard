"""
Which strategy the engine is measuring.

The engine used to call `compute_signal_frame` directly, which meant it
could only ever measure Donchian. This module is the seam: it turns a
strategy into data — a signal function, its parameters, a ranking key and
a warm-up length — so the same simulation can run any of them.

## The contract

A strategy's frame must carry the four columns `app/signals.py` documents
(`EntrySignal`, `ExitSignal`, `ATR`, `StopDistance`) plus one more that
only matters here:

    Rank    float   higher is a better candidate

## Why Rank is not a detail

Capital is finite: `max_positions` caps how many of a day's signals can be
taken. Measured over 2016-2026, the RSI strategy produces a median of 9
simultaneous entry signals and as many as 217 on one day — against 20
slots. On most signalling days the ranking rule, not the entry rule,
decides which trades the backtest actually takes.

So `Rank` must order candidates exactly the way the live dashboard orders
them, or the backtest measures a system nobody is running:

    donchian   ATRs above the breakout level   ==  /api/entry-candidates
    rsi        RSI points below the trigger    ==  /api/rsi/entry-candidates

`test_ranking_matches_the_live_api` pins both.

Strategies are added here, not in the engine. Nothing in this module may
import the engine — the dependency runs one way, the same rule
`app/backtest/config.py` follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import pandas as pd

from ..signals import compute_rsi_signal_frame, compute_signal_frame

if TYPE_CHECKING:  # config imports nothing from here, so this stays a hint
    from .config import BacktestConfig


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    #: Builds the signal frame, including its `Rank` column.
    frame: Callable[[pd.DataFrame, "BacktestConfig"], pd.DataFrame]
    #: Bars consumed before this strategy can produce its first signal.
    warmup: Callable[["BacktestConfig"], int]
    #: What `Rank` means, for the report.
    rank_description: str
    #: How much to buy. Trend following sizes off volatility so each
    #: position risks the same; short-term mean reversion allocates a flat
    #: share of the account. Applying the wrong one is not a rounding
    #: error — see `sizing_mode` in config.py. The CLI can still override.
    default_sizing: str


def _donchian_frame(df: pd.DataFrame, config: "BacktestConfig") -> pd.DataFrame:
    out = compute_signal_frame(
        df,
        n_entry=config.n_entry,
        m_exit=config.m_exit,
        atr_period=config.atr_period,
        k_atr=config.k_atr,
    )
    # How many ATRs above the breakout level the close printed. Identical
    # to the formula in /api/entry-candidates.
    #
    # A NaN or infinite Rank is what disqualifies a candidate downstream,
    # which subsumes the guards the engine used to apply inline: a missing
    # close, a missing level, or a zero ATR all land here as non-finite.
    out["Rank"] = (out["Close"] - out["DonchianHigh_N"]) / out["ATR"]
    return out


def _rsi_frame(df: pd.DataFrame, config: "BacktestConfig") -> pd.DataFrame:
    out = compute_rsi_signal_frame(
        df,
        rsi_period=config.rsi_period,
        entry_threshold=config.rsi_entry_threshold,
        exit_threshold=config.rsi_exit_threshold,
        trend_period=config.rsi_trend_period,
        exit_ma_period=config.rsi_exit_ma_period,
        atr_period=config.atr_period,
        k_atr=config.k_atr,
    )
    # RSI points below the entry trigger. Ranking by depth descending is
    # the same ordering as RSI ascending, which is what
    # /api/rsi/entry-candidates does.
    out["Rank"] = out["OversoldDepth"]
    return out


def _donchian_warmup(config: "BacktestConfig") -> int:
    return max(config.n_entry, config.m_exit, config.atr_period) + 1


def _rsi_warmup(config: "BacktestConfig") -> int:
    # The 200-day trend filter dominates: RSI(2) itself is ready in two
    # bars, but no entry can fire until the long average exists.
    return max(
        config.rsi_trend_period,
        config.rsi_exit_ma_period,
        config.rsi_period,
        config.atr_period,
    ) + 1


STRATEGIES: dict[str, Strategy] = {
    "donchian": Strategy(
        name="donchian",
        label="Donchian breakout",
        frame=_donchian_frame,
        warmup=_donchian_warmup,
        rank_description="ATRs above the breakout level",
        default_sizing="risk",
    ),
    "rsi": Strategy(
        name="rsi",
        label="RSI(2) pullback",
        frame=_rsi_frame,
        warmup=_rsi_warmup,
        rank_description="RSI points below the entry trigger",
        # Risk-based, despite equal weight being how this strategy is
        # usually described — because it was measured and equal weight lost.
        # Same bars, same costs: risk sizing returned 8.4% at Sharpe 0.14,
        # equal weight -0.3% at Sharpe 0.04, with a deeper drawdown.
        #
        # The reason is that equal weight allocates by DOLLARS, so it takes
        # its largest risk in the most volatile instruments — exactly the
        # ones ATR sizing shrinks. Switching to it also barely moved
        # deployment (24.7% -> 28.1% average exposure), because what limits
        # this strategy is how rarely it gets a signal, not how much it
        # buys when it does. `--sizing equal_weight` is still there to
        # re-check that.
        default_sizing="risk",
    ),
}


def get_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(
            f"Unknown strategy {name!r}. Available: {', '.join(sorted(STRATEGIES))}"
        ) from None
