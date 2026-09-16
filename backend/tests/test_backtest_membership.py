"""
Point-in-time membership and the index-change overlay.

Offline. The schedule tests pin how dated change events become a universe
— the place where a mistake silently changes which companies a backtest
sees. The engine tests pin when the overlay trades: an announcement is
known after one bar's close and filled at the next bar's open, like any
other signal.
"""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.backtest.__main__ import main, resolve_universe, universe_label
from app.backtest.config import BacktestConfig
from app.backtest.engine import run_backtest
from app.backtest.membership import (
    EventFile,
    IndexEvent,
    align,
    announcement_bar,
    build_schedule,
    load_events,
)
from app.backtest.universe import load_membership
from app.config import is_point_in_time

FAST = dict(n_entry=5, m_exit=3, atr_period=3)
UNIVERSES = Path(__file__).resolve().parent.parent / "data" / "universes"


def cfg(**overrides):
    base = dict(
        start=date(2000, 1, 1), end=date(2100, 1, 1), initial_equity=100_000.0,
        risk_fraction=0.01, cost_bps=0.0, slippage_bps=0.0,
        max_position_fraction=1.0, min_notional=0.0, **FAST,
    )
    base.update(overrides)
    return BacktestConfig(**base)


def ev(announced, effective, added="", removed="", kind="change"):
    return IndexEvent(date.fromisoformat(announced), date.fromisoformat(effective), added, removed, kind)


def schedule_of(events, anchor, as_of="2030-01-01", start="2000-01-01"):
    return build_schedule(
        EventFile(events=events, anchor=frozenset(anchor), anchor_as_of=date.fromisoformat(as_of)),
        start=date.fromisoformat(start),
    )


# --- building the schedule --------------------------------------------


def test_replaying_backwards_recovers_the_starting_list():
    schedule = schedule_of(
        [ev("2020-06-10", "2020-06-20", added="NEW", removed="OLD")],
        anchor={"A", "B", "NEW"}, as_of="2020-12-31", start="2020-01-01",
    )

    assert schedule.initial == {"A", "B", "OLD"}
    [switch] = schedule.switches
    assert (switch.announced, switch.added, switch.removed) == (date(2020, 6, 10), "NEW", "OLD")
    assert switch.tradeable
    assert schedule.warnings == []


def test_a_change_announced_before_the_start_has_already_happened():
    """Willis Towers Watson for Fossil: announced 2015-12-28, effective
    2016-01-05. Under announcement timing it is already done on the first
    day of a 2016 run — and there is no announcement left to trade."""
    schedule = schedule_of(
        [ev("2015-12-28", "2016-01-05", added="WLTW", removed="FOSL")],
        anchor={"WLTW"}, start="2016-01-01",
    )

    assert schedule.initial == {"WLTW"}
    assert schedule.switches == []


def test_a_rename_rekeys_older_events_and_trades_nothing():
    """FleetCor joined in 2018 as FLT and became Corpay (CPAY) in 2024.
    Yahoo only has CPAY, so the 2018 addition must be recorded as CPAY —
    and the rename itself must not read as a sale and a purchase."""
    schedule = schedule_of(
        [
            ev("2018-06-13", "2018-06-20", added="FLT", removed="TWX"),
            ev("2024-03-25", "2024-03-25", added="CPAY", removed="FLT", kind="continuation"),
        ],
        anchor={"CPAY", "A"}, start="2016-01-01",
    )

    assert schedule.initial == {"A", "TWX"}
    [switch] = schedule.switches
    assert (switch.added, switch.removed) == ("CPAY", "TWX")


def test_a_rename_only_rekeys_events_older_than_itself():
    """DOW is two companies: Dow Chemical (renamed DowDuPont in 2017, now
    DD) and the unrelated Dow Inc. spun off in 2019. The 2017 rename must
    not swallow the 2019 addition."""
    schedule = schedule_of(
        [
            ev("2017-09-01", "2017-09-01", added="DD", removed="DOW", kind="continuation"),
            ev("2019-03-20", "2019-04-02", added="DOW", removed="BHF"),
        ],
        anchor={"DD", "DOW"}, start="2016-01-01",
    )

    assert schedule.initial == {"DD", "BHF"}
    [switch] = schedule.switches
    assert switch.added == "DOW"


