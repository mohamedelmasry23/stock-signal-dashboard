"""
Daily price history for the ticker detail chart.

The dashboard shows a verdict — "GLD is below its 20-day low" — without the
evidence behind it. This module supplies the evidence: ten years of daily
bars with the Donchian levels drawn alongside them, so a signal can be read
in context rather than taken on trust.

It exists as its own module, rather than reusing the bar cache in
app/backtest/prices.py, for two reasons. The mechanical one: app/main.py
must never import app.backtest (see that package's docstring), and the API
is this module's only consumer. The substantive one: that cache is built
for reproducibility — no TTL, dividend-adjusted, populated once per backtest
universe. A chart wants the opposite of all three.

Three rules here are load-bearing:

**Raw prices, not adjusted.** This follows app/pipeline.py, which fetches
with auto_adjust=False and feeds the raw Close to compute_signal_frame. The
backtest instead scales everything by adj_close/close, which is right for
measuring total return and wrong here: for any dividend payer it would put a
visibly different 55-day level on the chart than the one on the dashboard
card, with nothing on screen to say which of the two is lying.

**Bands are computed over the full history, then the range is sliced.**
DonchianHigh_N needs 55 bars plus a shift and ATR needs 20, so a six-month
chart built from a six-month fetch would have no channel at all across its
first quarter — the exact stretch the user is usually looking at. Fetching
once also makes switching 6M to MAX a cache hit rather than a round trip.

**No ThreadPoolExecutor.** app/quotes.py uses one because it fetches a whole
holdings list; here there is exactly one ticker per request, so threads
would add file-descriptor pressure for no parallelism. Under launchd the
limit is 256, and yfinance reports exhausting it as an unrelated-looking
SQLite error (see the environment notes in CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

import pandas as pd
import yfinance as yf

from .pipeline import configure_yfinance_cache
from .signals import compute_signal_frame
from .timeutil import utcnow

# One fetch serves every range button. Ten years is what Yahoo will return
# for a daily interval without paging, and it comfortably outlives the
# 2016 start date the backtests use.
LOOKBACK_PERIOD = "10y"

# Far longer than quotes.py's 10 seconds, and deliberately so. A daily bar
# changes once a day; only the current session's partial bar moves at all.
# The headline price on the detail page does not come from here — it comes
# from app/quotes.py, with its own 10-second window — so this cache can be
# long enough to make reopening a ticker instant without any number on
# screen ever being fifteen minutes stale.
CACHE_TTL_SECONDS = 900

# A ten-year frame is ~2,500 rows across a dozen float columns, roughly
# 120 KB after compute_signal_frame widens it. Unbounded, a session spent
# clicking through S&P names would grow the uvicorn process all afternoon —
# invisibly in dev, where --reload resets it on every save.
MAX_CACHED_TICKERS = 32

# Calendar spans rather than bar counts: "6M" should mean six months whether
# the symbol trades 252 days a year or 200. MAX is the whole fetch.
RANGE_DAYS: Dict[str, Optional[int]] = {
    "6M": 183,
    "1Y": 365,
    "2Y": 730,
    "5Y": 1826,
    "MAX": None,
}

DEFAULT_RANGE = "1Y"

_cache: Dict[str, "History"] = {}


@dataclass(frozen=True)
class History:
    ticker: str
    # The full lookback, already through compute_signal_frame: OHLCV plus
    # DonchianHigh_N, DonchianLow_M, EntrySignal, ExitSignal, ATR and
    # StopDistance. Slice this, never recompute from a slice.
    frame: pd.DataFrame
    fetched_at: datetime  # naive UTC, per app/timeutil.py


def _fetch_history(ticker: str) -> Optional[pd.DataFrame]:
    """Ten years of daily bars from Yahoo, or None.

    The only function in this module that touches the network, which is what
    makes the rest of it testable offline — every test monkeypatches exactly
    this, as tests/test_quotes.py does with quotes._fetch_one.

    yf.Ticker().history rather than yf.download: for a single ticker
    download returns MultiIndex columns (which is why pipeline.fetch_ohlc_batch
    has to index raw["Open"][t]), and flat columns are one less thing to get
    wrong on the seam everything else trusts.
    """
    configure_yfinance_cache()
    try:
        raw = yf.Ticker(ticker).history(
            period=LOOKBACK_PERIOD,
            interval="1d",
            auto_adjust=False,  # raw prices — see the module docstring
        )
    except Exception:
        return None

    if raw is None or raw.empty:
        return None

    missing = [c for c in ("Open", "High", "Low", "Close", "Volume") if c not in raw.columns]
    if missing:
        return None

    frame = raw[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
    if frame.empty:
        return None

    return normalize_index(frame)


def normalize_index(frame: pd.DataFrame) -> pd.DataFrame:
    """Force a tz-naive, date-only, unique, sorted DatetimeIndex.

    Ported from app/backtest/prices.py rather than imported — app/main.py
    must not reach into that package, and this module is on its import path.

    Futures symbols such as GC=F come back tz-aware while equities don't.
    Mixing the two does not raise; it silently yields an object-dtype index
    that aligns nothing and fails far away from the cause.
    """
    index = frame.index
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    out = frame.copy()
    out.index = pd.DatetimeIndex(index).normalize()
    return out[~out.index.duplicated(keep="last")].sort_index()


def build_history(ticker: str) -> Optional[History]:
    """Fetch, normalize, and attach the strategy's own columns. Never raises.

    compute_signal_frame is called on the *whole* lookback, and it preserves
    the OHLCV it was given, so one call produces the entire chart payload.
    Its defaults are the pipeline's 55/20/20/2.0 — passing anything else here
    would draw a chart of a strategy nobody is trading.
    """
    frame = _fetch_history(ticker)
    if frame is None or frame.empty:
        return None

    try:
        computed = compute_signal_frame(frame)
    except Exception:
        return None

    return History(ticker=ticker, frame=computed, fetched_at=utcnow())


def get_history(ticker: str, *, force: bool = False) -> Optional[History]:
    """TTL-cached wrapper. Returns None when the ticker yields nothing usable.

    Stamps are per ticker, unlike quotes.py's single global one. That module
    fetches a whole batch in one call, so one stamp describes all of it
    honestly. History is fetched one ticker at a time on demand, and a shared
    stamp would discard a GLD frame fetched a second ago because MSFT was
    fetched sixteen minutes ago.
    """
    ticker = ticker.upper()

    cached = _cache.get(ticker)
    if cached is not None and not force:
        age = (utcnow() - cached.fetched_at).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return cached

    built = build_history(ticker)
    if built is None:
        return cached  # a stale frame beats a blank chart while Yahoo is down

    _cache[ticker] = built
    _evict()
    return built


def _evict() -> None:
    """Drop the oldest entries once the cache is over its cap."""
    while len(_cache) > MAX_CACHED_TICKERS:
        oldest = min(_cache, key=lambda t: _cache[t].fetched_at)
        del _cache[oldest]


def slice_range(frame: pd.DataFrame, range_key: str) -> pd.DataFrame:
    """The trailing window for a range button.

    Measured back from the frame's own last bar rather than from today, so a
    symbol that stopped trading still shows its final six months instead of
    an empty window.
    """
    days = RANGE_DAYS.get(range_key.upper(), RANGE_DAYS[DEFAULT_RANGE])
    if days is None or frame.empty:
        return frame
    cutoff = frame.index[-1] - pd.Timedelta(days=days)
    return frame[frame.index >= cutoff]


def clear_cache() -> None:
    """Test hook — the module-level cache would otherwise leak across tests."""
    _cache.clear()
