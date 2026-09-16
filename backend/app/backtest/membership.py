"""
Point-in-time S&P 500 membership, from either of two sources.

* `sp500_constituents.csv` — intervals built from a constituents workbook,
  switched on EFFECTIVE dates. The S&P source for every backtest from
  2026-09-14 on (`--universe constituents:...`). See `Interval` below and
  app/backtest/sp500_constituents.py.
* `sp500_events.csv` — dated change events, switched on ANNOUNCEMENT dates.
  Still the only source the index-change overlay can trade.

Both become the same `MembershipSchedule`, so the engine can't tell them
apart. Pure, apart from reading one file: the engine imports this, and the
engine must stay free of network and database access.

The rest of this docstring is about the events file.

## How the schedule is built

`sp500_events.csv` lists every change since 2016 with the date S&P
announced it. The membership on any day is reconstructed by replaying
those changes BACKWARDS from a known-good anchor — today's constituent list
— rather than forwards from the 2016 snapshot. The direction matters
because of ticker changes. The changes table omits them by policy, so a
forward replay from 2016 keeps `FB` forever and never learns that Yahoo
keys the same company as `META`: Meta drops out of the backtest entirely.
Backwards from today, every surviving company carries the ticker Yahoo
actually has prices under, for its whole history.

Rows marked `continuation` (renames recorded as "X replaced Y") are not
changes of company. They re-key the older ticker to the newer one for
every earlier event, and trigger no trade.

## When a change counts

A company is a member from its addition's ANNOUNCEMENT until its removal's
announcement — that is the rule chosen deliberately, and what lets the overlay
trade the gap between announcement and effective date. On the trading
calendar an announcement lands on the last session on or before its date:
S&P publishes after the close, so a Friday-evening release is decided on
Friday's bar and filled at Monday's open, exactly like any other signal.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Optional

import numpy as np
import pandas as pd

KINDS = ("change", "continuation", "no_trade")


@dataclass(frozen=True)
class IndexEvent:
    announced: date
    effective: date
    added: str = ""
    removed: str = ""
    # change        a company joins and/or leaves: the overlay trades it
    # continuation  a rename recorded as a replacement: re-key, no trade
    # no_trade      membership changes, but not a stock anyone buys on the
    #               news (a share class distributed to existing holders)
    kind: str = "change"

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")
        if self.announced > self.effective:
            raise ValueError(f"{self}: announced after it took effect")


@dataclass(frozen=True)
class EventFile:
    events: List[IndexEvent]
    # Constituents as of `anchor_as_of`, keyed by Yahoo ticker.
    anchor: FrozenSet[str]
    anchor_as_of: date


@dataclass(frozen=True)
class Switch:
    """One announced change, with tickers already re-keyed to the names
    the price data uses."""
    announced: date
    added: str
    removed: str
    tradeable: bool


@dataclass
class MembershipSchedule:
    start: date
    initial: FrozenSet[str]
    switches: List[Switch]
    warnings: List[str] = field(default_factory=list)

    @property
    def universe(self) -> FrozenSet[str]:
        """Every ticker that is a member at any point from `start` on."""
        names = set(self.initial)
        for s in self.switches:
            names.update(t for t in (s.added, s.removed) if t)
        return frozenset(names)


@dataclass
class TickerMembership:
    """Per-bar arrays on the backtest calendar, read at the decision bar."""
    member: np.ndarray
    added: np.ndarray    # True on the bar an addition was announced
    removed: np.ndarray  # True on the bar a removal was announced


def load_events(path: Path) -> EventFile:
    """Reads sp500_events.csv and the anchor list its header names.

    An undated `change` is demoted to `no_trade` on its effective date:
    membership must still switch, but a news trade on a guessed date is
    exactly what this file exists to avoid."""
    path = Path(path)
    text = path.read_text()
    anchor_name, anchor_as_of = None, None
    for line in text.splitlines():
        if line.startswith("# anchor:"):
            parts = line[len("# anchor:"):].split()
            anchor_name, anchor_as_of = parts[0], date.fromisoformat(parts[2])
    if anchor_name is None:
        raise ValueError(f"{path} names no anchor list in its header")

    from .universe import load_membership  # local: universe.py imports the pipeline

    events = []
    rows = csv.DictReader(io.StringIO("\n".join(l for l in text.splitlines() if not l.startswith("#"))))
    for row in rows:
        effective = date.fromisoformat(row["effective"])
        kind = row["kind"]
        if row["announced"]:
            announced = date.fromisoformat(row["announced"])
        else:
            announced, kind = effective, ("no_trade" if kind == "change" else kind)
        events.append(IndexEvent(announced, effective, row["added"], row["removed"], kind))

    return EventFile(
        events=events,
        anchor=frozenset(load_membership(path.parent / anchor_name)),
        anchor_as_of=anchor_as_of,
    )


def build_schedule(source: EventFile, start: date) -> MembershipSchedule:
    """Membership at `start` plus every announced switch from then on."""
    alias: Dict[str, str] = {}

    def resolve(ticker: str) -> str:
        seen = set()
        while ticker in alias and ticker not in seen:
            seen.add(ticker)
            ticker = alias[ticker]
        return ticker

    # Newest first, so a rename re-keys only the events OLDER than itself.
    # DOW is the case that makes this necessary: before 2017 it is Dow
    # Chemical (later DowDuPont, now DD); from 2019 it is the unrelated
    # Dow Inc. spin-off. A time-blind alias map would merge them.
    newest_first = sorted(source.events, key=lambda e: (e.effective, e.announced), reverse=True)
    resolved: List[IndexEvent] = []
    for e in newest_first:
        if e.kind == "continuation":
            if e.removed and e.removed != e.added:
                alias[e.removed] = resolve(e.added)
            continue
        resolved.append(IndexEvent(
            e.announced, e.effective,
            resolve(e.added) if e.added else "",
            resolve(e.removed) if e.removed else "",
            e.kind,
        ))

    # Undo, newest first, every change announced on or after `start`.
    # Changes still pending at the anchor date aren't in the anchor list,
    # so there is nothing to undo — they're applied forward like the rest.
    members = set(source.anchor)
    warnings: List[str] = []
    for e in resolved:
        if e.effective > source.anchor_as_of or e.announced < start:
            continue
        if e.added:
            if e.added in members:
                members.discard(e.added)
            else:
                warnings.append(f"{e.effective}: {e.added} added, but absent from the later list")
        if e.removed:
            if e.removed in members:
                warnings.append(f"{e.effective}: {e.removed} removed, but already a member before")
            members.add(e.removed)

    switches = [
        Switch(e.announced, e.added, e.removed, tradeable=e.kind == "change")
        for e in sorted(resolved, key=lambda e: (e.announced, e.effective))
        if e.announced >= start
    ]
    return MembershipSchedule(start=start, initial=frozenset(members), switches=switches,
                              warnings=warnings)


def announcement_bar(calendar: pd.DatetimeIndex, announced: date) -> int:
    """Last session on or before `announced`; -1 if it predates the calendar."""
    return int(calendar.searchsorted(pd.Timestamp(announced), side="right")) - 1


def align(
    schedule: MembershipSchedule,
    calendar: pd.DatetimeIndex,
    tickers: Iterable[str],
) -> Dict[str, TickerMembership]:
    """Per-ticker membership arrays for the tickers that have prices.

    Tickers outside the schedule (the non-S&P ETFs in `sp500_events,etfs`)
    get no entry and so are never restricted."""
    wanted = set(tickers) & schedule.universe
    n = len(calendar)
    out = {
        t: TickerMembership(
            member=np.full(n, t in schedule.initial, dtype=bool),
            added=np.zeros(n, dtype=bool),
            removed=np.zeros(n, dtype=bool),
        )
        for t in wanted
    }
    # Chronological, so a later switch overrides an earlier one from its
    # bar onward — a company added, removed and re-added comes out right.
    for s in schedule.switches:
        bar = announcement_bar(calendar, s.announced)
        at = max(bar, 0)
        if s.added in out:
            out[s.added].member[at:] = True
            if s.tradeable and bar >= 0:
                out[s.added].added[bar] = True
        if s.removed in out:
            out[s.removed].member[at:] = False
            if s.tradeable and bar >= 0:
                out[s.removed].removed[bar] = True
    return out


def additions_without_prices(
    schedule: MembershipSchedule, calendar: pd.DatetimeIndex, tickers: Iterable[str]
) -> List[str]:
    """Tradeable additions announced inside the calendar for which there
    is no price data at all — they can't be bought, and the report says so."""
    have = set(tickers)
    if not len(calendar):
        return []
    first, last = calendar[0].date(), calendar[-1].date()
    return sorted(
        s.added for s in schedule.switches
        if s.tradeable and s.added and s.added not in have and first <= s.announced <= last
    )


