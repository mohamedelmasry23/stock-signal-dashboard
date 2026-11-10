"""
Intraday watchlist tests. Offline — `watch._download_batch` is the single
network seam and is patched for every test in this module.

The property under test throughout is that this feature *observes* without
*deciding*. It reads live prices, it never writes to `signals`, and the
level it compares against is recomputed rather than read stale.
"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from app import models, watch
from app.pipeline import NEW_YORK
from app.signals import compute_signal_frame
from app.timeutil import utcnow


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in this module may reach Yahoo. A test that needs data
    overrides this with its own frames."""
    monkeypatch.setattr(watch, "_download_batch", lambda tickers: {})
    monkeypatch.setattr(watch, "RETRY_PAUSE_SEC", 0.0)


def frame_ending(end, periods=120, step=1.0, start=100.0, pad=1.0):
    closes = [start + step * i for i in range(periods)]
    idx = pd.bdate_range(end=end, periods=periods)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + pad for c in closes],
            "Low": [c - pad for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * periods,
        },
        index=idx,
    )


MID_SESSION = datetime(2026, 8, 19, 11, 0, tzinfo=NEW_YORK)


def test_the_level_includes_the_last_completed_session_not_the_one_before_it():
    """The pipeline stores max(High[D-55..D-1]) — the level D's close had to
    beat. The session in progress needs max(High[D-54..D]), which includes
    D's own high. Measured over 492,874 ticker-days the two differ 15.5% of
    the time, and on 2.37% the stale one lists a ticker that is not actually
    above the level it has to beat — about a third of a typical day's list."""
    full = frame_ending("2026-08-19")
    completed = full.iloc[:-1]

    mine = watch.watch_level(completed)

    # Exactly what tomorrow night's pipeline run will store for this session.
    expected = compute_signal_frame(full)["DonchianHigh_N"].iloc[-1]
    assert mine == pytest.approx(float(expected))

    # And genuinely different from the value already stored for yesterday.
    stale = compute_signal_frame(completed)["DonchianHigh_N"].iloc[-1]
    assert mine != pytest.approx(float(stale))


def test_the_in_progress_bar_sets_the_price_but_never_the_level():
    """This module reads one frame two ways, which looks like a hole in the
    drop_in_progress_bar invariant and is not. Nothing else pins the split,
    and collapsing it either way is silently wrong: the level would absorb a
    partial high, or the price would be yesterday's."""
    full = frame_ending("2026-08-19")
    full.iloc[-1, full.columns.get_loc("Close")] = 9_999.0
    full.iloc[-1, full.columns.get_loc("High")] = 9_999.0

    obs = watch.observe("AAA", full, MID_SESSION)

    assert obs.price == 9_999.0                       # partial bar drives price
    assert obs.level == watch.watch_level(full.iloc[:-1])  # but never the level
    assert obs.level < 9_999.0


def test_a_ticker_with_no_bar_for_today_is_skipped_entirely():
    """With no partial bar there is nothing live to say, and the level would
    quietly swallow the last completed session's own high. One condition
    covers holidays, halts and a scan before the first tick."""
    stale_frame = frame_ending("2026-08-14")  # Friday; MID_SESSION is the next Wednesday

    assert watch.observe("AAA", stale_frame, MID_SESSION) is None


def test_a_gap_through_the_level_is_marked_as_one():
    """22.7% of historical breakouts opened already above the level — MRNA's
    case, and the only group where acting early was measurably worth
    anything (+0.96% against +0.04%)."""
    full = frame_ending("2026-08-19")
    level = watch.watch_level(full.iloc[:-1])
    full.iloc[-1, full.columns.get_loc("Open")] = level * 1.5
    full.iloc[-1, full.columns.get_loc("Close")] = level * 1.6

    assert watch.observe("AAA", full, MID_SESSION).gap_open is True

    full.iloc[-1, full.columns.get_loc("Open")] = level * 0.9
    assert watch.observe("AAA", full, MID_SESSION).gap_open is False