def test_a_pending_change_is_applied_forward_not_undone():
    """Announced before the anchor date but effective after it: the anchor
    list doesn't contain it yet, so undoing it would corrupt the replay."""
    schedule = schedule_of(
        [ev("2026-09-04", "2026-09-21", added="BE", removed="TAP")],
        anchor={"TAP", "A"}, as_of="2026-09-12", start="2026-01-01",
    )

    assert schedule.initial == {"TAP", "A"}
    assert [(s.added, s.removed) for s in schedule.switches] == [("BE", "TAP")]
    assert schedule.warnings == []


def test_an_undated_change_moves_membership_but_is_never_traded(tmp_path):
    """A news trade on a guessed date is what the events file exists to
    prevent, so an undated row switches on its effective date instead."""
    (tmp_path / "anchor.txt").write_text("NEW\n")
    path = tmp_path / "events.csv"
    path.write_text(
        "# anchor: anchor.txt as_of 2030-01-01\n"
        "announced,effective,added,removed,kind,date_source,note\n"
        ",2020-06-20,NEW,OLD,change,none,\n"
    )

    [event] = load_events(path).events

    assert event.kind == "no_trade"
    assert event.announced == date(2020, 6, 20)


def test_the_committed_file_replays_to_the_archived_2016_list():
    """The cross-check that makes the events file trustworthy: replayed
    backwards from today's list to January 2016, it must agree with the
    independently archived snapshot in sp500_2016.txt — except where a
    company has since changed ticker, which is exactly how Yahoo keys it.

    If this fails after an edit to the events file, a change was dropped,
    duplicated or mis-keyed."""
    snapshot_to_replay = {
        "AA": "HWM", "ABC": "COR", "ANTM": "ELV", "BBT": "TFC", "BHI": "BKR", "BK": "BNY",
        "BLL": "BALL", "CBG": "CBRE", "CBS": "PSKY", "COG": "CTRA", "COH": "TPR",
        "CTL": "LUMN", "DISCA": "WBD", "DLPH": "APTV", "EQR": "VMRK", "FB": "META",
        "HCN": "WELL", "HCP": "DOC", "HRS": "LHX", "IR": "TT", "JEC": "J", "KORS": "CPRI",
        "LB": "BBWI", "LUK": "JEF", "MHFI": "SPGI", "MMC": "MRSH", "MYL": "VTRS",
        "PCLN": "BKNG", "PKI": "RVTY", "PX": "LIN", "SYMC": "GEN", "TMK": "GL",
        "TSO": "ANDV", "UA": "UAA", "UTX": "RTX", "WRK": "SW",
        # Not a rename: WTW replaced Fossil on an announcement made before
        # the start (see the test above).
        "FOSL": "WTW",
    }
    # Dow Chemical and the old DuPont collapse into one DD — two companies,
    # one Yahoo ticker. Documented in backend/README.md.
    collapsed = {"DOW"}

    schedule = build_schedule(load_events(UNIVERSES / "sp500_events.csv"), start=date(2016, 1, 4))
    snapshot = set(load_membership(UNIVERSES / "sp500_2016.txt"))

    assert snapshot - schedule.initial == set(snapshot_to_replay) | collapsed
    assert schedule.initial - snapshot == set(snapshot_to_replay.values())


def test_every_tradeable_change_in_the_committed_file_is_dated_before_it_takes_effect():
    source = load_events(UNIVERSES / "sp500_events.csv")
    changes = [e for e in source.events if e.kind == "change"]

    assert len(changes) > 200
    leads = [np.busday_count(e.announced, e.effective) for e in changes]
    # Measured 1-12 sessions when built. Zero would mean an effective date
    # standing in for an announcement; a long lead, a mis-cited release.
    assert min(leads) >= 1 and max(leads) <= 25


# --- aligning to the calendar -----------------------------------------


