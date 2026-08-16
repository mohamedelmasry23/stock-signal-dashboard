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

import numpy as np
import pandas as pd

from .account import DEFAULT_EQUITY


# RSI(2) pullback thresholds. Module constants rather than bare defaults in
# the signature because the API ranks candidates by their distance below
# the entry trigger and the UI explains both numbers in prose — three
# places that must quote the same figure the calculation actually used.
RSI_ENTRY_THRESHOLD = 5.0
RSI_EXIT_THRESHOLD = 70.0


def true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder's true range: the widest of today's range, and today's high
    or low measured against yesterday's close. Shared so that every
    strategy sizes off the identical volatility measure — two strategies
    disagreeing about a ticker's ATR would make their position sizes, and
    any comparison between them, meaningless."""
    prev_close = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI with Wilder's smoothing, which is an EMA with alpha = 1/period.

    `adjust=False` matters: it gives the recursive formulation Wilder
    defined and every charting package reproduces. With `adjust=True`
    pandas returns a re-weighted expanding mean instead, which disagrees
    with every published RSI value — and at period 2 the gap is large,
    because the first few bars dominate.

    Two degenerate cases the bare formula gets wrong:

    * **No losses in the window.** `avg_loss` is 0, the ratio is infinite
      and RSI is 100. That is correct and is left alone.
    * **No movement at all.** Both averages are 0, giving 0/0. A halted or
      suspended ticker has an *undefined* RSI, not a maximal one, so it
      gets the neutral 50. Returning 100 would print an exit signal every
      single day for anything that stopped trading.

    Bars before the window fills stay NaN, which compares False against
    every threshold — so warm-up can never signal.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rsi = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    warm = avg_gain.notna() & avg_loss.notna()
    flat = warm & (avg_gain == 0) & (avg_loss == 0)
    return rsi.mask(flat, 50.0).where(warm, np.nan)


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


def compute_rsi_signal_frame(
    df: pd.DataFrame,
    rsi_period: int = 2,
    entry_threshold: float = RSI_ENTRY_THRESHOLD,
    exit_threshold: float = RSI_EXIT_THRESHOLD,
    trend_period: int = 200,
    exit_ma_period: int = 5,
    atr_period: int = 20,
    k_atr: float = 2.0,
    risk_fraction: float = 0.01,
    equity: float = DEFAULT_EQUITY,
) -> pd.DataFrame:
    """
    RSI(2) pullback — buy a short, sharp dip inside an established uptrend.

    Connors' short-term mean reversion, and the deliberate opposite of the
    Donchian system above. Donchian buys a price making new highs; this
    buys one that just fell hard. That contrast is the reason it's worth
    having: two trend-following strategies agreeing tells you nothing,
    whereas these two agreeing is genuinely rare information.

        Entry   Close > SMA(200)  and  RSI(2) < 5
        Exit    RSI(2) > 70       or   Close > SMA(5)

    The 200-day filter is what separates "a dip" from "a collapse". Without
    it this strategy buys every falling knife in the universe, which is how
    RSI mean reversion earns its reputation.

    Expect a very different shape from the Donchian run: many short trades,
    a high win rate, and long stretches entirely in cash. It cannot be
    judged on total return alone against a system that is ~98% invested —
    exposure and Sharpe are the honest comparison.

    ## Timing

    Unlike the Donchian levels, nothing here is shifted. RSI, both moving
    averages and the ATR all include the bar whose close produced the
    signal, which is correct because the fill happens on the *next* bar —
    see the module docstring. Shifting them as well would delay every
    entry by a day and quietly measure a different strategy.

    The `Shares` column carries the same caveat as `compute_signal_frame`:
    it's here so the function is testable standalone, and real sizing comes
    from app/account.py via the API.
    """
    out = df.copy()

    out["RSI"] = wilder_rsi(out["Close"], rsi_period)
    out["SMA_Fast"] = out["Close"].rolling(exit_ma_period).mean()
    out["SMA_Trend"] = out["Close"].rolling(trend_period).mean()

    out["TR"] = true_range(out)
    out["ATR"] = out["TR"].rolling(atr_period).mean()
    out["StopDistance"] = out["ATR"] * k_atr

    # NaN compares False against every threshold, so a ticker still inside
    # any warm-up window simply produces no signal — which is what a
    # too-short history should mean. A recent IPO has no 200-day average
    # and so is never eligible, exactly as intended.
    out["EntrySignal"] = (out["Close"] > out["SMA_Trend"]) & (out["RSI"] < entry_threshold)
    out["ExitSignal"] = (out["RSI"] > exit_threshold) | (out["Close"] > out["SMA_Fast"])

    # How far below the entry trigger the RSI printed, in RSI points. Plays
    # the same role `breakout_strength` does for Donchian — the ranking key
    # for "best candidates" — and is only meaningful on a bar that actually
    # signalled, so it's clipped at zero rather than going negative.
    out["OversoldDepth"] = (entry_threshold - out["RSI"]).clip(lower=0.0)

    risk_dollars = risk_fraction * equity
    out["Shares"] = risk_dollars / out["StopDistance"]

    return out
