"""
S&P 500 membership as intervals, from a point-in-time constituents workbook.

    python -m app.backtest.sp500_constituents          # rebuilds the committed file

Reads `data/universes/sources/SP500_Historical_Constituents_2016_2025.xlsx`
and writes `data/universes/sp500_constituents.csv`: one row per continuous
stretch of index membership, keyed by the ticker Yahoo prices the company
under. `app/backtest/membership.py` turns that into the same schedule the
engine already consumes (`--universe constituents:<csv>,etfs`). This is the
membership source for S&P backtests from 2026-09-14 on.

## What the workbook is, and isn't

Ten yearly sheets list every company that was a member at any point in
that year, with the dates it was a member *within* the year. Its backbone
is the fja05680/sp500 daily snapshots (Wikipedia-derived), reconciled
against S&P's documented change log — the same ancestry as
`sp500_events.csv`, so the two agree closely; the difference is that this
one switches on EFFECTIVE dates and was cross-checked by someone else. It
has no announcement dates, so the index-change overlay can't run on it.

Two conventions in its dates matter:

* `Effective To` on a removal is the first day the company is OUT (the
  S&P effective date), so an interval's end is exclusive. On a full-year
  row it is just 31 December, the sheet's own boundary, not an exit.
* Ticker changes appear as a "Ticker Change Out" row for the old symbol
  and "Ticker Change In" for the new one on the same date. They are one
  company; re-keyed to one Yahoo ticker they merge back into one interval.

## Why tickers need re-keying at all

The workbook names each company by the ticker it used AT THE TIME. Yahoo
keys a company's whole history by its CURRENT ticker (FB's bars live under
META). So every interval is resolved forward through the renames that
happened after it — date-aware, because symbols get reused: DOW is Dow
Chemical until 2017 (whose line is now DD) and the unrelated Dow Inc. from
2019. Rules come from three places: the workbook's own "Ticker Changes"
sheet, the `continuation` rows in `sp500_events.csv`, and `EXTRA_RENAMES`
below for renames after the workbook was built.

## 2026

The workbook ends on 2025-12-31. Membership from 2026 on continues from
its year-end list with the 2026 changes in `sp500_events.csv`, switched on
their effective dates. `test_the_constituents_file_reaches_the_anchor_list`
fails if that stitch stops landing on today's actual list.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .membership import Interval, load_events, members_on
from .universe import UNIVERSE_DIR, load_membership

SOURCE_FILE = UNIVERSE_DIR / "sources" / "SP500_Historical_Constituents_2016_2025.xlsx"
OUTPUT_FILE = UNIVERSE_DIR / "sp500_constituents.csv"
EVENTS_FILE = UNIVERSE_DIR / "sp500_events.csv"
FIRST_YEAR, LAST_YEAR = 2016, 2025
WORKBOOK_END = date(LAST_YEAR, 12, 31)
FIELDS = ("ticker", "source_ticker", "company", "member_from", "member_until", "source")

# (effective, old, new, note). Renames the workbook doesn't record, found by
# comparing its 2025 year-end list, carried forward through 2026, with
# today's list: each pair is one company whose Yahoo history now sits under
# the new symbol (checked: identical closes under both where Yahoo still
# serves the old one). The dates only order the rules — they must fall
# after the company's last interval under the old ticker.
EXTRA_RENAMES: List[Tuple[str, str, str, str]] = [
    ("2026-01-01", "BK", "BNY", "Bank of New York Mellon now trades as BNY"),
    ("2026-01-01", "MMC", "MRSH", "Marsh McLennan now trades as MRSH"),
    ("2026-01-01", "EQR", "VMRK", "Equity Residential's line is keyed VMRK on Yahoo"),
    # Inside the window, but the workbook files the old company under its
    # successor's symbol or not at all.
    ("2020-03-02", "IR", "TT", "Ingersoll-Rand plc renamed Trane Technologies"),
    ("2021-02-17", "WYND", "TNL", "Wyndham Destinations renamed Travel + Leisure"),
]

# (ticker, date, note): one symbol, two companies, and the workbook runs
# them together as a single unbroken membership. Split at the handover so
# each side resolves to its own Yahoo history.
SPLITS: List[Tuple[str, str, str]] = [
    ("IR", "2020-03-02", "Ingersoll-Rand plc became Trane (TT); Gardner Denver took IR the same day"),
]


@dataclass(frozen=True)
class Rename:
    effective: date
    old: str
    new: str


def normalise(ticker: str) -> str:
    return (ticker or "").strip().upper().replace(".", "-")


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def read_workbook(path: Path = SOURCE_FILE) -> Tuple[List[dict], List[Rename]]:
    """Every yearly row, plus the workbook's own ticker-change table."""
    import openpyxl  # build-time only; see requirements-dev.txt

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows: List[dict] = []
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        sheet = book[f"{year} Constituents"]
        values = list(sheet.iter_rows(values_only=True))
        header = [str(c).strip() if c else "" for c in values[3]]
        for raw in values[4:]:
            if not raw or not raw[0]:
                continue
            row = dict(zip(header, raw))
            rows.append({
                "year": year,
                "ticker": normalise(row["Ticker"]),
                "company": str(row["Company Name"] or "").strip(),
                "from": _as_date(row["Effective From"]),
                "to": _as_date(row["Effective To"]),
                "status": str(row["Status"] or "").strip(),
            })

    renames: List[Rename] = []
    sheet = book["Ticker Changes"]
    values = list(sheet.iter_rows(values_only=True))
    for raw in values[4:]:
        if not raw or not raw[0]:
            continue
        renames.append(Rename(_as_date(raw[0]), normalise(raw[1]), normalise(raw[2])))

    # (year, ticker, "Added"/"Removed") -> S&P's effective date.
    sheet = book["Annual Changes"]
    values = list(sheet.iter_rows(values_only=True))
    header = [str(c).strip() if c else "" for c in values[3]]
    log: Dict[Tuple[int, str, str], date] = {}
    for raw in values[4:]:
        if not raw or not raw[0]:
            continue
        row = dict(zip(header, raw))
        effect = str(row["Membership Effect"] or "")
        if effect in ("Added", "Removed"):
            log[(int(row["Year"]), normalise(row["Ticker"]), effect)] = _as_date(row["Effective Date"])
    book.close()
    return apply_change_log(rows, log), renames


