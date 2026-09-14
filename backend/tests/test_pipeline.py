"""
Pipeline tests. Offline — no ticker universe is fetched and no bars are
downloaded; the universe getters and `fetch_fn` are both replaced.

These cover the guard that keeps the pipeline end-of-day. Everything else in
this project computes signals from completed daily closes; the pipeline is
where those closes enter, and until this guard existed it trusted its own
schedule to be running at the right hour.
"""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from app import pipeline
from app.models import RsiSignal, Signal, Universe
from app.pipeline import NEW_YORK, drop_in_progress_bar, previous_close
from app.signals import compute_signal_frame


def frame_ending(end, periods=5, step=2.0, start=100.0):
    """Business-day OHLCV frame ending on `end`. The step exceeds the
    high/low padding so the series genuinely breaks out — otherwise the
    close only ever equals the prior high and no signal fires."""
    closes = [start + step * i for i in range(periods)]
    idx = pd.bdate_range(end=end, periods=periods)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * periods,
        },
        index=idx,
    )


def test_todays_bar_is_dropped_while_the_session_is_still_running():
    """Yahoo's daily history carries the session in progress, where Close is
    just the current price. Storing it produced a GLD close of 404.58 for a
    day that actually closed at 405.49 — a 0.22% error on the number every
    exit flag is computed from."""
    df = frame_ending("2026-08-18")
    out = drop_in_progress_bar(df, datetime(2026, 8, 18, 10, 0, tzinfo=NEW_YORK))

    assert len(out) == len(df) - 1
    assert out.index[-1].date() == date(2026, 8, 17)


def test_todays_bar_is_kept_once_the_session_has_closed():
    """A guard that always dropped would freeze the dashboard one session
    behind forever — it has to give back the bar the evening run exists to
    capture."""
    df = frame_ending("2026-08-18")

    kept = drop_in_progress_bar(df, datetime(2026, 8, 18, 16, 30, tzinfo=NEW_YORK))
    assert len(kept) == len(df)
    assert kept.index[-1].date() == date(2026, 8, 18)

    # The boundary itself: 16:05 is the cutoff, and the five minutes before
    # it are settle time for the closing auction.
    assert len(drop_in_progress_bar(df, datetime(2026, 8, 18, 16, 4, tzinfo=NEW_YORK))) == len(df) - 1
    assert len(drop_in_progress_bar(df, datetime(2026, 8, 18, 16, 5, tzinfo=NEW_YORK))) == len(df)


def test_a_completed_session_is_never_dropped():
    """The rule is "today's bar", not "the last bar while the market is
    open". The latter would delete Friday's close every Monday morning, and
    again after every holiday."""
    friday = frame_ending("2026-08-14")
    monday_mid_session = datetime(2026, 8, 17, 10, 0, tzinfo=NEW_YORK)

    out = drop_in_progress_bar(friday, monday_mid_session)

    assert len(out) == len(friday)
    assert out.index[-1].date() == date(2026, 8, 14)


def test_a_frame_with_nothing_to_trim_survives_the_guard():
    """previous_close() runs outside the per-ticker try in run_pipeline, so
    anything raising on this path aborts the entire run rather than skipping
    one ticker."""
    mid = datetime(2026, 8, 18, 10, 0, tzinfo=NEW_YORK)

    assert drop_in_progress_bar(pd.DataFrame(), mid).empty
    assert drop_in_progress_bar(None, mid) is None

    # A listing whose only bar is today trims to nothing rather than raising.
    single = frame_ending("2026-08-18", periods=1)
    assert drop_in_progress_bar(single, mid).empty


def test_the_cutoff_follows_new_york_across_dst():
    """The same UTC instant is 16:30 in New York in July and 15:30 in
    January. A naive local clock or a hardcoded offset gets one of these
    wrong, and the failure is a silently corrupt close on the days nobody is
    watching."""
    july = frame_ending("2026-07-15")
    january = frame_ending("2026-01-15")

    # 20:30 UTC — after the close in summer, half an hour before it in winter.
    summer = datetime(2026, 7, 15, 20, 30, tzinfo=timezone.utc)
    winter = datetime(2026, 1, 15, 20, 30, tzinfo=timezone.utc)

    assert len(drop_in_progress_bar(july, summer)) == len(july)
    assert len(drop_in_progress_bar(january, winter)) == len(january) - 1