def test_a_scan_never_writes_to_the_signals_table(db, monkeypatch):
    """`signals` is what the backtest describes. The whole design rests on
    this scan being unable to touch it. Mirrors
    test_live_prices_do_not_touch_the_signals."""
    db.add(models.Universe(ticker="AAA", asset_class="equity", active=True))
    db.add(
        models.Signal(
            ticker="AAA", as_of_date=date(2026, 8, 18), close=100.0, prev_close=99.0,
            donchian_high=105.0, donchian_low=95.0, atr=2.0, stop_distance=4.0,
            entry_signal=False, exit_signal=False, updated_at=utcnow(),
        )
    )
    db.commit()
    before = {
        c.name: getattr(db.query(models.Signal).one(), c.name)
        for c in models.Signal.__table__.columns
    }

    full = frame_ending("2026-08-19")
    full.iloc[-1, full.columns.get_loc("Close")] = 9_999.0
    monkeypatch.setattr(watch, "_download_batch", lambda t: {"AAA": full})

    watch.run_watch_scan(now_et=MID_SESSION)

    after = {
        c.name: getattr(db.query(models.Signal).one(), c.name)
        for c in models.Signal.__table__.columns
    }
    assert before == after
    assert db.query(models.Signal).count() == 1
    # ...and it really did do its own job, so this isn't vacuous.
    assert db.query(models.WatchItem).filter_by(ticker="AAA").one().price == 9_999.0


def test_a_scan_outside_the_session_window_touches_nothing(db, monkeypatch):
    """launchd reruns a missed calendar job on wake, so this will fire at
    02:00 eventually. And after 16:05 ET the 'partial' bar is a finished
    one — a scan then would compute every level from today's own high."""
    calls = []
    monkeypatch.setattr(watch, "_download_batch", lambda t: calls.append(t) or {})
    db.add(models.Universe(ticker="AAA", asset_class="equity", active=True))
    db.commit()

    watch.run_watch_scan(now_et=datetime(2026, 8, 19, 8, 0, tzinfo=NEW_YORK))
    watch.run_watch_scan(now_et=datetime(2026, 8, 19, 17, 0, tzinfo=NEW_YORK))
    watch.run_watch_scan(now_et=datetime(2026, 8, 22, 12, 0, tzinfo=NEW_YORK))  # Saturday

    assert calls == []
    assert db.query(models.WatchItem).count() == 0
    assert watch.latest_scan(db) is None  # not even a stamp


def test_a_scan_replaces_the_previous_list_rather_than_adding_to_it(db, monkeypatch):
    """A name that has fallen back below its level has to leave the panel.
    Appending would leave it on screen indefinitely, which is the one way a
    live list can be worse than no list."""
    db.add(models.Universe(ticker="AAA", asset_class="equity", active=True))
    db.add(models.WatchItem(ticker="GONE", price=1.0, level=0.5))
    db.commit()

    full = frame_ending("2026-08-19")
    full.iloc[-1, full.columns.get_loc("Close")] = 9_999.0
    monkeypatch.setattr(watch, "_download_batch", lambda t: {"AAA": full})

    watch.run_watch_scan(now_et=MID_SESSION)

    assert {w.ticker for w in db.query(models.WatchItem).all()} == {"AAA"}


def test_a_name_below_its_level_never_reaches_the_list(db, monkeypatch):
    """The panel's one claim is 'above the level'. A scan that fetched a
    ticker successfully but found it below has done its job by omitting it."""
    db.add(models.Universe(ticker="AAA", asset_class="equity", active=True))
    db.commit()

    full = frame_ending("2026-08-19")
    full.iloc[-1, full.columns.get_loc("Close")] = 1.0  # far below the level
    monkeypatch.setattr(watch, "_download_batch", lambda t: {"AAA": full})

    watch.run_watch_scan(now_et=MID_SESSION)

    assert db.query(models.WatchItem).count() == 0
    scan = watch.latest_scan(db)
    assert scan.fetched == 1        # fetched, considered, and rejected
    assert scan.universe_size == 1


