"""The sweep premise applied to the long side, with no shorting at all.

This is the cheap half of the question. The dashboard's Donchian system
buys a close above the prior 55-day high. The sweep premise says some of
those breaks are stop-hunts rather than trends — price reaching past an
obvious level to fill resting orders, then failing. If that is true of
this universe, a breakout at a level that has RECENTLY been swept and
rejected should be worse than average, and skipping it should improve the
long book without any short leg, any new data feed, or any new engine.

## Why this needs no engine changes

Both variants are pure transforms of the signal frame:

    skip   EntrySignal &= no sweep of this level in the last `window` bars
    exit   ExitSignal  |= this bar swept the level and closed back under

`app/backtest/engine.py` consumes `EntrySignal`, `ExitSignal`, `ATR`,
`StopDistance` and `Rank` and asks no further questions, so a strategy
that supplies those runs unmodified. That matters beyond tidiness: the
engine, its costs, its sizing, its membership handling and its look-ahead
guard are all the measured ones, so a result here is directly comparable
to the +164% in research/README.md rather than nearly comparable.

## Causality

A sweep is detectable at the close of the bar that does it: the high went
past the level and the close came back under. `compute_signal_frame`
already `.shift(1)`s `DonchianHigh_N`, so the level being compared against
is yesterday's — the same level the breakout rule uses, and one that
contains none of today's own trading. The rolling veto window therefore
looks only backwards, and the bar it ends on is one whose close is already
known when the decision is made.

Note that a bar can never be both a sweep and an entry: an entry closes
ABOVE the level, a sweep closes at or below it. Including the current bar
in the window is consequently free, and excluding it would be an
arbitrary extra lag.

## Registration

Strategies are registered by name into `app.backtest.strategies.STRATEGIES`
at call time rather than edited into that file. Each parameter combination
gets its own name, so a sensitivity sweep can register a dozen variants in
one process without any of them mutating another's settings — which a
module-level "current window" global would do, silently, and only when the
runs were executed in the wrong order.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from app.backtest.strategies import STRATEGIES, Strategy
from app.signals import compute_signal_frame

VetoMode = Literal["skip", "exit", "both"]

DEFAULT_WINDOW = 5
DEFAULT_PENETRATION_ATR = 0.0


def swept_the_level(frame: pd.DataFrame, penetration_atr: float = 0.0) -> pd.Series:
    """True on bars that traded above the 55-day high and closed back under.

    `penetration_atr` scales the "at least one tick beyond" requirement to
    something meaningful on daily bars across a universe whose prices span
    three orders of magnitude: one cent past the level is noise on a $900
    stock and a real probe on a $4 one. Zero reproduces the plain
    definition, which is the honest default because it adds no parameter.
    """
    level = frame["DonchianHigh_N"]
    beyond = frame["High"] > level + penetration_atr * frame["ATR"]
    rejected = frame["Close"] <= level
    return (beyond & rejected).fillna(False)


def failed_breakdown(frame: pd.DataFrame, penetration_atr: float = 0.0) -> pd.Series:
    """The mirror: traded below the 20-day low and closed back above it.

    Not used by either veto variant, but it is the same premise applied to
    the exit level, and it is what a future "don't sell into a sweep of
    the 20-day low" test would need. Kept here so that test does not
    re-derive it slightly differently.
    """
    level = frame["DonchianLow_M"]
    beyond = frame["Low"] < level - penetration_atr * frame["ATR"]
    rejected = frame["Close"] >= level
    return (beyond & rejected).fillna(False)


def apply_veto(
    frame: pd.DataFrame,
    window: int,
    mode: VetoMode,
    penetration_atr: float = 0.0,
) -> pd.DataFrame:
    """Transform a Donchian frame according to the veto rule."""
    out = frame.copy()
    sweeps = swept_the_level(out, penetration_atr)

    if mode in ("skip", "both"):
        # `min_periods=1` so the warm-up does not veto everything before
        # the window fills; closed="right" is pandas' default and is the
        # backward-looking window this needs.
        recent = sweeps.rolling(window=window, min_periods=1).max().astype(bool)
        out["EntrySignal"] = out["EntrySignal"].fillna(False) & ~recent

    if mode in ("exit", "both"):
        out["ExitSignal"] = out["ExitSignal"].fillna(False) | sweeps

    out["Swept"] = sweeps
    return out


def register_veto(
    window: int = DEFAULT_WINDOW,
    mode: VetoMode = "skip",
    penetration_atr: float = DEFAULT_PENETRATION_ATR,
) -> str:
    """Register one veto variant and return the name to pass as --strategy.

    Idempotent: registering the same combination twice returns the same
    name and leaves the registry unchanged, so a sweep that revisits a
    setting does not accumulate duplicates.
    """
    if window < 1:
        raise ValueError("window must be at least 1 bar")
    if mode not in ("skip", "exit", "both"):
        raise ValueError(f"Unknown veto mode {mode!r}")

    suffix = f"{mode}{window}"
    if penetration_atr:
        suffix += f"p{penetration_atr:g}".replace(".", "_")
    name = f"donchian_veto_{suffix}"
    if name in STRATEGIES:
        return name

    def frame(df: pd.DataFrame, config) -> pd.DataFrame:
        base = compute_signal_frame(
            df,
            n_entry=config.n_entry,
            m_exit=config.m_exit,
            atr_period=config.atr_period,
            k_atr=config.k_atr,
        )
        out = apply_veto(base, window, mode, penetration_atr)
        # Identical to `_donchian_frame`'s ranking, deliberately: the veto
        # changes WHICH breakouts are taken, never how the survivors are
        # ordered. Changing both at once would make the result
        # unattributable to either.
        out["Rank"] = (out["Close"] - out["DonchianHigh_N"]) / out["ATR"]
        return out

    def warmup(config) -> int:
        return max(config.n_entry, config.m_exit, config.atr_period) + window + 1

    STRATEGIES[name] = Strategy(
        name=name,
        label=f"Donchian + sweep veto ({mode}, {window} bars)",
        frame=frame,
        warmup=warmup,
        rank_description="ATRs above the breakout level",
        default_sizing="risk",
    )
    return name


def veto_config(base_kwargs: dict, strategy: str):
    """A `BacktestConfig` naming a registered research strategy.

    `app/backtest/config.py` validates `strategy` against the literal pair
    `("donchian", "rsi")` rather than against the registry, because config
    may not import strategies — the dependency runs one way, and that rule
    is what keeps the registry free to import config for typing. So a
    research strategy cannot pass that check, and this builds the config
    with a valid name and then sets the real one past the frozen
    dataclass's guard.

    The alternatives were worse. Widening the check means editing
    `app/backtest/`, which this experiment is meant to leave untouched so
    the dashboard's measured numbers stay measured. Temporarily swapping
    `STRATEGIES["donchian"]` would run the right code under the wrong
    name, and every artifact and log line would then claim to be the
    baseline.

    What is being bypassed is a spelling check, not a safety property: the
    engine resolves the name through `get_strategy`, which raises loudly
    if it is not registered. The one thing this must never reach is
    `store.save_result` — a research variant written into the backtest
    table would be served by `/api/backtest` as if the dashboard ran it.
    `run_veto` below never saves, and nothing here should either.
    """
    from app.backtest.config import BacktestConfig

    if strategy not in STRATEGIES:
        raise ValueError(f"{strategy!r} is not registered — call register_veto first")

    config = BacktestConfig(**{**base_kwargs, "strategy": "donchian"})
    object.__setattr__(config, "strategy", strategy)
    return config