def test_the_days_move_is_measured_between_completed_sessions():
    """prev_close is stored beside close in the same row, so both have to
    come from the trimmed frame. If close moved back a session and prev_close
    didn't, the two would be the same number and the dashboard would render a
    confident 0.00% day move."""
    df = frame_ending("2026-08-18")
    trimmed = drop_in_progress_bar(df, datetime(2026, 8, 18, 10, 0, tzinfo=NEW_YORK))

    assert previous_close(trimmed) == df["Close"].iloc[-3]
    assert previous_close(trimmed) != previous_close(df)


def test_a_run_during_the_session_stores_the_last_completed_close(db, monkeypatch):
    """End to end: a run at 11:00 ET must store yesterday's session.

    This is also the test that pins *placement*, and the assertion doing it
    is `prev_close` — not, as you might expect, ATR. Every indicator is
    backward-looking, so computing on the full frame and then reading the
    second-to-last row yields identical Donchian levels and an identical ATR;
    the partial bar simply isn't in their windows. What the partial bar does
    reach is `previous_close()`, which is called on the frame directly. Trim
    afterwards and `close` and `prev_close` both become yesterday's close —
    the same number twice, rendering as a confident 0.00% day move."""
    monkeypatch.setattr(pipeline, "get_sp500_tickers", lambda: [])
    monkeypatch.setattr(pipeline, "get_extra_equity_tickers", lambda: [])
    monkeypatch.setattr(pipeline, "get_commodity_tickers", lambda: [])
    # VOO is in INDEX_ETF_TICKERS, so one ticker drives both strategies.
    monkeypatch.setattr(pipeline, "get_index_etf_tickers", lambda: ["VOO"])
    monkeypatch.setattr(
        pipeline, "market_now_et", lambda: datetime(2026, 8, 18, 11, 0, tzinfo=NEW_YORK)
    )

    # 260 bars clears RSI's 200-day trend filter. The final bar is given an
    # absurd close so any leak is unmistakable rather than marginal.
    df = frame_ending("2026-08-18", periods=260)
    df.iloc[-1, df.columns.get_loc("Close")] = 99_999.0
    df.iloc[-1, df.columns.get_loc("High")] = 99_999.0

    pipeline.run_pipeline(fetch_fn=lambda tickers: {"VOO": df.copy()})

    sig = db.query(Signal).filter(Signal.ticker == "VOO").one()
    assert sig.as_of_date == date(2026, 8, 17)
    assert sig.close == pytest.approx(df["Close"].iloc[-2])
    assert sig.prev_close == pytest.approx(df["Close"].iloc[-3])

    # Correctness of the stored levels, rather than a placement check —
    # see the docstring for why these would match either way.
    expected = compute_signal_frame(df.iloc[:-1]).iloc[-1]
    assert sig.atr == pytest.approx(float(expected["ATR"]))
    assert sig.donchian_low == pytest.approx(float(expected["DonchianLow_M"]))

    rsi = db.query(RsiSignal).filter(RsiSignal.ticker == "VOO").one()
    assert rsi.as_of_date == date(2026, 8, 17)


# --- empty fetches and a missing S&P list ------------------------------

AFTER_CLOSE = datetime(2026, 8, 18, 20, 0, tzinfo=NEW_YORK)


@pytest.fixture
def one_etf_universe(monkeypatch):
    """A run over VOO alone, after the close so nothing is trimmed, with no
    real pauses between fetches."""
    monkeypatch.setattr(pipeline, "get_sp500_tickers", lambda: [])
    monkeypatch.setattr(pipeline, "get_extra_equity_tickers", lambda: [])
    monkeypatch.setattr(pipeline, "get_commodity_tickers", lambda: [])
    monkeypatch.setattr(pipeline, "get_index_etf_tickers", lambda: ["VOO"])
    monkeypatch.setattr(pipeline, "market_now_et", lambda: AFTER_CLOSE)
    monkeypatch.setattr(pipeline, "RETRY_PAUSE_SEC", 0)
    monkeypatch.setattr(pipeline, "PAUSE_BETWEEN_CHUNKS_SEC", 0)