def test_a_scan_from_a_previous_session_is_never_served_as_live(db):
    """The clock alone cannot tell yesterday's 15:45 scan from today's, and
    yesterday's list shown at today's open is the most misleading thing this
    panel could do."""
    db.add(
        models.WatchScan(
            id=1, scanned_at=utcnow(), session_date=date(2026, 8, 18),
            universe_size=1, fetched=1, market_state="open",
        )
    )
    db.commit()
    scan = watch.latest_scan(db)

    # Stamped seconds ago, but for the wrong session.
    assert watch.is_stale(scan, MID_SESSION) is True


def test_a_fresh_scan_from_this_session_is_not_stale(db):
    """The mirror of the above — otherwise 'always stale' would pass every
    staleness test and the panel would never present as live."""
    db.add(
        models.WatchScan(
            id=1, scanned_at=utcnow(), session_date=MID_SESSION.date(),
            universe_size=1, fetched=1, market_state="open",
        )
    )
    db.commit()

    assert watch.is_stale(watch.latest_scan(db), MID_SESSION) is False


def test_a_scan_that_has_aged_out_reports_itself_stale(db):
    """The panel's honesty is entirely in this flag; without it a 3pm list
    renders identically at 3am."""
    db.add(
        models.WatchScan(
            id=1, scanned_at=utcnow() - timedelta(minutes=90),
            session_date=MID_SESSION.date(), universe_size=1, fetched=1,
            market_state="open",
        )
    )
    db.commit()

    assert watch.is_stale(watch.latest_scan(db), MID_SESSION) is True


def test_todays_scan_after_the_close_is_stale_but_still_this_session(db):
    """Stale and current-session have to come apart: at 17:00 the 15:45
    scan is too old to present as live, but it is still the last look at
    the session the pipeline is about to judge, so the panel keeps it."""
    db.add(
        models.WatchScan(
            id=1, scanned_at=utcnow() - timedelta(minutes=75),
            session_date=MID_SESSION.date(), universe_size=1, fetched=1,
            market_state="open",
        )
    )
    db.commit()
    evening = MID_SESSION.replace(hour=17)
    scan = watch.latest_scan(db)

    assert watch.is_stale(scan, evening) is True
    assert watch.is_current_session(scan, evening) is True
    # And the next morning it belongs to a previous session.
    assert watch.is_current_session(scan, MID_SESSION + timedelta(days=1)) is False


def test_no_scan_is_never_the_current_session():
    assert watch.is_current_session(None, MID_SESSION) is False


# --- the endpoint -----------------------------------------------------


def add_watch(db, ticker, price, level, atr=2.0, prev_close=None):
    db.add(
        models.WatchItem(
            ticker=ticker, price=price, level=level, atr=atr,
            atrs_above=(price - level) / atr if atr else None,
            prev_close=prev_close, gap_open=False,
        )
    )


def stamp_scan(db, session_date=None, universe_size=3, fetched=3):
    db.add(
        models.WatchScan(
            id=1, scanned_at=utcnow(),
            session_date=session_date or MID_SESSION.date(),
            universe_size=universe_size, fetched=fetched, market_state="open",
        )
    )


def test_the_watchlist_orders_by_atrs_above_like_the_entry_candidates_panel(client, db):
    """Two panels ranking the same tickers differently would read as one of
    them being broken, and the user would have no way to tell which."""
    add_watch(db, "WEAK", price=101.0, level=100.0, atr=2.0)    # 0.5 ATR
    add_watch(db, "STRONG", price=110.0, level=100.0, atr=2.0)  # 5.0 ATR
    add_watch(db, "MID", price=104.0, level=100.0, atr=2.0)     # 2.0 ATR
    add_watch(db, "NOATR", price=200.0, level=100.0, atr=None)  # sorts last, as in entry_candidates
    stamp_scan(db)
    db.commit()

    items = client.get("/api/watch").json()["items"]

    assert [i["ticker"] for i in items] == ["STRONG", "MID", "WEAK", "NOATR"]
    assert items[0]["atrs_above"] == pytest.approx(5.0)


