"""
S&P membership from the constituents workbook — the source every S&P
backtest uses from 2026-09-14 on.

Offline, and none of it needs openpyxl: the builder's logic is tested on
plain rows, and the committed CSV is tested as committed. The rules pinned
here are the ones that silently change which companies a backtest sees:
where an interval ends, which Yahoo ticker a company lands on, and whether
the file still arrives at today's actual index.
"""

from datetime import date
from pathlib import Path

import pandas as pd

from app.backtest.__main__ import main, resolve_membership, resolve_universe, universe_label
from app.backtest.membership import (
    Interval,
    align,
    load_intervals,
    members_on,
    schedule_from_intervals,
)
from app.backtest.sp500_constituents import (
    Rename,
    apply_change_log,
    merge,
    raw_intervals,
    resolve,
    split,
)
from app.backtest.universe import load_membership
from app.config import is_point_in_time, is_rolling_membership

D = date.fromisoformat
UNIVERSES = Path(__file__).resolve().parent.parent / "data" / "universes"


def row(year, ticker, start, end, status="Full Year", company="Co"):
    return {"year": year, "ticker": ticker, "company": company,
            "from": D(start), "to": D(end), "status": status}


def iv(ticker, start, until=None):
    return Interval(ticker, ticker, "", D(start), D(until) if until else None, "test")


# --- the builder ------------------------------------------------------


def test_full_year_rows_run_on_as_one_membership_that_is_still_open():
    rows = [row(y, "A", f"{y}-01-01", f"{y}-12-31") for y in range(2023, 2026)]

    assert raw_intervals(rows) == [("A", "Co", D("2023-01-01"), None)]


def test_a_company_that_vanishes_from_next_years_sheet_left_on_new_years_day():
    rows = [row(2016, "A", "2016-01-01", "2016-12-31")]

    assert raw_intervals(rows) == [("A", "Co", D("2016-01-01"), D("2017-01-01"))]


def test_a_removal_ends_on_its_effective_date_and_a_readdition_starts_again():
    rows = [
        row(2024, "A", "2024-01-01", "2024-12-31"),
        row(2025, "A", "2025-01-01", "2025-03-24", "Removed During Year"),
        row(2025, "B", "2025-06-01", "2025-12-31", "Added During Year"),
    ]
    rows += [row(2025, "A", "2025-09-22", "2025-12-31", "Added During Year")]

    got = sorted(raw_intervals(rows))

    assert got == [
        ("A", "Co", D("2024-01-01"), D("2025-03-24")),
        ("A", "Co", D("2025-09-22"), None),
        ("B", "Co", D("2025-06-01"), None),
    ]


def test_the_change_log_date_wins_over_a_yearly_sheet_that_runs_to_december():
    """The workbook's own defect: BorgWarner left on 2025-03-24 but the
    2025 sheet shows it as a member until 31 December."""
    rows = [row(2025, "BWA", "2025-01-01", "2025-12-31", "Removed During Year")]

    fixed = apply_change_log(rows, {(2025, "BWA", "Removed"): D("2025-03-24")})

    assert raw_intervals(fixed) == [("BWA", "Co", D("2025-01-01"), D("2025-03-24"))]


def test_a_reused_symbol_resolves_by_date():
    """DOW before 2017 is Dow Chemical, whose line is now DD; DOW from 2019
    is Dow Inc. A time-blind alias map would merge two companies."""
    rules = [Rename(D("2017-09-01"), "DOW", "DWDP"), Rename(D("2019-06-03"), "DWDP", "DD")]

    assert resolve("DOW", D("2016-01-01"), rules) == "DD"
    assert resolve("DOW", D("2019-04-02"), rules) == "DOW"


def test_a_symbol_that_changes_away_and_back_lands_on_its_final_ticker():
    rules = [Rename(D("2023-06-07"), "FISV", "FI"), Rename(D("2025-11-11"), "FI", "FISV")]

    assert resolve("FISV", D("2016-01-01"), rules) == "FISV"
    assert resolve("FI", D("2023-06-07"), rules) == "FISV"


def test_a_split_symbol_gives_each_company_its_own_history():
    """Ingersoll-Rand plc became Trane (TT) on the day Gardner Denver took
    the IR symbol; the workbook shows one unbroken IR."""
    stretches = split([("IR", "Ingersoll Rand", D("2016-01-01"), None)],
                      [("IR", "2020-03-02", "")])
    rules = [Rename(D("2020-03-02"), "IR", "TT")]

    keys = [(resolve(t, s, rules), s, u) for t, _, s, u in stretches]

    assert keys == [("TT", D("2016-01-01"), D("2020-03-02")), ("IR", D("2020-03-02"), None)]


def test_a_ticker_change_rekeyed_onto_its_successor_is_one_membership():
    """Includes the one-day seam the workbook leaves between IR's last day
    and Trane's first."""
    got = merge([iv("META", "2016-01-01", "2022-06-09"), iv("META", "2022-06-09"),
                 iv("TT", "2016-01-01", "2020-03-02"), iv("TT", "2020-03-03")])

    assert [(i.ticker, i.member_from, i.member_until) for i in got] == [
        ("META", D("2016-01-01"), None), ("TT", D("2016-01-01"), None),
    ]


def test_a_real_gap_is_not_merged():
    got = merge([iv("A", "2016-01-01", "2018-01-01"), iv("A", "2020-01-01")])

    assert len(got) == 2


# --- the schedule -----------------------------------------------------


