"""Three short rules on the Donchian 55-day high / 20-day low, as code.

Each rule is a pure function of ONE ticker's signal frame (the output of
`app.signals.compute_signal_frame`, so the levels are the dashboard's own)
and returns five columns:

    ShortEntry   bool   short at the NEXT bar's open           (decision bar)
    ShortStop    float  initial stop, a price above entry      (decision bar)
    ShortTarget  float  take-profit price below entry, or NaN  (decision bar)
    ShortRank    float  higher = better candidate              (decision bar)
    ShortTrail   float  stop to trail down to, or NaN          (EXECUTION bar)

Everything but `ShortTrail` is read on the decision bar, and uses only bars
up to and including it. `ShortTrail` is read on the execution bar and is
already shifted: it is `DonchianHigh_N`, the prior 55 bars' high, which is
known before that bar opens. Mixing the two conventions up is the
look-ahead this module is most exposed to, which is why they are named.

## Where the prose needed a decision

The strategies were described in words, and several words do not pin down
a rule. Each decision below is a parameter, and the sensitivity runs move
it, because a result that depends on one reading is not a result.

S1 "fails to make a new 55-day high": no bar in the last `stall_bars`
   traded above its own prior 55-day high. Zero disables the filter.
S1 "the exact moment the price breaks": the close CROSSES below the prior
   20-day low. A plain "close below" would fire every day of a slide.
S1 exit: the spec gives only a trailing 55-day-high stop, so there is no
   target — the position is held until stopped.
S2 "fails within 1 to 3 days": the close comes back under the broken line
   within `fail_window` bars of the breakout bar. The breakout bar itself
   closing back under is day 0 and counts; `fail_window=0` is same-day only.
S2 "the historical 55-day high line": the level as it stood on the
   breakout day, frozen. The rolling line moves up with the breakout's own
   high, and comparing against that would make failure nearly impossible.
S3 "severe downtrend": a close crossed below the 20-day low within
   `max_age` bars, and price is at least `far_atr` ATRs below the 55-day high.
S3 "toward the previous 20-day low line": the bar's high comes within
   `touch_atr` ATRs of the line that broke, and closes back under it.
S3 "low-volume rally": average volume over the rally bars is below the
   average of the 20 bars that ended at the leg's low.
S3 "bearish candlestick reversal": a bearish engulfing bar or a shooting
   star (upper wick at least twice the body and at least half the range).
S3 target: not given in the spec. Taken as the low the rally started from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

import numpy as np
import pandas as pd

TICK = 0.01
OUTPUT_COLUMNS = ("ShortEntry", "ShortStop", "ShortTarget", "ShortRank", "ShortTrail")


@dataclass(frozen=True)
class BreakdownParams:
    stall_bars: int = 20


@dataclass(frozen=True)
class FakeoutParams:
    fail_window: int = 3
    #: Stop placement above the failed breakout's peak: a fixed price
    #: buffer, or a fraction of ATR when `buffer_atr` is set.
    buffer: float = TICK
    buffer_atr: float = 0.0


@dataclass(frozen=True)
class PullbackParams:
    far_atr: float = 3.0
    touch_atr: float = 0.25
    max_age: int = 20
    use_volume: bool = True


def _empty(index: pd.Index) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ShortEntry": np.zeros(len(index), dtype=bool),
            "ShortStop": np.full(len(index), np.nan),
            "ShortTarget": np.full(len(index), np.nan),
            "ShortRank": np.full(len(index), np.nan),
            "ShortTrail": np.full(len(index), np.nan),
        },
        index=index,
    )


# --------------------------------------------------------------------------
# S1 — the macro 20-day breakdown
# --------------------------------------------------------------------------
def breakdown(frame: pd.DataFrame, params: BreakdownParams = BreakdownParams()) -> pd.DataFrame:
    close, high = frame["Close"], frame["High"]
    low20, high55 = frame["DonchianLow_M"], frame["DonchianHigh_N"]

    crossed = (close < low20) & (close.shift(1) >= low20.shift(1))
    if params.stall_bars > 0:
        new_high = (high > high55).astype(float)
        recent = new_high.rolling(params.stall_bars, min_periods=1).max() > 0
        entry = crossed & ~recent
    else:
        entry = crossed

    out = _empty(frame.index)
    out["ShortEntry"] = entry.fillna(False).to_numpy(dtype=bool)
    # The 55-day high INCLUDING the decision bar: exactly the line the
    # execution bar will see as DonchianHigh_N, so the initial stop and
    # the first trailing value agree.
    out["ShortStop"] = high.rolling(55).max().to_numpy()
    out["ShortRank"] = ((low20 - close) / frame["ATR"]).to_numpy()
    out["ShortTrail"] = high55.to_numpy()
    return out


# --------------------------------------------------------------------------
# S2 — the 55-day fakeout (Turtle Soup)
# --------------------------------------------------------------------------
def fakeout(frame: pd.DataFrame, params: FakeoutParams = FakeoutParams()) -> pd.DataFrame:
    high = frame["High"].to_numpy(float)
    close = frame["Close"].to_numpy(float)
    line = frame["DonchianHigh_N"].to_numpy(float)
    atr = frame["ATR"].to_numpy(float)
    low20 = frame["Low"].rolling(20).min().to_numpy(float)

    out = _empty(frame.index)
    entry = out["ShortEntry"].to_numpy().copy()
    stop = out["ShortStop"].to_numpy().copy()
    target = out["ShortTarget"].to_numpy().copy()
    rank = out["ShortRank"].to_numpy().copy()

    active = False
    level = peak = 0.0
    start = 0
    for t in range(len(close)):
        if not np.isfinite(close[t]):
            continue
        if not active and np.isfinite(line[t]) and high[t] > line[t]:
            active, level, peak, start = True, line[t], high[t], t
        elif active:
            peak = max(peak, high[t])

        if not active:
            continue
        if close[t] < level:
            buffer = params.buffer_atr * atr[t] if params.buffer_atr else params.buffer
            s, tg = peak + buffer, low20[t]
            if np.isfinite(s) and np.isfinite(tg) and s > close[t] > tg:
                entry[t], stop[t], target[t] = True, s, tg
                rank[t] = (close[t] - tg) / (s - close[t])
            active = False
        elif t - start >= params.fail_window:
            active = False  # it held: a breakout, which is Donchian's trade

    out["ShortEntry"], out["ShortStop"] = entry, stop
    out["ShortTarget"], out["ShortRank"] = target, rank
    return out


# --------------------------------------------------------------------------
# S3 — the intermediate pullback short
# --------------------------------------------------------------------------
def _bearish_reversal(o, h, l, c, o1, c1) -> bool:
    body = abs(c - o)
    span = h - l
    if not (np.isfinite(span) and span > 0):
        return False
    upper = h - max(o, c)
    star = upper >= 2 * body and upper >= 0.5 * span
    engulfing = (
        np.isfinite(o1) and np.isfinite(c1)
        and c1 > o1 and c < o and o >= c1 and c <= o1
    )
    return bool(star or engulfing)


def pullback(frame: pd.DataFrame, params: PullbackParams = PullbackParams()) -> pd.DataFrame:
    o = frame["Open"].to_numpy(float)
    h = frame["High"].to_numpy(float)
    l = frame["Low"].to_numpy(float)
    c = frame["Close"].to_numpy(float)
    v = frame["Volume"].to_numpy(float) if "Volume" in frame else np.full(len(c), np.nan)
    low20 = frame["DonchianLow_M"].to_numpy(float)
    high55 = frame["DonchianHigh_N"].to_numpy(float)
    atr = frame["ATR"].to_numpy(float)

    out = _empty(frame.index)
    entry = out["ShortEntry"].to_numpy().copy()
    stop = out["ShortStop"].to_numpy().copy()
    target = out["ShortTarget"].to_numpy().copy()
    rank = out["ShortRank"].to_numpy().copy()

    active = False
    line = leg_low = 0.0
    born = low_bar = 0
    for t in range(1, len(c)):
        if not (np.isfinite(c[t]) and np.isfinite(low20[t])):
            continue
        crossed = c[t] < low20[t] and np.isfinite(low20[t - 1]) and c[t - 1] >= low20[t - 1]
        if crossed:
            active, line, born, leg_low, low_bar = True, low20[t], t, l[t], t
            continue
        if not active:
            continue
        if c[t] > line or t - born > params.max_age:
            active = False  # the line was reclaimed, or the breakdown is stale
            continue
        if l[t] < leg_low:
            leg_low, low_bar = l[t], t
            continue  # a new low is the decline continuing, not a rally

        a = atr[t]
        if not (np.isfinite(a) and a > 0 and np.isfinite(high55[t])):
            continue
        if (high55[t] - c[t]) / a < params.far_atr:
            continue
        if h[t] < line - params.touch_atr * a or c[t] >= line:
            continue
        if not _bearish_reversal(o[t], h[t], l[t], c[t], o[t - 1], c[t - 1]):
            continue
        if params.use_volume:
            rally = v[low_bar + 1 : t + 1]
            decline = v[max(0, low_bar - 19) : low_bar + 1]
            if not (len(rally) and len(decline)) or not np.nanmean(rally) < np.nanmean(decline):
                continue

        s = np.nanmax(h[low_bar : t + 1]) + TICK
        if s > c[t] > leg_low:
            entry[t], stop[t], target[t] = True, s, leg_low
            rank[t] = (c[t] - leg_low) / (s - c[t])
            active = False  # one entry per breakdown

    out["ShortEntry"], out["ShortStop"] = entry, stop
    out["ShortTarget"], out["ShortRank"] = target, rank
    return out


RULES: Dict[str, Callable] = {
    "breakdown": breakdown,
    "fakeout": fakeout,
    "pullback": pullback,
}
DEFAULT_PARAMS = {
    "breakdown": BreakdownParams(),
    "fakeout": FakeoutParams(),
    "pullback": PullbackParams(),
}
LABELS = {
    "breakdown": "S1 Macro 20-day breakdown",
    "fakeout": "S2 55-day fakeout (Turtle Soup)",
    "pullback": "S3 Pullback short inside the channel",
}