def test_held_tickers_stay_on_the_watchlist_and_are_marked(client, db):
    """The deliberate opposite of test_entry_candidates_exclude_held_tickers.
    That panel is a buy list, so owning something removes it. This one is an
    observation, and a position you hold going back above its 55-day high is
    among the most informative things on the page."""
    add_watch(db, "OWNED", price=110.0, level=100.0)
    add_watch(db, "FREE", price=105.0, level=100.0)
    db.add(models.Holding(ticker="OWNED", shares=5, added_at=utcnow()))
    stamp_scan(db)
    db.commit()

    items = {i["ticker"]: i for i in client.get("/api/watch").json()["items"]}

    assert set(items) == {"OWNED", "FREE"}
    assert items["OWNED"]["held"] is True
    assert items["FREE"]["held"] is False


def test_already_flagged_names_are_marked_from_the_current_signals_row(client, db):
    """Resolved at read time on purpose: the 23:30 pipeline changes this
    answer without a rescan, and a copy stored at scan time would be wrong
    for the fifteen hours in between."""
    add_watch(db, "AAA", price=110.0, level=100.0)
    db.add(
        models.Signal(
            ticker="AAA", as_of_date=date(2026, 8, 18), close=110.0,
            donchian_high=100.0, donchian_low=95.0, atr=2.0, stop_distance=4.0,
            entry_signal=False, exit_signal=False, updated_at=utcnow(),
        )
    )
    stamp_scan(db)
    db.commit()

    assert client.get("/api/watch").json()["items"][0]["already_flagged"] is False

    db.query(models.Signal).filter_by(ticker="AAA").one().entry_signal = True
    db.commit()

    assert client.get("/api/watch").json()["items"][0]["already_flagged"] is True


def test_the_watchlist_is_capped_and_says_how_many_it_left_out(client, db):
    """275 names above their level has been observed. A silently truncated
    list of 20 out of 275 is a shortlist that isn't one — the count is what
    lets the panel say so."""
    for i in range(60):
        add_watch(db, f"T{i:03d}", price=100.0 + i, level=100.0)
    stamp_scan(db, universe_size=547, fetched=540)
    db.commit()

    body = client.get("/api/watch?limit=20").json()

    assert len(body["items"]) == 20
    assert body["above_count"] == 60
    assert body["truncated"] is True

    # limit is clamped to 50, so an absurd request returns 50 of the 60 —
    # and still reports itself truncated, because 10 really are missing.
    wide = client.get("/api/watch?limit=500").json()
    assert len(wide["items"]) == 50
    assert wide["truncated"] is True


def test_the_endpoint_reports_a_previous_sessions_scan_as_stale(client, db):
    """The panel's honesty is entirely in this flag; without it a list built
    at yesterday's close renders identically at today's open."""
    add_watch(db, "AAA", price=110.0, level=100.0)
    stamp_scan(db, session_date=date(2020, 1, 2))
    db.commit()

    assert client.get("/api/watch").json()["stale"] is True


def test_the_endpoint_says_whether_the_scan_is_from_todays_session(client, db, monkeypatch):
    """The panel hides the list outright when this is False. Keyed only on
    `stale`, a stored list from 17 days earlier stayed on screen — dimmed,
    but there — after the schedule silently stopped scanning."""
    monkeypatch.setattr(watch, "market_now_et", lambda: MID_SESSION)
    add_watch(db, "AAA", price=110.0, level=100.0)
    stamp_scan(db, session_date=MID_SESSION.date() - timedelta(days=17))
    db.commit()

    assert client.get("/api/watch").json()["current_session"] is False

    db.query(models.WatchScan).delete()
    stamp_scan(db)
    db.commit()

    assert client.get("/api/watch").json()["current_session"] is True