def test_intervals_become_the_schedule_the_engine_already_reads():
    schedule = schedule_from_intervals(
        [iv("A", "2016-01-01"), iv("B", "2016-01-01", "2026-01-08"), iv("C", "2026-01-07")],
        start=D("2026-01-01"),
    )
    calendar = pd.bdate_range("2026-01-05", periods=5)  # Mon..Fri

    rows = align(schedule, calendar, ["A", "B", "C"])

    assert schedule.initial == {"A", "B"}
    assert rows["A"].member.all()
    # Out on its effective date: Wednesday is the last decision bar as a member.
    assert rows["B"].member.tolist() == [True, True, True, False, False]
    assert rows["C"].member.tolist() == [False, False, True, True, True]
    # No announcement dates, so nothing for the overlay to trade.
    assert not any(r.added.any() or r.removed.any() for r in rows.values())


def test_a_weekend_effective_date_switches_on_the_friday_bar():
    schedule = schedule_from_intervals([iv("A", "2026-01-10")], start=D("2026-01-01"))
    calendar = pd.bdate_range("2026-01-05", periods=10)

    member = align(schedule, calendar, ["A"])["A"].member

    assert calendar[member.argmax()] == pd.Timestamp("2026-01-09")


# --- the committed file -----------------------------------------------


def test_the_index_holds_about_500_companies_on_every_session():
    """Catches an interval that never closes or one that closes too soon:
    either shows up as the count drifting away from ~503."""
    intervals = load_intervals(UNIVERSES / "sp500_constituents.csv")
    counts = [len(members_on(intervals, d.date()))
              for d in pd.bdate_range("2016-01-04", "2026-09-11")]

    assert 500 <= min(counts) and max(counts) <= 510


def test_the_constituents_file_reaches_the_anchor_list():
    """The workbook ends in 2025; the file continues through 2026 from the
    events file. The stitch has to land on today's actual index, keyed the
    way Yahoo keys it — a miss is a rename or a 2026 change gone wrong."""
    intervals = load_intervals(UNIVERSES / "sp500_constituents.csv")
    anchor = set(load_membership(UNIVERSES / "sp500_anchor.txt"))

    assert members_on(intervals, D("2026-09-12")) == anchor


def test_the_removals_the_workbook_ran_to_december_end_when_sp_said():
    intervals = {(i.ticker, i.member_until) for i in load_intervals(UNIVERSES / "sp500_constituents.csv")}

    for ticker in ("BWA", "CE", "FMC", "TFX"):
        assert (ticker, D("2025-03-24")) in intervals
    assert ("VNO", D("2023-01-05")) in intervals


def test_renamed_companies_carry_their_whole_history_under_todays_ticker():
    intervals = load_intervals(UNIVERSES / "sp500_constituents.csv")
    on_2016 = members_on(intervals, D("2016-01-04"))

    assert {"META", "BNY", "TT", "DD", "VMRK"} <= on_2016
    assert not {"FB", "BK", "EQR"} & on_2016
    # Dow Inc. joined in 2019 under a symbol Dow Chemical had used.
    assert "DOW" not in on_2016 and "DOW" in members_on(intervals, D("2019-06-03"))


# --- the CLI and the dashboard ------------------------------------------


SPEC = f"constituents:{UNIVERSES / 'sp500_constituents.csv'},etfs"


def test_a_constituents_universe_contributes_every_member_since_the_start():
    tradeable, _ = resolve_universe(SPEC, start=D("2016-01-01"))

    assert {"META", "VNO", "SPUS"} <= set(tradeable)
    assert resolve_membership(SPEC, D("2016-01-01")) is not None


def test_a_constituents_run_is_labelled_as_rolling_membership():
    label = universe_label(SPEC)

    assert label == "sp500_constituents,etfs"
    assert is_rolling_membership(label)
    assert not is_point_in_time(label)


def test_the_overlay_is_refused_without_announcement_dates(capsys):
    assert main(["--universe", SPEC, "--index-overlay", "--offline"]) == 2
    assert "effective dates only" in capsys.readouterr().out


def _stored_run(db, universe, total_return, variant="faithful"):
    from app.models import BacktestRun

    run = BacktestRun(
        label=universe, universe=universe, strategy="donchian", variant=variant,
        start_date=date(2016, 1, 4), end_date=date(2026, 8, 11),
        summary_json=f'{{"total_return": {total_return}}}', config_json="{}",
    )
    db.add(run)
    db.commit()
    return run


def test_a_membership_run_becomes_the_headline(client, db):
    """Decided 2026-09-14: the one universe that could actually
    have been traded is what the tiles and the badge describe. Today's list
    stays on the chart as the comparison, never the other way round — even
    though it's the newer save here."""
    _stored_run(db, "sp500_constituents,etfs", 1.64)
    _stored_run(db, "sp500_constituents,etfs", 2.09, "atr_stop")
    _stored_run(db, "sp500_2016,etfs", 2.02)
    _stored_run(db, "sp500,etfs", 8.70)
    _stored_run(db, "sp500,etfs", 6.00, "atr_stop")

    body = client.get("/api/backtest").json()

    assert body["faithful"]["universe"] == "sp500_constituents,etfs"
    assert body["atr_stop"]["universe"] == "sp500_constituents,etfs"
    assert body["current_list"]["universe"] == "sp500,etfs"
    assert body["point_in_time"]["universe"] == "sp500_2016,etfs"
    caveats = " ".join(body["caveats"])
    assert "only while it was actually in the S&P 500" in caveats
    assert "lies between" not in caveats


def test_without_a_membership_run_todays_list_stays_the_headline(client, db):
    _stored_run(db, "sp500,etfs", 8.70)

    body = client.get("/api/backtest").json()

    assert body["faithful"]["universe"] == "sp500,etfs"
    assert body["current_list"] is None
