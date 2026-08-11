import pandas as pd

from .account import DEFAULT_EQUITY


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

    prev_close = out["Close"].shift(1)
    tr1 = out["High"] - out["Low"]
    tr2 = (out["High"] - prev_close).abs()
    tr3 = (out["Low"] - prev_close).abs()
    out["TR"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    out["ATR"] = out["TR"].rolling(atr_period).mean()
    out["StopDistance"] = out["ATR"] * k_atr

    risk_dollars = risk_fraction * equity
    out["Shares"] = risk_dollars / out["StopDistance"]

    return out