def test_a_weekend_announcement_lands_on_the_friday_bar():
    """Friday's bar is the decision bar, so the fill is Monday's open —
    the first open after the news."""
    calendar = pd.bdate_range("2026-09-01", "2026-09-10")

    assert calendar[announcement_bar(calendar, date(2026, 9, 5))] == pd.Timestamp("2026-09-04")
    assert calendar[announcement_bar(calendar, date(2026, 9, 4))] == pd.Timestamp("2026-09-04")
    assert announcement_bar(calendar, date(2026, 8, 1)) == -1


def test_a_company_added_removed_and_readded_is_a_member_exactly_twice():
    calendar = pd.bdate_range("2026-01-05", periods=10)
    schedule = schedule_of(
        [
            ev("2026-01-06", "2026-01-08", added="X"),
            ev("2026-01-09", "2026-01-12", removed="X"),
            ev("2026-01-13", "2026-01-15", added="X"),
        ],
        anchor={"X"}, start="2026-01-01",
    )

    row = align(schedule, calendar, ["X"])["X"]

    assert row.member.tolist() == [False, True, True, True, False, False, True, True, True, True]
    assert row.added.nonzero()[0].tolist() == [1, 6]
    assert row.removed.nonzero()[0].tolist() == [4]


# --- the engine -------------------------------------------------------


def day(frame, i):
    return frame.index[i].date().isoformat()


def test_an_addition_announced_on_one_bar_is_bought_at_the_next_open(ohlc):
    """No breakout anywhere — the buy is the announcement's alone. The
    announcement bar closes at 100 and the next bar opens at 130, so a fill
    at the announcement close would be visible."""
    closes = [100.0] * 12
    opens = [100.0] * 9 + [130.0, 100.0, 100.0]
    frame = ohlc(closes, opens=opens)
    schedule = schedule_of([ev(day(frame, 8), day(frame, 11), added="NEW")], anchor={"NEW"},
                           start=day(frame, 0))

    bought = run_backtest({"NEW": frame}, cfg(index_overlay=True), membership=schedule)
    watched = run_backtest({"NEW": frame}, cfg(), membership=schedule)

    [trade] = bought.trades
    assert trade.entry_reason == "index_addition"
    assert trade.entry_date == frame.index[9].date()
    assert trade.entry_price == pytest.approx(130.0)
    assert bought.overlay == {"bought": 1}
    # Membership alone never buys anything the strategy didn't signal.
    assert watched.trades == []


def test_a_removal_announcement_sells_at_the_next_open(ohlc):
    """A rising series the Donchian exit would hold to the end. The
    removal is announced on bar 15 and must be sold at bar 16's open."""
    closes = [100.0] * 10 + [120.0 + 5 * i for i in range(10)]
    opens = list(closes)
    opens[16] = 111.0
    frame = ohlc(closes, opens=opens)
    schedule = schedule_of([ev(day(frame, 15), day(frame, 18), removed="HELD")], anchor=set(),
                           start=day(frame, 0))

    result = run_backtest({"HELD": frame}, cfg(index_overlay=True), membership=schedule)
    held = run_backtest({"HELD": frame}, cfg(), membership=schedule)

    [trade] = result.trades
    assert trade.exit_reason == "index_removal"
    assert trade.exit_date == frame.index[16].date()
    assert trade.exit_price == pytest.approx(111.0)
    # Without the overlay, a removal only stops new entries.
    assert held.trades[0].exit_reason == "end_of_backtest"


def test_a_breakout_counts_only_while_the_company_is_a_member(ohlc):
    """EARLY breaks out one bar before its addition is announced; ONTIME
    breaks out on the announcement bar itself. Both breakouts last a single
    bar. Only ONTIME was a member when its decision was made — and an ETF
    outside the schedule is never restricted at all."""
    breakout_once = [100.0] * 12 + [200.0] + [100.0] * 4
    early = ohlc(breakout_once)
    ontime = ohlc(breakout_once)
    etf = ohlc(breakout_once)
    schedule = schedule_of(
        [ev(day(early, 13), day(early, 15), added="EARLY"),
         ev(day(ontime, 12), day(ontime, 15), added="ONTIME")],
        anchor={"EARLY", "ONTIME"}, start=day(early, 0),
    )

    result = run_backtest({"EARLY": early, "ONTIME": ontime, "ETF": etf}, cfg(),
                          membership=schedule)

    assert sorted(t.ticker for t in result.trades) == ["ETF", "ONTIME"]


