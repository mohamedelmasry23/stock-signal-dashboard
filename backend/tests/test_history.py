"""
Price-history tests, and the detail endpoint built on it. Offline — Yahoo
is never contacted; `_fetch_history` and `quotes._fetch_one` are patched,
which are the only two functions on this path that touch it.
"""

import json
from datetime import date

import pandas as pd
import pytest

from app import history, quotes
from app.models import Holding, Signal, Universe
from app.signals import compute_signal_frame
from app.timeutil import utcnow


@pytest.fixture(autouse=True)
def clear_history_cache():
    """Both caches are module-level, so they would otherwise leak between
    tests — and an unpatched quote lookup would reach the network."""
    history.clear_cache()
    quotes.clear_cache()
    yield
    history.clear_cache()
    quotes.clear_cache()


@pytest.fixture(autouse=True)
def no_network_quotes(monkeypatch):
    """The detail endpoint fetches a quote by default. Patch the seam so no
    test can silently depend on Yahoo being reachable."""
    monkeypatch.setattr(quotes, "_fetch_one", lambda t: None)


def rising(n, start=100.0, step=0.5, end="2026-08-11"):
    """A steadily rising series long enough to clear the 55-bar warm-up."""
    closes = [start + i * step for i in range(n)]
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


def test_bands_are_computed_before_the_range_is_sliced(monkeypatch):
    """A six-month chart built from a six-month fetch has no channel across
    its first 55 bars — a quarter of the window, and usually the part being
    looked at. Fetching the full lookback and slicing afterwards is the only
    reason the band exists at the left edge of a short range."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(400))

    frame = history.slice_range(history.get_history("AAA").frame, "6M")

    assert len(frame) < 400  # the slice really happened
    assert frame["DonchianHigh_N"].notna().all()
    assert frame["ATR"].notna().all()


def test_bands_are_not_shifted_twice(monkeypatch):
    """compute_signal_frame already shifts the rolling windows so today's
    own high can't be part of the level today has to break. Shifting again
    anywhere downstream is the classic off-by-one, and it would draw a
    channel that lags the price by a day for no visible reason."""
    raw = rising(200)
    monkeypatch.setattr(history, "_fetch_history", lambda t: raw)

    frame = history.get_history("AAA").frame
    i = 120

    expected = raw["High"].iloc[i - 55 : i].max()
    assert frame["DonchianHigh_N"].iloc[i] == pytest.approx(expected)
    # And the bar's own high is genuinely excluded.
    assert frame["DonchianHigh_N"].iloc[i] < raw["High"].iloc[i]


def test_the_chart_never_reimplements_the_strategy(monkeypatch):
    """The whole point of routing this through compute_signal_frame is that
    the chart and the dashboard card can't drift. If this module ever grows
    its own rolling().max(), this is the test that catches it."""
    raw = rising(200)
    monkeypatch.setattr(history, "_fetch_history", lambda t: raw)

    served = history.get_history("AAA").frame
    reference = compute_signal_frame(raw)

    for column in ("DonchianHigh_N", "DonchianLow_M", "ATR", "StopDistance"):
        pd.testing.assert_series_equal(served[column], reference[column])


def test_range_slices_back_from_the_last_bar(monkeypatch):
    """Measured from today, a symbol that stopped trading last year would
    return an empty window and render as a blank chart with no explanation.
    Measured from its own last bar, it shows its final six months."""
    stale = rising(400, end="2024-06-28")
    monkeypatch.setattr(history, "_fetch_history", lambda t: stale)

    frame = history.get_history("AAA").frame
    sliced = history.slice_range(frame, "6M")

    assert len(sliced) > 0
    assert sliced.index[-1] == frame.index[-1]
    assert len(sliced) < len(history.slice_range(frame, "MAX"))


def test_max_returns_the_whole_lookback(monkeypatch):
    """MAX is the one range that must not slice — it's what makes the
    decade of history reachable at all."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(400))

    frame = history.get_history("AAA").frame
    assert len(history.slice_range(frame, "MAX")) == len(frame)


def test_a_reopened_ticker_costs_no_second_fetch(monkeypatch):
    """The cache exists so that flipping between range buttons, or coming
    back to a ticker, is instant. Every range is served from one fetch."""
    calls = []

    def counted(ticker):
        calls.append(ticker)
        return rising(200)

    monkeypatch.setattr(history, "_fetch_history", counted)

    history.get_history("AAA")
    history.get_history("AAA")
    history.get_history("AAA")

    assert calls == ["AAA"]


def test_the_cache_is_per_ticker(monkeypatch):
    """quotes.py keeps a single global stamp because it fetches a whole
    batch in one call. History is fetched one ticker at a time, so a shared
    stamp would throw away a frame fetched seconds ago because a different
    ticker was fetched a quarter of an hour ago."""
    calls = []

    def counted(ticker):
        calls.append(ticker)
        return rising(200)

    monkeypatch.setattr(history, "_fetch_history", counted)

    history.get_history("AAA")
    history.get_history("BBB")
    history.get_history("AAA")

    assert calls == ["AAA", "BBB"]