def load_schedule(path: Path, start: date) -> MembershipSchedule:
    return build_schedule(load_events(path), start)


# --- Membership as intervals ------------------------------------------------
#
# The second source, and the one S&P backtests use from 2026-09-14 on:
# `sp500_constituents.csv`, built from a point-in-time constituents workbook
# by app/backtest/sp500_constituents.py. Already keyed by Yahoo ticker, so
# there is no replay and no aliasing here. It switches on EFFECTIVE dates
# and carries no announcement dates, so every switch is non-tradeable: the
# index overlay has nothing to act on.


@dataclass(frozen=True)
class Interval:
    ticker: str          # the Yahoo key
    source_ticker: str   # as the source spelled it at the time
    company: str
    member_from: date
    member_until: Optional[date]  # exclusive (first day out); None = still in
    source: str

    def covers(self, day: date) -> bool:
        return self.member_from <= day and (self.member_until is None or day < self.member_until)


def members_on(intervals: Iterable[Interval], day: date) -> set:
    return {i.ticker for i in intervals if i.covers(day)}


def load_intervals(path: Path) -> List[Interval]:
    lines = [l for l in Path(path).read_text().splitlines() if not l.startswith("#")]
    return [
        Interval(
            ticker=row["ticker"], source_ticker=row["source_ticker"], company=row["company"],
            member_from=date.fromisoformat(row["member_from"]),
            member_until=date.fromisoformat(row["member_until"]) if row["member_until"] else None,
            source=row["source"],
        )
        for row in csv.DictReader(io.StringIO("\n".join(lines)))
    ]


def schedule_from_intervals(intervals: Iterable[Interval], start: date) -> MembershipSchedule:
    """The same schedule `build_schedule` produces, from intervals.

    A removal's switch sits on its effective date, the first day out, so
    the last decision bar that still counts the company as a member is the
    session before. On the calendar, `align` places a switch on the last
    session on or before its date — for a weekend effective date that is
    the Friday: a day early for a removal, harmless for an entry filter."""
    intervals = list(intervals)
    initial = frozenset(i.ticker for i in intervals if i.covers(start))
    switches = []
    for i in intervals:
        if i.member_from > start:
            switches.append(Switch(i.member_from, added=i.ticker, removed="", tradeable=False))
        if i.member_until is not None and i.member_until > start:
            switches.append(Switch(i.member_until, added="", removed=i.ticker, tradeable=False))
    # Chronological; on a shared date the order only matters for one ticker,
    # and one ticker can't end and restart on the same day — the builder
    # merges those into a single interval.
    switches.sort(key=lambda s: (s.announced, s.added or s.removed))
    return MembershipSchedule(start=start, initial=initial, switches=switches)


def load_interval_schedule(path: Path, start: date) -> MembershipSchedule:
    return schedule_from_intervals(load_intervals(path), start)