def test_an_announcement_gets_the_last_slot_ahead_of_a_breakout(ohlc):
    breakout = ohlc([100.0] * 12 + [200.0, 205.0, 210.0])
    quiet = ohlc([100.0] * 15)
    schedule = schedule_of([ev(day(quiet, 12), day(quiet, 14), added="NEWS")],
                           anchor={"NEWS", "BRK"}, start=day(quiet, 0))
    frames = {"BRK": breakout, "NEWS": quiet}

    overlay = run_backtest(frames, cfg(index_overlay=True, max_positions=1), membership=schedule)
    plain = run_backtest(frames, cfg(max_positions=1), membership=schedule)

    assert [t.ticker for t in overlay.trades] == ["NEWS"]
    assert [t.ticker for t in plain.trades] == ["BRK"]


def test_a_news_buy_the_account_cannot_fund_is_reported_as_no_cash(ohlc):
    """The measured reason the overlay rarely trades: a trend book sits
    ~96% invested, so the announcement finds no cash. That must be
    reported as a cash problem, not as something unsizable."""
    rising = ohlc([100.0] * 12 + [200.0, 205.0, 210.0, 215.0, 220.0])
    quiet = ohlc([100.0] * 17)
    schedule = schedule_of([ev(day(quiet, 14), day(quiet, 16), added="NEWS")],
                           anchor={"NEWS", "BRK"}, start=day(quiet, 0))

    result = run_backtest({"BRK": rising, "NEWS": quiet}, cfg(index_overlay=True, risk_fraction=1.0),
                          membership=schedule)

    assert [t.ticker for t in result.trades] == ["BRK"]
    assert result.overlay == {"no_cash": 1}


def test_a_spin_off_with_no_history_yet_is_reported_not_bought(ohlc):
    """Spin-offs are announced before they trade: no bars, so no ATR to
    size by. The overlay must skip it and say why, not invent a price."""
    anchor_frame = ohlc([100.0] * 15)
    spin = ohlc([100.0] * 3)  # first bar two sessions after the announcement
    schedule = schedule_of([ev(day(anchor_frame, 10), day(anchor_frame, 12), added="SPIN")],
                           anchor={"SPIN"}, start=day(anchor_frame, 0))

    result = run_backtest({"OTHER": anchor_frame, "SPIN": spin}, cfg(index_overlay=True),
                          membership=schedule)

    assert result.trades == []
    assert result.overlay == {"no_price_history": 1}


def test_an_addition_with_no_price_data_at_all_is_counted(ohlc):
    frame = ohlc([100.0] * 15)
    schedule = schedule_of([ev(day(frame, 10), day(frame, 12), added="GONE")],
                           anchor={"GONE"}, start=day(frame, 0))

    result = run_backtest({"OTHER": frame}, cfg(index_overlay=True), membership=schedule)

    assert result.overlay == {"no_price_data": 1}


def test_a_schedule_that_never_binds_changes_nothing(ohlc):
    """Every ticker always a member and no announcements: the run must be
    bit-for-bit the run with no schedule, or the membership plumbing is
    changing results by itself."""
    frames = {
        "AAA": ohlc([100.0] * 12 + [200.0, 150.0, 90.0, 95.0]),
        "BBB": ohlc([100.0] * 12 + [180.0, 185.0, 190.0, 195.0]),
    }
    schedule = schedule_of([], anchor={"AAA", "BBB"})

    with_schedule = run_backtest(frames, cfg(index_overlay=True, max_positions=1),
                                 membership=schedule)
    without = run_backtest(frames, cfg(max_positions=1))

    pd.testing.assert_series_equal(with_schedule.equity, without.equity)
    assert [(t.ticker, t.entry_date, t.exit_date) for t in with_schedule.trades] == \
           [(t.ticker, t.entry_date, t.exit_date) for t in without.trades]