def test_a_tz_aware_index_is_normalized(monkeypatch):
    """GC=F comes back tz-aware while equities don't. Left alone, the two
    can't be compared or sliced against each other, and the failure surfaces
    far from the cause."""
    aware = rising(200)
    aware.index = aware.index.tz_localize("America/New_York")
    monkeypatch.setattr(history, "_fetch_history", lambda t: history.normalize_index(aware))

    frame = history.get_history("GC=F").frame

    assert frame.index.tz is None
    assert (frame.index == frame.index.normalize()).all()
    assert frame.index.is_monotonic_increasing


def test_a_dead_ticker_yields_nothing_rather_than_raising(monkeypatch):
    """A symbol Yahoo knows nothing about must degrade to None so the API
    can answer, not raise out of a request handler."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: None)

    assert history.get_history("NOPE") is None


def test_a_stale_frame_survives_yahoo_going_down(monkeypatch):
    """Once a ticker has been charted, a failed refetch should leave the
    chart as it was. Blanking it would suggest the ticker had stopped
    trading, which is a much stronger claim than 'Yahoo timed out'."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(200))
    first = history.get_history("AAA")

    monkeypatch.setattr(history, "_fetch_history", lambda t: None)
    again = history.get_history("AAA", force=True)

    assert again is not None
    assert again.fetched_at == first.fetched_at


# --- the endpoint -----------------------------------------------------


def add_signal(db, ticker, **kw):
    fields = dict(
        as_of_date=date(2026, 8, 11),
        close=200.0,
        prev_close=199.0,
        donchian_high=210.0,
        donchian_low=190.0,
        atr=2.0,
        stop_distance=4.0,
        entry_signal=False,
        exit_signal=False,
        updated_at=utcnow(),
    )
    fields.update(kw)
    db.add(Signal(ticker=ticker, **fields))
    db.commit()


def test_no_nan_reaches_the_json_payload(client, db, monkeypatch):
    """float('nan') is not valid JSON — it serializes as a bare NaN token
    that JSON.parse rejects, so a single warm-up value would white-screen
    the whole page instead of leaving one gap in one line."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(120))
    add_signal(db, "AAA")

    body = client.get("/api/ticker/AAA?range=MAX").content
    assert b"NaN" not in body

    bars = json.loads(body)["bars"]
    # The 55-bar warm-up really is in this window, and it travels as null.
    assert bars["donchian_high"][0] is None
    assert bars["donchian_high"][-1] is not None


def test_the_served_signal_block_matches_the_stored_end_of_day_row(client, db, monkeypatch):
    """Yahoo's daily history includes the session in progress. Reading the
    levels off it would print an exit on an intraday dip that recovers by
    the close — a different strategy from the one the backtest measured,
    and the dashboard card would disagree with the chart's own heading."""
    # A frame whose last bars crash well below the stored 20-day low.
    closes = [200.0] * 100 + [100.0] * 5
    idx = pd.bdate_range(end="2026-08-11", periods=len(closes))
    crashed = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=idx,
    )
    monkeypatch.setattr(history, "_fetch_history", lambda t: crashed)
    add_signal(db, "AAA", exit_signal=False, close=200.0)

    stats = client.get("/api/ticker/AAA").json()["stats"]

    assert stats["exit_signal"] is False
    assert stats["signal_source"] == "pipeline"
    assert stats["close"] == 200.0


def test_an_in_progress_bar_never_carries_a_signal_marker(client, db, monkeypatch):
    """A partial candle can be drawn — dimmed, and labelled as such — but a
    triangle on it would assert that a signal fired, which today's session
    has not yet earned."""
    from app import main

    # The endpoint decides "partial" by comparing the last bar with
    # main's `date.today()`, so pin that clock to a fixed weekday and end
    # the frame on it. Built off the real today instead, a weekend run's
    # last business day is Friday — a finished session — and the test
    # failed every Saturday and Sunday.
    session = date(2026, 8, 11)  # a Tuesday

    class FixedDate(date):
        @classmethod
        def today(cls):
            return session

    monkeypatch.setattr(main, "date", FixedDate)

    # The step has to exceed the high/low padding or the close only ever
    # equals the prior high and nothing breaks out — which would make the
    # last assertion below pass for the wrong reason.
    closes = [100.0 + 2 * i for i in range(120)]
    idx = pd.bdate_range(end=session, periods=len(closes))
    frame = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=idx,
    )
    monkeypatch.setattr(history, "_fetch_history", lambda t: frame)
    db.add(Universe(ticker="AAA", asset_class="equity", active=True))
    db.commit()

    body = client.get("/api/ticker/AAA?range=MAX").json()

    assert body["partial_last_bar"] is True
    last = len(body["bars"]["dates"]) - 1
    assert last not in body["bars"]["entry_index"]
    # The rising series does break out, so this isn't vacuously true.
    assert len(body["bars"]["entry_index"]) > 0