def apply_change_log(rows: List[dict], log: Dict[Tuple[int, str, str], date]) -> List[dict]:
    """Where the yearly sheets and the workbook's own change log both date
    an addition or removal, the change log's date wins.

    The workbook says the change log carries S&P's effective date and the
    yearly sheets the membership file's, 1-6 days apart. But eight removals
    in the yearly sheets run to 31 December when the company left early in
    the year — BorgWarner, Celanese, FMC and Teleflex left on 2025-03-24
    and are shown as members for the rest of 2025; Vornado left on
    2023-01-05 and is shown for all of 2023. Taken at face value those
    leave a company buyable for months after S&P dropped it."""
    fixed = []
    for r in rows:
        r = dict(r)
        if "Removed" in r["status"]:
            r["to"] = log.get((r["year"], r["ticker"], "Removed"), r["to"])
        if "Added" in r["status"]:
            r["from"] = log.get((r["year"], r["ticker"], "Added"), r["from"])
        fixed.append(r)
    return fixed


def raw_intervals(rows: Iterable[dict]) -> List[Tuple[str, str, date, Optional[date]]]:
    """(source ticker, company, from, exclusive until) per continuous stretch
    under ONE source ticker. A year's row ending on 31 December runs on
    into the next year's row; a removal's `Effective To` is exclusive."""
    by_ticker: Dict[str, List[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    out = []
    for ticker, items in by_ticker.items():
        items.sort(key=lambda r: r["from"])
        start = company = None
        prev_end: Optional[date] = None
        for r in items:
            left = "Removed" in r["status"] or "Change Out" in r["status"]
            if start is not None and prev_end is not None and r["from"] > prev_end:
                # A gap: the earlier stretch ended at the year boundary.
                # Only happens when a company left on 1 January exactly.
                out.append((ticker, company, start, prev_end))
                start = None
            if start is None:
                start, company = r["from"], r["company"]
            if left:
                out.append((ticker, company, start, r["to"]))
                start, prev_end = None, None
                continue
            if r["to"] == date(r["year"], 12, 31):
                # Runs into next year; its row there starts on 1 January.
                prev_end = date(r["year"] + 1, 1, 1)
            else:
                prev_end = r["to"]
        if start is not None:
            last_year = max(r["year"] for r in items)
            until = None if last_year == LAST_YEAR and prev_end == date(LAST_YEAR + 1, 1, 1) else prev_end
            out.append((ticker, company, start, until))
    return out


def rename_rules(workbook_renames: Iterable[Rename], events_path: Path = EVENTS_FILE) -> List[Rename]:
    rules = list(workbook_renames)
    for e in load_events(events_path).events:
        if e.kind == "continuation" and e.removed and e.added and e.removed != e.added:
            rules.append(Rename(e.effective, e.removed, e.added))
    rules += [Rename(date.fromisoformat(d), old, new) for d, old, new, _ in EXTRA_RENAMES]
    # The workbook and the events file both carry some of the same renames,
    # sometimes a day or two apart; keep the earlier of each pair.
    unique: List[Rename] = []
    for r in sorted(rules, key=lambda r: r.effective):
        if not any(u.old == r.old and u.new == r.new and (r.effective - u.effective).days <= 7
                   for u in unique):
            unique.append(r)
    return unique


def resolve(ticker: str, since: date, rules: List[Rename]) -> str:
    """The Yahoo key for `ticker` as it was used from `since`: follow the
    first rename of that symbol on or after `since`, then chain onward from
    the rename's own date. A rename BEFORE `since` belongs to an earlier
    holder of the symbol and doesn't apply — the DOW case."""
    # Strictly after: a stretch that starts ON a rename's date is already
    # the new holder of the symbol (FISV from 2025-11-11, IR from 2020-03-02).
    seen = set()
    while (ticker, since) not in seen:
        seen.add((ticker, since))
        nxt = next((r for r in rules if r.old == ticker and r.effective > since and r.new != ticker), None)
        if nxt is None:
            return ticker
        ticker, since = nxt.new, nxt.effective
    return ticker


SEAM_DAYS = 7


def merge(intervals: List[Interval]) -> List[Interval]:
    """Joins stretches of one Yahoo key that touch or overlap — a ticker
    change re-keyed onto its successor is one membership, not two. A gap
    of under a week between them is a seam in the source (Trane's TT row
    starts the day after IR's ends), not a company leaving and rejoining."""
    by_key: Dict[str, List[Interval]] = {}
    for iv in intervals:
        by_key.setdefault(iv.ticker, []).append(iv)
    out = []
    for key, items in by_key.items():
        items.sort(key=lambda i: i.member_from)
        cur = items[0]
        for nxt in items[1:]:
            if cur.member_until is None or (nxt.member_from - cur.member_until).days < SEAM_DAYS:
                end = None if cur.member_until is None or nxt.member_until is None else max(cur.member_until, nxt.member_until)
                cur = Interval(key, cur.source_ticker, cur.company, cur.member_from, end, cur.source)
            else:
                out.append(cur)
                cur = nxt
        out.append(cur)
    return sorted(out, key=lambda i: (i.ticker, i.member_from))


def extend_2026(intervals: List[Interval], events_path: Path = EVENTS_FILE,
                through: Optional[date] = None) -> List[Interval]:
    """Carries the workbook's year-end list forward through the 2026 changes
    in the events file, on their effective dates. Changes still pending at
    `through` (effective after it) are left out: they haven't happened."""
    events = load_events(events_path)
    through = through or events.anchor_as_of
    rules = [Rename(e.effective, e.removed, e.added) for e in events.events
             if e.kind == "continuation" and e.removed and e.added and e.removed != e.added]
    out = list(intervals)
    for e in sorted(events.events, key=lambda e: e.effective):
        if e.effective <= WORKBOOK_END or e.effective > through or e.kind == "continuation":
            continue
        if e.removed:
            key = resolve(e.removed, e.effective, rules)
            for n, iv in enumerate(out):
                if iv.ticker in (key, e.removed) and iv.member_until is None:
                    out[n] = Interval(iv.ticker, iv.source_ticker, iv.company, iv.member_from,
                                      e.effective, iv.source + "+sp500_events")
        if e.added:
            key = resolve(e.added, e.effective, rules)
            out.append(Interval(key, e.added, "", e.effective, None, "sp500_events"))
    return merge(out)


def split(stretches: List[Tuple[str, str, date, Optional[date]]],
          splits: Iterable[Tuple[str, str, str]] = SPLITS) -> List[Tuple[str, str, date, Optional[date]]]:
    cuts = [(t, date.fromisoformat(d)) for t, d, _ in splits]
    out = []
    for t, company, start, until in stretches:
        for ticker, at in cuts:
            if t == ticker and start < at and (until is None or at < until):
                out.append((t, company, start, at))
                start = at
        out.append((t, company, start, until))
    return out


def build(source: Path = SOURCE_FILE, events_path: Path = EVENTS_FILE) -> List[Interval]:
    rows, workbook_renames = read_workbook(source)
    rules = rename_rules(workbook_renames, events_path)
    intervals = [
        Interval(resolve(t, start, rules), t, company, start, until, "workbook")
        for t, company, start, until in split(raw_intervals(rows))
    ]
    return extend_2026(merge(intervals), events_path)


def write(intervals: List[Interval], path: Path = OUTPUT_FILE, source: Path = SOURCE_FILE) -> Path:
    buf = io.StringIO()
    buf.write(f"# S&P 500 membership intervals, keyed by Yahoo ticker. Built by "
              f"app/backtest/sp500_constituents.py on {date.today().isoformat()}\n")
    buf.write(f"# source: {source.name} (2016-2025), then sp500_events.csv effective dates\n")
    buf.write("# member_until is exclusive (the first day out); blank = still a member\n")
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    for iv in intervals:
        w.writerow({
            "ticker": iv.ticker, "source_ticker": iv.source_ticker, "company": iv.company,
            "member_from": iv.member_from.isoformat(),
            "member_until": iv.member_until.isoformat() if iv.member_until else "",
            "source": iv.source,
        })
    path.write_text(buf.getvalue())
    return path


def reconcile(intervals: List[Interval], events_path: Path = EVENTS_FILE) -> List[str]:
    """Differences from the announcement-dated schedule, sampled monthly.
    Printed at build time so each one is looked at, not discovered later."""
    from .membership import build_schedule

    events = load_events(events_path)
    schedule = build_schedule(events, date(FIRST_YEAR, 1, 1))
    lines = []
    day = date(FIRST_YEAR, 1, 4)
    while day <= events.anchor_as_of:
        theirs = set(schedule.initial)
        for s in schedule.switches:
            if s.announced <= day:
                if s.added:
                    theirs.add(s.added)
                if s.removed:
                    theirs.discard(s.removed)
        ours = members_on(intervals, day)
        if ours != theirs:
            lines.append(f"{day}: workbook only {sorted(ours - theirs)}; events only {sorted(theirs - ours)}")
        day = date(day.year + (day.month == 12), day.month % 12 + 1, 4)
    anchor = frozenset(load_membership(events_path.parent / "sp500_anchor.txt"))
    final = members_on(intervals, events.anchor_as_of)
    lines.append(f"at {events.anchor_as_of} vs anchor list: file only {sorted(final - anchor)}; "
                 f"anchor only {sorted(anchor - final)}")
    return lines


if __name__ == "__main__":  # pragma: no cover - one-off build
    intervals = build()
    for line in reconcile(intervals):
        print(line)
    print(f"wrote {write(intervals)} ({len(intervals)} intervals, "
          f"{len({i.ticker for i in intervals})} companies)")