def test_truncation_invariance_with_the_overlay(ohlc):
    """The general look-ahead detector, with an addition and a removal
    inside the overlap: appending violent future bars must not move a
    single earlier equity point."""
    closes = [100.0] * 8 + [101.0, 102.0, 104.0, 107.0, 111.0, 116.0]
    short_end = pd.Timestamp("2026-08-11")
    long_end = short_end + pd.tseries.offsets.BDay(3)
    short = ohlc(closes, end=short_end)
    long = ohlc(closes + [2000.0, 2500.0, 3000.0], end=long_end)
    assert short.index[0] == long.index[0]
    schedule = schedule_of(
        [ev(day(short, 6), day(short, 8), added="AAA"),
         ev(day(short, 11), day(short, 13), removed="AAA")],
        anchor=set(), start=day(short, 0),
    )

    a = run_backtest({"AAA": short}, cfg(index_overlay=True), membership=schedule)
    b = run_backtest({"AAA": long}, cfg(index_overlay=True), membership=schedule)

    assert [t.exit_reason for t in a.trades] == ["index_removal"]
    pd.testing.assert_series_equal(a.equity, b.equity.loc[a.equity.index], check_freq=False)


def test_the_overlay_without_a_schedule_is_refused(ohlc):
    with pytest.raises(ValueError, match="membership schedule"):
        run_backtest({"AAA": ohlc([100.0] * 12)}, cfg(index_overlay=True))


# --- the CLI ----------------------------------------------------------


def write_event_file(tmp_path):
    (tmp_path / "anchor.txt").write_text("AAA\nNEW\n")
    path = tmp_path / "sp500_events.csv"
    path.write_text(
        "# anchor: anchor.txt as_of 2030-01-01\n"
        "announced,effective,added,removed,kind,date_source,note\n"
        "2020-06-10,2020-06-20,NEW,OLD,change,press_archive,\n"
    )
    return path


def test_an_events_universe_contributes_every_member_since_the_start(tmp_path):
    path = write_event_file(tmp_path)

    tradeable, _ = resolve_universe(f"events:{path},etfs", start=date(2016, 1, 1))

    assert {"AAA", "NEW", "OLD", "SPUS"} <= set(tradeable)


def test_an_events_run_is_labelled_so_the_dashboard_cannot_mistake_it():
    """Neither of the dashboard's two S&P runs: not today's list, and not
    the frozen 2016 floor whose wording says 'understates'."""
    label = universe_label("events:data/universes/sp500_events.csv,etfs")

    assert label == "sp500_events,etfs"
    assert not is_point_in_time(label)


def test_saving_the_overlay_run_is_refused(tmp_path, capsys):
    """The dashboard has no place for the announcement-trading run, and
    the live pipeline has no feed to trade it. Refused before any price is
    loaded; the membership-only run is still saveable."""
    path = write_event_file(tmp_path)

    assert main(["--universe", f"events:{path}", "--index-overlay", "--save", "--offline"]) == 2
    assert "--save is refused" in capsys.readouterr().out


def _stored_run(db, universe, total_return):
    from app.models import BacktestRun

    run = BacktestRun(
        label=universe, universe=universe, strategy="donchian", variant="faithful",
        start_date=date(2016, 1, 4), end_date=date(2026, 8, 11),
        summary_json=f'{{"total_return": {total_return}}}', config_json="{}",
    )
    db.add(run)
    db.commit()
    return run


def test_an_events_membership_run_is_a_membership_run_too(client, db):
    """Either membership source makes the headline (see
    test_backtest_constituents.py); today's list becomes the comparison
    curve, and the caveat stops claiming two lists bracket the truth."""
    _stored_run(db, "sp500,etfs", 8.63)
    _stored_run(db, "sp500_2016,etfs", 2.02)
    _stored_run(db, "sp500_events,etfs", 1.77)

    body = client.get("/api/backtest").json()

    assert body["faithful"]["universe"] == "sp500_events,etfs"
    assert body["current_list"]["universe"] == "sp500,etfs"
    assert body["point_in_time"]["universe"] == "sp500_2016,etfs"
    caveats = " ".join(body["caveats"])
    assert "only while it was actually in the S&P 500" in caveats
    assert "lies between" not in caveats


def test_the_overlay_flag_needs_an_events_universe(capsys):
    assert main(["--universe", "AAPL", "--index-overlay", "--offline"]) == 2