def test_a_marker_lands_on_the_crossing_not_on_every_bar_it_holds(client, db, monkeypatch):
    """A steadily rising series sits above its 55-day high on every bar once
    it clears it. Marking the state rather than the crossing would put a
    triangle on hundreds of bars and answer "is it above the level" — where
    the question the chart is asked is "where would you have bought"."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(300, step=2.0))
    add_signal(db, "AAA")

    bars = client.get("/api/ticker/AAA?range=MAX").json()["bars"]

    assert len(bars["entry_index"]) == 1
    # And it's the first bar that could have broken out — right after the
    # 55-bar warm-up, not somewhere arbitrary.
    assert bars["donchian_high"][bars["entry_index"][0] - 1] is None


def test_a_run_starting_before_the_window_is_not_re_marked(client, db, monkeypatch):
    """Run starts are found on the full history, then sliced. Computed on the
    visible window instead, a breakout from two years ago would print a fresh
    triangle at the left edge of every shorter range."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(800, step=2.0))
    add_signal(db, "AAA")

    short = client.get("/api/ticker/AAA?range=6M").json()["bars"]

    assert short["entry_index"] == []


def test_shares_from_the_signal_frame_are_never_served(client, db, monkeypatch):
    """compute_signal_frame's Shares column is computed against a placeholder
    equity and doesn't compound. Sizing has to resolve from the real account
    at read time, or changing your cash balance wouldn't move the number."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(120))
    add_signal(db, "AAA", stop_distance=4.0)

    body = client.get("/api/ticker/AAA").json()
    assert "Shares" not in json.dumps(body)

    before = body["stats"]["suggested_shares"]
    client.patch("/api/account", json={"equity_override": 500_000})
    after = client.get("/api/ticker/AAA").json()["stats"]["suggested_shares"]

    assert before != after


def test_a_futures_ticker_survives_the_url_path(client, db, monkeypatch):
    """GC=F is how bullion is tracked here. The '=' arrives percent-encoded
    and has to survive both the route match and the pattern check."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(120))
    add_signal(db, "GC=F")

    body = client.get("/api/ticker/GC%3DF").json()
    assert body["ticker"] == "GC=F"
    assert len(body["bars"]["dates"]) > 0


def test_a_known_ticker_survives_yahoo_being_down(client, db, monkeypatch):
    """A 404 on a ticker you hold would say it doesn't exist. The page still
    has stored levels to show, so it renders them and says the chart is the
    part that's missing."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: None)
    add_signal(db, "AAA", close=200.0)

    response = client.get("/api/ticker/AAA")

    assert response.status_code == 200
    body = response.json()
    assert body["history_available"] is False
    assert body["bars"]["dates"] == []
    assert body["stats"]["close"] == 200.0


def test_an_unknown_ticker_with_no_history_is_a_404(client, db, monkeypatch):
    """Nothing stored and nothing fetchable is the one case where the ticker
    genuinely isn't a thing this instance can chart."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: None)

    assert client.get("/api/ticker/NOPE").status_code == 404


def test_a_malformed_ticker_is_rejected_before_any_fetch(client, monkeypatch):
    """The pattern check is what stops a pasted sentence becoming an
    outbound request."""
    def explode(ticker):
        raise AssertionError("should never be reached")

    monkeypatch.setattr(history, "_fetch_history", explode)

    assert client.get("/api/ticker/not%20a%20ticker").status_code == 422


def test_a_ticker_outside_the_universe_is_labelled_as_computed(client, db, monkeypatch):
    """Levels derived here haven't been through a daily run. Saying so is
    the difference between 'the pipeline scanned this' and 'we worked it out
    on the spot because you typed it into the URL'."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(200))
    db.add(Holding(ticker="AAA", shares=10, added_at=utcnow()))
    db.commit()

    stats = client.get("/api/ticker/AAA").json()["stats"]

    assert stats["signal_source"] == "computed"
    assert stats["donchian_high"] is not None


def test_the_range_parameter_changes_how_much_is_returned(client, db, monkeypatch):
    """The range buttons are the whole reason one fetch covers a decade."""
    monkeypatch.setattr(history, "_fetch_history", lambda t: rising(1200))
    add_signal(db, "AAA")

    short = len(client.get("/api/ticker/AAA?range=6M").json()["bars"]["dates"])
    long_ = len(client.get("/api/ticker/AAA?range=MAX").json()["bars"]["dates"])

    assert 0 < short < long_
    assert client.get("/api/ticker/AAA?range=NOPE").status_code == 422
