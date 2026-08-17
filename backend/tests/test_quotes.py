"""
Live-quote tests. Offline — Yahoo is never contacted; `_fetch_one` is
patched, which is the only function that touches the network.
"""

import pytest

from app import quotes
from app.models import Holding, Signal
from app.timeutil import utcnow


@pytest.fixture(autouse=True)
def clear_quote_cache():
    """The cache is module-level, so it would otherwise leak between tests."""
    quotes.clear_cache()
    yield
    quotes.clear_cache()


def fake(ticker, price=100.0, change=1.5, prev=98.5, state="REGULAR"):
    return quotes.Quote(
        ticker=ticker, price=price, change_percent=change,
        previous_close=prev, market_state=state,
    )


def test_change_percent_is_not_rescaled(monkeypatch):
    """Yahoo reports 0.5265777 to mean 0.53%, not 52.7%. If anything ever
    multiplies it by 100 to 'convert to a percentage', a half-percent day
    renders as a 53% move — which looks like a crash or a takeover."""
    monkeypatch.setattr(quotes, "_fetch_one", lambda t: fake(t, change=0.5265777))

    quote = quotes.fetch_quotes(["NVDA"])["NVDA"]

    assert quote.change_percent == pytest.approx(0.5265777)
    assert abs(quote.change_percent) < 5  # a plausible daily move, not 52%


def test_a_failing_ticker_does_not_cost_the_others(monkeypatch):
    """A quote is an enhancement. One bad symbol must not blank the rest."""
    monkeypatch.setattr(
        quotes, "_fetch_one", lambda t: None if t == "BAD" else fake(t)
    )

    result = quotes.fetch_quotes(["GOOD", "BAD", "ALSOGOOD"])

    assert set(result) == {"GOOD", "ALSOGOOD"}


def test_cache_coalesces_calls_within_the_window(monkeypatch):
    """One dashboard refresh hits two endpoints; that must be one trip to
    Yahoo, not two."""
    calls = []

    def counted(ticker):
        calls.append(ticker)
        return fake(ticker)

    monkeypatch.setattr(quotes, "_fetch_one", counted)

    quotes.get_quotes(["AAA"])
    quotes.get_quotes(["AAA"])

    assert calls == ["AAA"]


def test_cache_expiry_lets_a_second_refresh_get_fresh_prices(monkeypatch):
    """The cache coalesces; it must not make the Refresh button useless."""
    calls = []
    monkeypatch.setattr(quotes, "_fetch_one", lambda t: (calls.append(t), fake(t))[1])

    quotes.get_quotes(["AAA"])
    quotes._cache_stamp = utcnow().timestamp() - (quotes.CACHE_TTL_SECONDS + 1)
    quotes.get_quotes(["AAA"])

    assert calls == ["AAA", "AAA"]


def test_force_bypasses_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(quotes, "_fetch_one", lambda t: (calls.append(t), fake(t))[1])

    quotes.get_quotes(["AAA"])
    quotes.get_quotes(["AAA"], force=True)

    assert len(calls) == 2


def test_a_ticker_missing_from_the_cache_triggers_a_fetch(monkeypatch):
    """Adding a holding must not be served from a cache that predates it."""
    calls = []
    monkeypatch.setattr(quotes, "_fetch_one", lambda t: (calls.append(t), fake(t))[1])

    quotes.get_quotes(["AAA"])
    quotes.get_quotes(["AAA", "BBB"])

    assert "BBB" in calls


# --- how quotes flow through the API ---------------------------------


def seed_holding(db, ticker, shares, close):
    db.add(Holding(ticker=ticker, shares=shares, added_at=utcnow()))
    db.add(
        Signal(
            ticker=ticker, close=close, prev_close=close * 0.99,
            donchian_low=close * 0.9, stop_distance=2.0,
            entry_signal=False, exit_signal=False, updated_at=utcnow(),
        )
    )
    db.commit()


def test_holdings_without_live_returns_no_quote(client, db):
    seed_holding(db, "AAA", 10, 100.0)

    row = client.get("/api/holdings").json()[0]

    assert row["quote"] is None
    assert row["market_value"] == pytest.approx(1000.0)  # 10 x stored close


def test_live_quote_drives_value_and_change(client, db, monkeypatch):
    seed_holding(db, "AAA", 10, 100.0)
    monkeypatch.setattr(quotes, "_fetch_one", lambda t: fake(t, price=110.0, change=2.25))

    row = client.get("/api/holdings?live=true").json()[0]

    assert row["quote"]["change_percent"] == pytest.approx(2.25)
    assert row["market_value"] == pytest.approx(1100.0)  # 10 x LIVE price


def test_live_prices_do_not_touch_the_signals(client, db, monkeypatch):
    """The strategy is a daily system. A live price may change what a
    position is worth, but never the levels that decide entries and exits —
    otherwise the dashboard stops matching the backtest."""
    seed_holding(db, "AAA", 10, 100.0)
    monkeypatch.setattr(quotes, "_fetch_one", lambda t: fake(t, price=50.0, change=-50.0))

    row = client.get("/api/holdings?live=true").json()[0]

    # Live price 50 is below the 90 exit level, but the flag must not fire.
    assert row["signal"]["close"] == pytest.approx(100.0)
    assert row["signal"]["exit_signal"] is False
    assert row["signal"]["donchian_low"] == pytest.approx(90.0)


def test_account_equity_uses_live_prices(client, db, monkeypatch):
    seed_holding(db, "AAA", 10, 100.0)

    stored = client.get("/api/account").json()
    assert stored["equity"] == pytest.approx(1000.0)
    assert stored["live"] is False

    monkeypatch.setattr(quotes, "_fetch_one", lambda t: fake(t, price=120.0))
    live = client.get("/api/account?live=true").json()

    assert live["equity"] == pytest.approx(1200.0)
    assert live["live"] is True


def test_dashboard_still_works_when_yahoo_fails(client, db, monkeypatch):
    """Yahoo being down must degrade to stored closes, not break the page."""
    seed_holding(db, "AAA", 10, 100.0)

    def explode(_):
        raise RuntimeError("Yahoo is down")

    monkeypatch.setattr(quotes, "_fetch_one", explode)

    response = client.get("/api/holdings?live=true")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["quote"] is None
    assert row["market_value"] == pytest.approx(1000.0)  # fell back to stored


def test_the_long_name_rides_along_on_the_payload_already_fetched(monkeypatch):
    """The detail page's heading needs the ticker's name, and `info` — which
    this module already fetches for the price — carries it. Asking Yahoo
    separately would roughly double that page's latency for a cosmetic
    field, so if a second round trip ever appears, it isn't paying for this.
    """
    calls = []

    def one_info(ticker):
        calls.append(ticker)
        return quotes.Quote(
            ticker=ticker, price=100.0, change_percent=1.0,
            previous_close=99.0, market_state="REGULAR",
            long_name="SPDR Gold Shares",
        )

    monkeypatch.setattr(quotes, "_fetch_one", one_info)

    quote = quotes.fetch_quotes(["GLD"])["GLD"]

    assert quote.long_name == "SPDR Gold Shares"
    assert calls == ["GLD"]
