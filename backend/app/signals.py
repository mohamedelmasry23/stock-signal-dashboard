"""
The strategies themselves.

Every strategy in here is a pure function of one ticker's OHLC frame, and
every one of them is called by three consumers: the daily pipeline, the
demo seeder, and (for Donchian) the backtest engine. That shared call is
the point — a strategy reimplemented per consumer stops describing the
system it's meant to be measuring the moment either copy changes.

Each returns the same four load-bearing columns, whatever indicators it
used to get there:

    EntrySignal   bool   buy on the next bar
    ExitSignal    bool   sell on the next bar
    ATR           float  volatility, for stops and for comparing strategies
    StopDistance  float  what one unit of risk costs — position sizing
                         divides into this, so a strategy that can't
                         produce it can't be sized

## Signal timing

Signals are computed from a bar's close and filled on the *next* bar, so
an indicator that includes its own bar is not look-ahead. The Donchian
levels are the exception and are `.shift(1)`ed here, because a breakout
level containing today's own high could never be broken. Nothing else in
this module shifts, and the backtest engine must not shift again — see
the d/x split in app/backtest/engine.py.
"""

import pandas as pd

from .account import DEFAULT_EQUITY


def true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder's true range: the widest of today's range, and today's high
    or low measured against yesterday's close."""
    prev_close = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def compute_signal_frame(
    df: pd.DataFrame,
    n_entry: int = 55,
    m_exit: int = 20,
    atr_period: int = 20,
    k_atr: float = 2.0,
    risk_fraction: float = 0.01,
    equity: float = DEFAULT_EQUITY,
) -> pd.DataFrame:
    """
    Donchian channel breakout + ATR-based stop distance and position sizing.

    This is the same logic as the original single-ticker (GLD) script in
    the project brief, factored out unchanged so the daily pipeline and
    the demo seeder both call the identical calculation — no redesign, no
    drift between the two.

    Expects `df` indexed by date with columns: Open, High, Low, Close, Volume.
    Returns the same frame with signal columns appended.

    Note on the `Shares` column: it's computed here from the `equity`
    default only so this function stays self-contained and testable on a
    bare DataFrame. Real sizing comes from your actual account equity,
    resolved in app/account.py and applied by both the pipeline (after its
    run) and the API (on every read). Don't read `Shares` off this frame
    and treat it as the real position size — go through the API.
    """
    out = df.copy()

    out["DonchianHigh_N"] = out["High"].rolling(n_entry).max().shift(1)
    out["DonchianLow_M"] = out["Low"].rolling(m_exit).min().shift(1)

    out["EntrySignal"] = out["Close"] > out["DonchianHigh_N"]
    out["ExitSignal"] = out["Close"] < out["DonchianLow_M"]

    out["TR"] = true_range(out)
    out["ATR"] = out["TR"].rolling(atr_period).mean()
    out["StopDistance"] = out["ATR"] * k_atr

    risk_dollars = risk_fraction * equity
    out["Shares"] = risk_dollars / out["StopDistance"]

    return out
