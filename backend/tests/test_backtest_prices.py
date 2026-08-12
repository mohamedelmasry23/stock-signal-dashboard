"""
Price cache tests. All offline — the fetcher is injected, so nothing here
reaches Yahoo.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import inspect

from app.backtest import prices
from app.database import Base
from app.database import engine as app_engine

START, END = date(2024, 1, 1), date(2024, 3, 1)


@pytest.fixture
def cache(tmp_path):
    return prices.cache_engine(tmp_path / "prices_cache.db")


def raw_frame(closes, adj_ratio=1.0, tz=None):
    """A frame shaped like what fetch_ohlc_history returns (lowercase cols)."""
    idx = pd.bdate_range(start="2024-01-02", periods=len(closes), tz=tz)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "adj_close": [c * adj_ratio for c in closes],
            "volume": [1_000_000.0] * len(closes),
        },
        index=idx,
    )


def test_cache_roundtrip_means_the_second_run_needs_no_network(cache):
    """The whole point of the cache: re-running must not refetch."""
    def fetch_once(tickers, start, end):
        return {"AAA": raw_frame([100.0, 101.0, 102.0])}

    def must_not_be_called(tickers, start, end):
        raise AssertionError(f"refetched {tickers} — the cache did not serve them")

    first = prices.load_prices(["AAA"], START, END, fetch_fn=fetch_once, engine=cache, verbose=False)
    second = prices.load_prices(["AAA"], START, END, fetch_fn=must_not_be_called, engine=cache, verbose=False)

    assert len(first["AAA"]) == 3
    pd.testing.assert_frame_equal(first["AAA"], second["AAA"])


def test_a_ticker_with_no_data_is_negatively_cached(cache):
    """fetch_ohlc_batch silently drops dead symbols. Without recording the
    failure, the few dozen delisted names in an S&P list would be
    re-requested on every single run."""
    calls = []

    def fetch(tickers, start, end):
        calls.append(list(tickers))
        return {}  # nothing came back

    prices.load_prices(["DEAD"], START, END, fetch_fn=fetch, engine=cache, verbose=False)
    prices.load_prices(["DEAD"], START, END, fetch_fn=fetch, engine=cache, verbose=False)

    assert len(calls) == 1, "a dead ticker was requested twice"
    assert "DEAD" in prices.cached_tickers(cache)


def test_offline_refuses_rather_than_silently_fetching(cache):
    with pytest.raises(RuntimeError, match="not cached"):
        prices.load_prices(["NEW"], START, END, engine=cache, offline=True, verbose=False)


def test_adjustment_reproduces_adj_close_returns(cache):
    """Adjusted mode must give the dividend-inclusive return series, and
    must leave intrabar ratios untouched so ATR relative to price is
    unchanged."""
    frame = raw_frame([100.0, 110.0, 121.0], adj_ratio=0.9)

    converted = prices.to_total_return(frame, "adjusted")

    expected = (frame["adj_close"] / frame["adj_close"].shift(1) - 1).dropna()
    actual = converted["Close"].pct_change().dropna()
    np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy())

    # High/Close ratio survives the scaling.
    np.testing.assert_allclose(
        (converted["High"] / converted["Close"]).to_numpy(),
        (frame["high"] / frame["close"]).to_numpy(),
    )


def test_raw_mode_leaves_prices_alone(cache):
    frame = raw_frame([100.0, 110.0], adj_ratio=0.9)
    converted = prices.to_total_return(frame, "raw")
    np.testing.assert_allclose(converted["Close"].to_numpy(), frame["close"].to_numpy())


def test_tz_aware_frames_are_normalized(cache):
    """Futures come back tz-aware while equities don't. Unioning the two
    does not raise — it silently yields an object-dtype index with every
    date duplicated, which would align nothing."""
    aware = prices.normalize_index(raw_frame([100.0, 101.0], tz="America/New_York"))
    naive = prices.normalize_index(raw_frame([50.0, 51.0]))

    assert aware.index.tz is None
    combined = aware.index.union(naive.index)
    assert isinstance(combined, pd.DatetimeIndex)
    assert not combined.duplicated().any()


def test_cache_never_touches_the_app_database(cache):
    """If `bars` were declared on app.database.Base, create_all() in
    app/main.py would create an empty bars table inside real.db on every
    API start. Pin the isolation so a future refactor can't undo it."""
    assert "bars" not in Base.metadata.tables
    assert "fetch_log" not in Base.metadata.tables

    prices.load_prices(
        ["AAA"], START, END,
        fetch_fn=lambda t, s, e: {"AAA": raw_frame([100.0, 101.0])},
        engine=cache, verbose=False,
    )

    assert "bars" not in set(inspect(app_engine).get_table_names())


def test_only_missing_tickers_are_fetched(cache):
    requested = []

    def fetch(tickers, start, end):
        requested.append(sorted(tickers))
        return {t: raw_frame([100.0, 101.0]) for t in tickers}

    prices.load_prices(["AAA"], START, END, fetch_fn=fetch, engine=cache, verbose=False)
    prices.load_prices(["AAA", "BBB"], START, END, fetch_fn=fetch, engine=cache, verbose=False)

    assert requested == [["AAA"], ["BBB"]]