def test_an_unscanned_instance_says_so_rather_than_showing_nothing(client, db):
    """Zero rows with no stamp means 'never scanned'; zero rows stamped four
    minutes ago means 'nothing is above its level'. The panel has to be able
    to tell those apart, which is why the stamp is its own table."""
    body = client.get("/api/watch").json()

    assert body["items"] == []
    assert body["scanned_at"] is None
    assert body["stale"] is True
    assert body["above_count"] == 0


def test_the_days_move_is_derived_from_the_last_completed_close(client, db):
    """Not Yahoo's regularMarketChangePercent, which is already scaled and
    lives on QuoteOut. Same field name, different provenance — mixing them
    up turns a half-percent day into a 53% one."""
    add_watch(db, "AAA", price=110.0, level=100.0, prev_close=100.0)
    stamp_scan(db)
    db.commit()

    assert client.get("/api/watch").json()["items"][0]["change_percent"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# The retry pass. 2026-11-10 12:31: a scan launched from a battery DarkWake
# fetched 0 of 547 because the Mac slept again two seconds in.


def _universe(db, *tickers):
    for t in tickers:
        db.add(models.Universe(ticker=t, asset_class="equity", active=True))
    db.commit()


def test_tickers_that_came_back_empty_are_fetched_again(db, monkeypatch, capsys):
    _universe(db, "AAA", "BBB")
    full = frame_ending("2026-08-19")
    full.iloc[-1, full.columns.get_loc("Close")] = 9_999.0
    calls = []

    def download(tickers):
        calls.append(list(tickers))
        return {} if len(calls) == 1 else {t: full for t in tickers}

    monkeypatch.setattr(watch, "_download_batch", download)
    watch.run_watch_scan(now_et=MID_SESSION)

    assert calls == [["AAA", "BBB"], ["AAA", "BBB"]]
    assert {w.ticker for w in db.query(models.WatchItem).all()} == {"AAA", "BBB"}
    assert watch.latest_scan(db).fetched == 2
    out = capsys.readouterr().out
    assert "Retry recovered 2 of 2 ticker(s)." in out
    assert "2 of 2 tickers fetched." in out


def test_a_failed_chunk_is_retried_rather_than_skipped(db, monkeypatch):
    _universe(db, "AAA")
    full = frame_ending("2026-08-19")
    calls = []

    def download(tickers):
        calls.append(list(tickers))
        if len(calls) == 1:
            raise ConnectionError("network is down")
        return {"AAA": full}

    monkeypatch.setattr(watch, "_download_batch", download)
    watch.run_watch_scan(now_et=MID_SESSION)

    assert len(calls) == 2
    assert watch.latest_scan(db).fetched == 1


def test_a_ticker_that_downloaded_but_has_no_bar_today_is_not_retried(db, monkeypatch):
    """A holiday or a halt: the data arrived, it just ends yesterday.
    Asking Yahoo again can't change that."""
    _universe(db, "AAA")
    calls = []
    yesterday = frame_ending("2026-08-18")
    monkeypatch.setattr(watch, "_download_batch", lambda t: calls.append(t) or {"AAA": yesterday})

    watch.run_watch_scan(now_et=MID_SESSION)

    assert len(calls) == 1
    assert watch.latest_scan(db).fetched == 0


def test_no_retry_once_the_session_has_closed(db, monkeypatch, capsys):
    """The Mac can sleep through the close. A retry after 16:05 ET would
    observe a finished bar against a pre-close clock."""
    _universe(db, "AAA")
    clock = iter([MID_SESSION, datetime(2026, 8, 19, 16, 30, tzinfo=NEW_YORK)])
    monkeypatch.setattr(watch, "market_now_et", lambda: next(clock))
    calls = []
    monkeypatch.setattr(watch, "_download_batch", lambda t: calls.append(t) or {})

    watch.run_watch_scan()

    assert len(calls) == 1
    assert "Session closed before a retry" in capsys.readouterr().out
