"""
Live quotes for the dashboard.

Everything else in this app is deliberately end-of-day: the pipeline runs
once after the close, and signals are computed from completed daily bars.
That is what makes it the same system the backtest measured.

This module is the one exception, and its scope is strictly display. A
live quote updates what a position is *worth* and how it has moved today.
It must never feed the Donchian levels, the ATR or the exit flags — an
exit that fires on an intraday dip which recovers by the close is a
different strategy from the one that was tested.

The change percentage is taken from Yahoo's own `regularMarketChangePercent`
rather than derived here. Two reasons: it's what the user sees quoted
everywhere else, and Yahoo applies its own handling of halts, splits and
odd sessions that a naive (last - prev) / prev would get subtly wrong.

Note the units: Yahoo reports 0.5265777 to mean **0.53%**, not 52.7%. It
is already scaled as a percentage, so it must not be multiplied by 100
again. `test_change_percent_is_not_rescaled` guards that.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional

import yfinance as yf

from .pipeline import configure_yfinance_cache
from .timeutil import utcnow

# One dashboard refresh calls /api/holdings and /api/account within
# milliseconds of each other, and both need the same quotes. This cache
# exists to make that a single trip to Yahoo.
#
# Kept deliberately short: its job is to coalesce concurrent calls, not to
# serve stale prices. A longer window would mean clicking Refresh again
# returned the same numbers, which is precisely what the button is for.
CACHE_TTL_SECONDS = 10

# Enough to cover a normal holdings list in one round trip without being
# rude to Yahoo. Requests are IO-bound, so threads are the right tool.
MAX_WORKERS = 8

_cache: Dict[str, "Quote"] = {}
_cache_stamp: Optional[float] = None


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: Optional[float]
    change_percent: Optional[float]  # already a percentage: 0.53 means 0.53%
    previous_close: Optional[float]
    # REGULAR | CLOSED | PRE | POST | POSTPOST — lets the UI say whether
    # the figure is live or simply the last session's final move.
    market_state: Optional[str]


def _fetch_one(ticker: str) -> Optional[Quote]:
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return None
    if not info:
        return None

    def num(key: str) -> Optional[float]:
        value = info.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    price = num("regularMarketPrice")
    if price is None:
        return None  # nothing usable; caller falls back to stored close

    return Quote(
        ticker=ticker,
        price=price,
        change_percent=num("regularMarketChangePercent"),
        previous_close=num("regularMarketPreviousClose"),
        market_state=info.get("marketState") if isinstance(info.get("marketState"), str) else None,
    )


def fetch_quotes(tickers: List[str]) -> Dict[str, Quote]:
    """Live quotes, fetched in parallel. Tickers that fail are simply
    absent from the result — a quote is an enhancement, so one bad symbol
    must not cost the caller the others."""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}

    configure_yfinance_cache()
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(tickers))) as pool:
        results = pool.map(_fetch_one, tickers)

    return {q.ticker: q for q in results if q is not None}


def get_quotes(tickers: List[str], *, force: bool = False) -> Dict[str, Quote]:
    """Cached wrapper. One dashboard refresh calls several endpoints; this
    keeps that to a single trip to Yahoo."""
    global _cache, _cache_stamp

    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}

    now = utcnow().timestamp()
    fresh = _cache_stamp is not None and (now - _cache_stamp) < CACHE_TTL_SECONDS
    if fresh and not force and all(t in _cache for t in tickers):
        return {t: _cache[t] for t in tickers if t in _cache}

    fetched = fetch_quotes(tickers)
    _cache = {**_cache, **fetched}
    _cache_stamp = now
    return fetched


def clear_cache() -> None:
    """Test hook — the module-level cache would otherwise leak across tests."""
    global _cache, _cache_stamp
    _cache = {}
    _cache_stamp = None