def test_a_ticker_that_comes_back_empty_is_retried_once(db, one_etf_universe, capsys):
    """Nearly every "possibly delisted" in pipeline.log was a live ticker
    whose download was cut off by the Mac going back to sleep. The first
    empty answer must not be the last word."""
    df = frame_ending("2026-08-18", periods=260)
    calls = []

    def flaky(tickers):
        calls.append(list(tickers))
        return {} if len(calls) == 1 else {"VOO": df.copy()}

    pipeline.run_pipeline(fetch_fn=flaky)

    assert calls == [["VOO"], ["VOO"]]
    assert db.query(Signal).filter(Signal.ticker == "VOO").one().as_of_date == date(2026, 8, 18)
    out = capsys.readouterr().out
    assert "Retry recovered 1 of 1 ticker(s)." in out
    assert "Donchian: 1 updated, 0 skipped/failed." in out


def test_a_ticker_still_empty_after_the_retry_counts_as_failed(db, one_etf_universe, capsys):
    """One retry, not a loop: a genuinely dead symbol costs one extra
    request and is then reported, exactly as before."""
    calls = []

    def dead(tickers):
        calls.append(list(tickers))
        return {}

    pipeline.run_pipeline(fetch_fn=dead)

    assert len(calls) == 2
    assert db.query(Signal).count() == 0
    out = capsys.readouterr().out
    assert "Retry recovered 0 of 1 ticker(s)." in out
    assert "Donchian: 0 updated, 1 skipped/failed." in out


def test_a_chunk_whose_fetch_raises_is_retried_too(db, one_etf_universe):
    df = frame_ending("2026-08-18", periods=260)
    calls = []

    def raises_once(tickers):
        calls.append(list(tickers))
        if len(calls) == 1:
            raise RuntimeError("connection reset")
        return {"VOO": df.copy()}

    pipeline.run_pipeline(fetch_fn=raises_once)

    assert db.query(Signal).filter(Signal.ticker == "VOO").count() == 1


def test_a_failed_sp500_fetch_reuses_the_stored_equities(db, one_etf_universe, capsys):
    """Twice in September 2026 the Wikipedia fetch failed just after a wake,
    and the run rewrote `universe` down to two extra equities — taking the
    stored S&P list with it."""
    for t in ("AAA", "BBB", "CCC"):
        db.add(Universe(ticker=t, asset_class="equity", active=True))
    db.commit()
    fetched = []

    def record(tickers):
        fetched.extend(tickers)
        return {}

    pipeline.run_pipeline(fetch_fn=record)

    stored = {
        t for (t,) in db.query(Universe.ticker).filter(Universe.asset_class == "equity").all()
    }
    assert stored == {"AAA", "BBB", "CCC"}
    assert {"AAA", "BBB", "CCC"} <= set(fetched)
    out = capsys.readouterr().out
    assert "reusing the 3 equities stored by the last run" in out
    assert "3 equities + 0 commodities" in out


def test_a_successful_sp500_fetch_replaces_the_stored_list(db, one_etf_universe, monkeypatch, capsys):
    """The fallback is only a fallback: a good fetch must still drop a
    company that has left the index."""
    db.add(Universe(ticker="GONE", asset_class="equity", active=True))
    db.commit()
    monkeypatch.setattr(pipeline, "get_sp500_tickers", lambda: ["AAA"])

    pipeline.run_pipeline(fetch_fn=lambda tickers: {})

    stored = [t for (t,) in db.query(Universe.ticker).filter(Universe.asset_class == "equity").all()]
    assert stored == ["AAA"]
    assert "reusing" not in capsys.readouterr().out
