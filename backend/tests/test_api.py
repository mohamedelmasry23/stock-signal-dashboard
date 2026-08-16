"""End-to-end tests through the HTTP layer."""

import json
from datetime import date, datetime

import pytest

from app.models import BacktestEquityPoint, BacktestRun, Holding, RsiSignal, Signal
from app.timeutil import utcnow


def add_signal(db, ticker, close, entry=False, exit_=False, donchian_high=None, atr=1.0):
    db.add(
        Signal(
            ticker=ticker,
            as_of_date=date(2026, 8, 11),
            close=close,
            donchian_high=donchian_high,
            donchian_low=close - 10,
            atr=atr,
            stop_distance=2 * atr,
            entry_signal=entry,
            exit_signal=exit_,
            updated_at=utcnow(),
        )
    )
    db.commit()


def test_health_and_status(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert "signals_last_updated" in client.get("/api/status").json()


def test_timestamps_carry_an_explicit_utc_offset(client, db):
    """Serialized bare, these would be read as local time by JavaScript's
    Date(), skewing every timestamp in the frontend by the UTC offset and
    breaking the stale-data check."""
    add_signal(db, "GLD", close=400.0)

    updated = client.get("/api/status").json()["signals_last_updated"]
    assert updated.endswith("+00:00")

    client.post("/api/holdings", json={"ticker": "GLD", "shares": 1})
    assert client.get("/api/holdings").json()[0]["added_at"].endswith("+00:00")


def test_holdings_crud_roundtrip(client):
    created = client.post("/api/holdings", json={"ticker": "gld", "shares": 10})
    assert created.status_code == 201
    assert created.json()["ticker"] == "GLD"  # normalised to upper case

    assert client.patch("/api/holdings/GLD", json={"shares": 25}).json()["shares"] == 25
    assert client.delete("/api/holdings/GLD").status_code == 204
    assert client.get("/api/holdings").json() == []


def test_duplicate_ticker_is_rejected_with_a_usable_message(client):
    client.post("/api/holdings", json={"ticker": "GLD"})
    conflict = client.post("/api/holdings", json={"ticker": "GLD"})

    assert conflict.status_code == 409
    assert "already in holdings" in conflict.json()["detail"]


@pytest.mark.parametrize(
    "ticker",
    ["", "THIS IS NOT A TICKER", "ABCDEFGHIJKLMNOP", "AA;DROP TABLE", "GL D"],
)
def test_malformed_tickers_are_rejected(client, ticker):
    assert client.post("/api/holdings", json={"ticker": ticker}).status_code == 422


@pytest.mark.parametrize("ticker", ["BRK.B", "SHOP-A", "gld", "A", "GC=F", "^GSPC"])
def test_valid_ticker_shapes_are_accepted(client, ticker):
    """'=' and '^' are in the allowed set for futures/FX and index symbols.
    GC=F specifically is how bullion held by weight is tracked, so
    rejecting it would make per-ounce holdings unrepresentable."""
    assert client.post("/api/holdings", json={"ticker": ticker}).status_code == 201


def test_futures_ticker_survives_the_url_path(client, db):
    """A ticker with '=' has to round-trip through the path segment of
    PATCH/DELETE, where it arrives percent-encoded."""
    client.post("/api/holdings", json={"ticker": "GC=F", "shares": 3})

    assert client.patch("/api/holdings/GC%3DF", json={"shares": 5}).json()["shares"] == 5
    assert client.delete("/api/holdings/GC%3DF").status_code == 204
    assert client.get("/api/holdings").json() == []


def test_per_ounce_holding_values_as_amount_times_price(client, db):
    """Bullion is held as ounces of GC=F, so equity must be ounces x the
    per-ounce price — the same arithmetic as shares, which is exactly why
    no special case is needed for it."""
    add_signal(db, "GC=F", close=4000.0)
    client.post("/api/holdings", json={"ticker": "GC=F", "shares": 2.5})

    assert client.get("/api/account").json()["positions_value"] == pytest.approx(10_000.0)


def test_negative_shares_are_rejected(client):
    assert client.post("/api/holdings", json={"ticker": "GLD", "shares": -5}).status_code == 422


def test_holding_without_a_signal_serializes_as_null(client):
    """A ticker added since the last pipeline run has no computed signal.
    The UI relies on this being null rather than a partially-filled object."""
    client.post("/api/holdings", json={"ticker": "NEWQ", "shares": 3})
    assert client.get("/api/holdings").json()[0]["signal"] is None


def test_entry_candidates_exclude_held_tickers(client, db):
    add_signal(db, "AAA", close=110.0, entry=True, donchian_high=100.0)
    add_signal(db, "BBB", close=120.0, entry=True, donchian_high=100.0)

    assert {c["ticker"] for c in client.get("/api/entry-candidates").json()} == {"AAA", "BBB"}

    client.post("/api/holdings", json={"ticker": "AAA"})
    assert [c["ticker"] for c in client.get("/api/entry-candidates").json()] == ["BBB"]


def test_entry_candidates_rank_by_breakout_strength(client, db):
    # strength = (close - donchian_high) / atr
    add_signal(db, "WEAK", close=101.0, entry=True, donchian_high=100.0, atr=1.0)  # 1.0
    add_signal(db, "STRONG", close=110.0, entry=True, donchian_high=100.0, atr=2.0)  # 5.0

    assert [c["ticker"] for c in client.get("/api/entry-candidates").json()] == ["STRONG", "WEAK"]


def test_entry_candidates_limit_is_clamped(client, db):
    for i in range(15):
        add_signal(db, f"T{i:02d}", close=110.0, entry=True, donchian_high=100.0)

    assert len(client.get("/api/entry-candidates?limit=99").json()) == 10
    assert len(client.get("/api/entry-candidates?limit=3").json()) == 3
    assert len(client.get("/api/entry-candidates?limit=0").json()) == 1


def test_position_sizing_reflects_account_changes_without_a_pipeline_run(client, db):
    """The whole point of resolving equity on read: editing the account
    re-sizes every suggestion immediately."""
    add_signal(db, "AAA", close=110.0, entry=True, donchian_high=100.0, atr=5.0)

    before = client.get("/api/entry-candidates").json()[0]["suggested_shares"]
    client.patch("/api/account", json={"equity_override": 200_000})
    after = client.get("/api/entry-candidates").json()[0]["suggested_shares"]

    assert before == pytest.approx(100_000 * 0.01 / 10.0)  # default equity
    assert after == pytest.approx(200_000 * 0.01 / 10.0)
    assert after == pytest.approx(2 * before)


def test_account_override_cleared_by_explicit_null(client):
    """Omitting a field leaves it alone; sending null clears it. Without
    that distinction there'd be no way back to the derived figure."""
    client.patch("/api/account", json={"cash_balance": 5_000})
    assert client.patch("/api/account", json={"equity_override": 50_000}).json()["equity"] == 50_000

    # a PATCH that doesn't mention the override must not disturb it
    assert client.patch("/api/account", json={"cash_balance": 6_000}).json()["equity"] == 50_000

    cleared = client.patch("/api/account", json={"equity_override": None}).json()
    assert cleared["equity_override"] is None
    assert cleared["equity"] == 6_000
    assert cleared["equity_source"] == "derived"


def test_null_cash_does_not_violate_the_not_null_column(client):
    """`{"cash_balance": null}` is well-formed and would otherwise write
    NULL into a NOT NULL column, failing at commit with an opaque
    IntegrityError."""
    response = client.patch("/api/account", json={"cash_balance": None})
    assert response.status_code == 200
    assert response.json()["cash_balance"] == 0.0


@pytest.mark.parametrize("risk", [0, -0.01, 1.5])
def test_out_of_range_risk_is_rejected(client, risk):
    assert client.patch("/api/account", json={"risk_fraction": risk}).status_code == 422


# --- write protection -------------------------------------------------
# security.py binds API_KEY/READ_ONLY at import, so these patch the module
# attributes rather than the environment.


def test_api_key_required_for_writes_when_configured(client, monkeypatch):
    monkeypatch.setattr("app.security.API_KEY", "s3cret")

    assert client.get("/api/holdings").status_code == 200  # reads stay open
    assert client.post("/api/holdings", json={"ticker": "GLD"}).status_code == 401
    assert (
        client.post("/api/holdings", json={"ticker": "GLD"}, headers={"X-API-Key": "wrong"}).status_code
        == 401
    )
    assert (
        client.post("/api/holdings", json={"ticker": "GLD"}, headers={"X-API-Key": "s3cret"}).status_code
        == 201
    )


def test_read_only_blocks_writes_even_with_a_valid_key(client, monkeypatch):
    """READ_ONLY is the public-demo setting and has to be unconditional —
    if a key could bypass it, a leaked key would make the demo writable."""
    monkeypatch.setattr("app.security.API_KEY", "s3cret")
    monkeypatch.setattr("app.security.READ_ONLY", True)

    assert client.get("/api/holdings").status_code == 200
    assert (
        client.post("/api/holdings", json={"ticker": "GLD"}, headers={"X-API-Key": "s3cret"}).status_code
        == 403
    )
    assert client.patch("/api/account", json={"cash_balance": 1}, headers={"X-API-Key": "s3cret"}).status_code == 403
    assert client.delete("/api/holdings/GLD", headers={"X-API-Key": "s3cret"}).status_code == 403


def test_auth_runs_before_body_validation(client, monkeypatch):
    """An unauthenticated caller shouldn't learn anything about the schema
    from a malformed request — the 401 has to come first."""
    monkeypatch.setattr("app.security.API_KEY", "s3cret")

    assert client.post("/api/holdings", json={"ticker": "NOT A TICKER"}).status_code == 401


def add_backtest_run(db, variant, universe, created_at, total_return):
    """A stored run with one equity point, enough for /api/backtest to
    select and serialize it."""
    run = BacktestRun(
        label=f"{universe} {variant}",
        universe=universe,
        variant=variant,
        start_date=date(2016, 1, 4),
        end_date=date(2026, 8, 11),
        config_json=json.dumps({"cost_bps": 5, "slippage_bps": 5}),
        summary_json=json.dumps({"total_return": total_return}),
        benchmark_summary_json=json.dumps({"total_return": 3.53}),
        created_at=created_at,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    db.add(
        BacktestEquityPoint(
            run_id=run.id, as_of_date=date(2016, 1, 4), strategy=100.0, benchmark=100.0
        )
    )
    db.commit()
    return run


def test_point_in_time_run_does_not_displace_the_current_list_run(client, db):
    """The 2016-membership run is the survivorship-bias counterweight to
    the current-list run, so it has to arrive as a third series. Selecting
    purely on 'newest of each variant' would let it replace the very run
    it exists to be compared against — and it is always the newer of the
    two, because it's run afterwards."""
    add_backtest_run(db, "faithful", "sp500,etfs", datetime(2026, 8, 13, 12, 0), 8.63)
    add_backtest_run(db, "faithful", "sp500_2016,etfs", datetime(2026, 8, 17, 12, 0), 2.02)

    body = client.get("/api/backtest").json()

    assert body["faithful"]["universe"] == "sp500,etfs"
    assert body["point_in_time"]["universe"] == "sp500_2016,etfs"
    assert body["faithful"]["summary"]["total_return"] == 8.63
    assert body["point_in_time"]["summary"]["total_return"] == 2.02


def test_survivorship_caveat_reflects_which_runs_are_present(client, db):
    """Alone, the current-list run overstates and the caveat must say so.
    Once the point-in-time run is beside it the claim changes: the pair
    brackets the answer, and a caveat still calling the number optimistic
    would be describing a chart the user isn't looking at."""
    add_backtest_run(db, "faithful", "sp500,etfs", datetime(2026, 8, 13, 12, 0), 8.63)

    alone = " ".join(client.get("/api/backtest").json()["caveats"])
    assert "only companies that survived" in alone
    assert "bracketed" not in alone

    add_backtest_run(db, "faithful", "sp500_2016,etfs", datetime(2026, 8, 17, 12, 0), 2.02)

    both = " ".join(client.get("/api/backtest").json()["caveats"])
    assert "bracketed" in both
    assert "only companies that survived" not in both


# --- RSI(2) pullback page --------------------------------------------------


def add_rsi_signal(db, ticker, close, rsi, entry=False, exit_=False, sma_fast=None, atr=1.0):
    db.add(
        RsiSignal(
            ticker=ticker,
            as_of_date=date(2026, 8, 11),
            close=close,
            rsi=rsi,
            sma_fast=sma_fast if sma_fast is not None else close * 1.02,
            sma_trend=close * 0.9,
            atr=atr,
            stop_distance=2 * atr,
            entry_signal=entry,
            exit_signal=exit_,
            updated_at=utcnow(),
        )
    )
    db.commit()


def test_rsi_candidates_rank_the_most_oversold_first(client, db):
    """Ascending RSI, which is the mirror of ranking Donchian candidates by
    how far ABOVE their breakout they closed. Getting the direction
    backwards would recommend the least oversold names."""
    add_rsi_signal(db, "AAA", close=100.0, rsi=4.5, entry=True)
    add_rsi_signal(db, "BBB", close=50.0, rsi=0.8, entry=True)
    add_rsi_signal(db, "CCC", close=75.0, rsi=2.6, entry=True)

    body = client.get("/api/rsi/entry-candidates").json()

    assert [c["ticker"] for c in body] == ["BBB", "CCC", "AAA"]
    # Depth is measured from the trigger, so the most oversold has the most.
    assert body[0]["oversold_depth"] > body[-1]["oversold_depth"]


def test_rsi_candidates_exclude_what_you_already_hold(client, db):
    """Same rule as the Donchian panel: it's a buy list, and a position you
    already own isn't a new trade."""
    add_rsi_signal(db, "AAA", close=100.0, rsi=1.0, entry=True)
    add_rsi_signal(db, "BBB", close=50.0, rsi=2.0, entry=True)
    client.post("/api/holdings", json={"ticker": "AAA"})

    assert [c["ticker"] for c in client.get("/api/rsi/entry-candidates").json()] == ["BBB"]


def test_rsi_candidates_ignore_non_signalling_rows(client, db):
    """A low RSI alone isn't an entry — the trend filter has already been
    applied upstream, and the endpoint must not second-guess it by ranking
    on RSI regardless of the stored signal."""
    add_rsi_signal(db, "AAA", close=100.0, rsi=0.2, entry=False)
    add_rsi_signal(db, "BBB", close=50.0, rsi=3.0, entry=True)

    assert [c["ticker"] for c in client.get("/api/rsi/entry-candidates").json()] == ["BBB"]


def test_the_two_strategies_can_disagree_about_the_same_holding(client, db):
    """The whole point of a second page. One ticker, one position, two
    verdicts — the RSI route must report its own, not Donchian's."""
    client.post("/api/holdings", json={"ticker": "GLD", "shares": 10})
    add_signal(db, "GLD", close=400.0, exit_=False, donchian_high=380.0)
    add_rsi_signal(db, "GLD", close=400.0, rsi=82.0, exit_=True)

    donchian = client.get("/api/holdings").json()[0]
    rsi = client.get("/api/rsi/holdings").json()[0]

    assert donchian["signal"]["exit_signal"] is False
    assert rsi["signal"]["exit_signal"] is True
    assert rsi["signal"]["rsi"] == 82.0
    # Same position underneath both verdicts.
    assert donchian["ticker"] == rsi["ticker"] == "GLD"
    assert donchian["market_value"] == rsi["market_value"] == 4000.0


def test_rsi_sizing_follows_the_account_not_the_stored_value(client, db):
    """Sizing is recomputed per request on both pages, so a change to
    equity is reflected immediately rather than waiting for the next
    pipeline run."""
    add_rsi_signal(db, "AAA", close=100.0, rsi=1.0, entry=True, atr=2.0)  # stop distance 4

    client.patch("/api/account", json={"equity_override": 100_000, "risk_fraction": 0.01})
    before = client.get("/api/rsi/entry-candidates").json()[0]["suggested_shares"]

    client.patch("/api/account", json={"equity_override": 200_000})
    after = client.get("/api/rsi/entry-candidates").json()[0]["suggested_shares"]

    assert before == 250.0  # 1% of 100k / 4
    assert after == 2 * before


def test_status_reports_each_strategy_separately(client, db):
    """Both are written by one pipeline run, but they diverge whenever one
    strategy's pass fails and the other's doesn't — so each page has to be
    able to say how fresh its own numbers are."""
    add_signal(db, "AAA", close=100.0)

    body = client.get("/api/status").json()
    assert body["signals_last_updated"] is not None
    assert body["rsi_signals_last_updated"] is None

    add_rsi_signal(db, "AAA", close=100.0, rsi=1.0)
    assert client.get("/api/status").json()["rsi_signals_last_updated"] is not None
