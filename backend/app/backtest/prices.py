"""
Historical price cache.

Refetching ~500 tickers from Yahoo on every run is slow, rate-limit-prone
and non-reproducible — two runs a week apart would silently use different
data. So bars are cached locally and the network is touched only for
what's missing.

## Isolation

This cache lives in its own SQLite file with its own engine and a bare
`MetaData()`. It must never be attached to `app.database.Base`: if it
were, `Base.metadata.create_all()` in `app/main.py` would create an empty
`bars` table inside `real.db` on every API start, and `sync_new_columns()`
would iterate it. `test_cache_never_touches_the_app_database` pins this.

The filename sits directly in `data/` because `data/*.db` is already
git-ignored — `data/cache/prices.sqlite` would not be.

## Dividends

Fetched with `auto_adjust=False` so both `Close` and `Adj Close` are
stored. Splits are already applied to `Close` by Yahoo; `Adj Close`
additionally removes dividends, so `adj_close / close` is a pure dividend
factor. Measured on this project's own universe, that difference is not
marginal: KO returned 96% over ten years on price alone and 168% with
dividends reinvested. A backtest on raw closes would understate every
equity return by roughly that shape.

Scaling the whole OHLC bar by that one factor keeps all intrabar ratios
intact, so ATR-relative-to-price and Donchian breakouts are unchanged
while the returns become total-return.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    text,
)

from ..config import DATA_DIR
from ..pipeline import CHUNK_SIZE, PAUSE_BETWEEN_CHUNKS_SEC

CACHE_PATH = DATA_DIR / "prices_cache.db"

# Deliberately NOT app.database.Base — see the module docstring.
_metadata = MetaData()

bars = Table(
    "bars",
    _metadata,
    Column("ticker", String, primary_key=True),
    Column("date", String, primary_key=True),  # ISO date, never a datetime
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    Column("adj_close", Float),
    Column("volume", Float),
)

fetch_log = Table(
    "fetch_log",
    _metadata,
    Column("ticker", String, primary_key=True),
    Column("first_date", String),
    Column("last_date", String),
    Column("rows", Integer),
    Column("fetched_at", String),
    # 'empty' is the load-bearing one: fetch_ohlc_batch silently drops
    # tickers with no data, so without a negative cache the few dozen dead
    # symbols in any S&P list get re-requested on every single run.
    Column("status", String),
)


def cache_engine(path: Optional[Path] = None):
    engine = create_engine(f"sqlite:///{path or CACHE_PATH}")
    _metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA busy_timeout=5000"))
    return engine


def normalize_index(frame: pd.DataFrame) -> pd.DataFrame:
    """Force a tz-naive, date-only, unique, sorted DatetimeIndex.

    Futures symbols such as GC=F come back tz-aware while equities don't.
    Unioning a tz-aware index with a tz-naive one does not raise — it
    silently produces an object-dtype index with every date duplicated,
    which would align nothing and fail far away from the cause.
    """
    index = frame.index
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    out = frame.copy()
    out.index = pd.DatetimeIndex(index).normalize()
    return out[~out.index.duplicated(keep="last")].sort_index()


def fetch_ohlc_history(tickers: List[str], start: date, end: date) -> Dict[str, pd.DataFrame]:
    """Download raw bars, keeping Adj Close.

    Explicit dates rather than `period="10y"`: a relative period silently
    shifts the window every day you run it, so results would never be
    comparable across runs.
    """
    if not tickers:
        return {}

    raw = yf.download(
        tickers,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="column",
    )
    if raw is None or raw.empty:
        return {}

    out: Dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            frame = pd.DataFrame(
                {
                    "open": raw["Open"][ticker],
                    "high": raw["High"][ticker],
                    "low": raw["Low"][ticker],
                    "close": raw["Close"][ticker],
                    "adj_close": raw["Adj Close"][ticker],
                    "volume": raw["Volume"][ticker],
                }
            ).dropna(subset=["close"])
            if not frame.empty:
                out[ticker] = normalize_index(frame)
        except Exception:
            continue  # delisted / bad symbol — recorded as 'empty' by the caller
    return out


def _write(engine, ticker: str, frame: pd.DataFrame) -> None:
    rows = [
        {
            "ticker": ticker,
            "date": stamp.date().isoformat(),
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "close": _f(row.get("close")),
            "adj_close": _f(row.get("adj_close")),
            "volume": _f(row.get("volume")),
        }
        for stamp, row in frame.iterrows()
    ]
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(
                    "INSERT OR REPLACE INTO bars "
                    "(ticker, date, open, high, low, close, adj_close, volume) "
                    "VALUES (:ticker, :date, :open, :high, :low, :close, :adj_close, :volume)"
                ),
                row,
            )
        conn.execute(
            text(
                "INSERT OR REPLACE INTO fetch_log "
                "(ticker, first_date, last_date, rows, fetched_at, status) "
                "VALUES (:ticker, :first, :last, :rows, :at, :status)"
            ),
            {
                "ticker": ticker,
                "first": rows[0]["date"] if rows else None,
                "last": rows[-1]["date"] if rows else None,
                "rows": len(rows),
                "at": datetime.now().isoformat(timespec="seconds"),
                "status": "ok" if rows else "empty",
            },
        )


def _f(value) -> Optional[float]:
    if value is None:
        return None
    value = float(value)
    return value if np.isfinite(value) else None


def _mark_empty(engine, tickers: Iterable[str]) -> None:
    with engine.begin() as conn:
        for ticker in tickers:
            conn.execute(
                text(
                    "INSERT OR REPLACE INTO fetch_log "
                    "(ticker, first_date, last_date, rows, fetched_at, status) "
                    "VALUES (:ticker, NULL, NULL, 0, :at, 'empty')"
                ),
                {"ticker": ticker, "at": datetime.now().isoformat(timespec="seconds")},
            )


def cached_tickers(engine) -> set:
    """Tickers already attempted, successfully or not."""
    with engine.begin() as conn:
        return {r[0] for r in conn.execute(select(fetch_log.c.ticker))}


def to_total_return(frame: pd.DataFrame, price_mode: str = "adjusted") -> pd.DataFrame:
    """Convert stored bars into the OHLCV shape compute_signal_frame wants.

    In "adjusted" mode every column is scaled by `adj_close / close`, so
    dividends are reinvested and intrabar ratios are preserved.
    """
    out = pd.DataFrame(index=frame.index)
    if price_mode == "adjusted":
        factor = frame["adj_close"] / frame["close"]
        factor = factor.where(np.isfinite(factor) & (frame["close"] > 0), 1.0)
    else:
        factor = pd.Series(1.0, index=frame.index)

    for src, dst in (("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close")):
        out[dst] = frame[src] * factor
    # Volume moves inversely so notional turnover stays honest.
    out["Volume"] = frame["volume"] / factor.replace(0, 1.0)
    return out.dropna(subset=["Close"])


def load_prices(
    tickers: List[str],
    start: date,
    end: date,
    *,
    fetch_fn: Callable[[List[str], date, date], Dict[str, pd.DataFrame]] = fetch_ohlc_history,
    engine=None,
    refresh: bool = False,
    offline: bool = False,
    price_mode: str = "adjusted",
    verbose: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Bars for `tickers`, fetching only what isn't cached."""
    engine = engine or cache_engine()
    wanted = sorted(set(tickers))

    known = set() if refresh else cached_tickers(engine)
    missing = [t for t in wanted if t not in known]

    if missing and not offline:
        for i in range(0, len(missing), CHUNK_SIZE):
            chunk = missing[i : i + CHUNK_SIZE]
            if verbose:
                print(f"[prices] fetching {i + 1}-{i + len(chunk)} of {len(missing)}...")
            fetched = fetch_fn(chunk, start, end)
            for ticker, frame in fetched.items():
                _write(engine, ticker, frame)
            # Anything the fetch didn't return is negatively cached, or it
            # would be re-requested forever.
            _mark_empty(engine, [t for t in chunk if t not in fetched])
            if i + CHUNK_SIZE < len(missing):
                import time

                time.sleep(PAUSE_BETWEEN_CHUNKS_SEC)
    elif missing and offline:
        raise RuntimeError(
            f"offline=True but {len(missing)} ticker(s) are not cached "
            f"(e.g. {missing[:3]}). Run once without --offline first."
        )

    placeholders = ",".join(f":t{i}" for i in range(len(wanted)))
    params = {f"t{i}": t for i, t in enumerate(wanted)}
    params.update({"start": start.isoformat(), "end": end.isoformat()})

    with engine.begin() as conn:
        raw = pd.read_sql(
            text(
                f"SELECT * FROM bars WHERE ticker IN ({placeholders}) "
                "AND date >= :start AND date <= :end ORDER BY ticker, date"
            ),
            conn,
            params=params,
        )

    out: Dict[str, pd.DataFrame] = {}
    for ticker, group in raw.groupby("ticker"):
        frame = group.drop(columns=["ticker"]).set_index("date")
        frame.index = pd.DatetimeIndex(frame.index)
        converted = to_total_return(frame, price_mode)
        if len(converted):
            out[str(ticker)] = converted
    return out
