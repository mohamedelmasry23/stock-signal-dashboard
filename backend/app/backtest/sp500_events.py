"""
S&P 500 membership changes, dated by when they were ANNOUNCED.

    python -m app.backtest.sp500_events          # rebuilds the committed files

Writes `data/universes/sp500_events.csv` and `data/universes/sp500_anchor.txt`,
which `app/backtest/membership.py` turns into a point-in-time universe. Both
are committed, like `sp500_2016.txt`, so a run is reproducible without
re-scraping sources that change.

## Why announcement dates, and why they're hard

The index-change overlay buys an addition and sells a removal when S&P
*announces* the change, because the price move lives between the
announcement and the effective date. The whole trade sits in a window of
roughly 2-15 sessions, so an approximate date is not a small error: a fixed
"effective minus N days" offset would put the fill on the wrong side of the
very move being measured. Every date here comes from a source, and the
source is recorded per row.

Free data gives effective dates easily and announcement dates grudgingly:

1. **Wikipedia's "Historical components of the S&P 500"** — the changes
   table, effective dates only. (It lived on "List of S&P 500 companies"
   until 2026-08-11.) Its citations usually point at the S&P press release.
2. **S&P's own news archive, press.spglobal.com** — searchable, dated, back
   to January 2016. This is the primary source for the announcement date.
   Where it and Wikipedia's citation both have a date they agreed on 132 of
   134 events when this was built; both disagreements were Wikipedia errors
   (a 2021 press release cited on a 2022 change; a PDF path dated the day
   before its release).

Priority, highest first: the press archive; the date in an S&P document URL
(`.../announcements/20260731-...`); a citation `date=` on an S&P-published
reference. Citation dates from anywhere else (10-Ks, earnings releases) are
never used — they are what produced dates 66 and 257 sessions AFTER the
effective date.

## Rows that are not index changes

The table records some renames and ticker changes as "X replaced Y", which
would make the overlay sell and rebuy one company. `CONTINUATIONS` marks
those; `RENAMES` adds the ticker changes the table omits by policy but that
matter here because the company joined inside the window. Both were found
by replaying the changes backwards from today's list to January 2016 and
diffing against the independent `sp500_2016.txt` snapshot: after these, every
remaining difference is a rename of a pre-2016 member (FB -> META, BBT -> TFC,
...), which is exactly how Yahoo keys the price history.
"""

from __future__ import annotations

import csv
import html
import io
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

from ..pipeline import SP500_URL, USER_AGENT
from .universe import UNIVERSE_DIR, load_membership, save_membership

WIKI_API = "https://en.wikipedia.org/w/api.php"
HISTORY_TITLE = "Historical components of the S&P 500"
PRESS_ARCHIVE = "https://press.spglobal.com/index.php"
# The S&P Dow Jones Indices section of S&P Global's press archive.
PRESS_SECTION = "2429"

EVENTS_FILE = UNIVERSE_DIR / "sp500_events.csv"
ANCHOR_FILE = UNIVERSE_DIR / "sp500_anchor.txt"
FIELDS = ("announced", "effective", "added", "removed", "kind", "date_source", "note")

# How far before the effective date an announcement may sit and still be
# accepted. The longest measured gap is ~25 sessions (spin-offs announced
# well ahead); anything beyond this is a mis-cited source, not a real lead.
MAX_LEAD_DAYS = 45

# (effective, added, removed) -> why this row is not a change of company.
CONTINUATIONS: Dict[Tuple[str, str, str], str] = {
    ("2024-03-25", "CPAY", "FLT"): "FleetCor renamed Corpay",
    ("2024-02-01", "DAY", "CDAY"): "Ceridian ticker change",
    ("2022-04-11", "WBD", "DISCA"): "Discovery absorbed WarnerMedia and was renamed",
    ("2022-01-10", "WTW", "WLTW"): "Willis Towers Watson ticker change",
    ("2020-04-01", "HWM", "ARNC"): "Arconic Inc. renamed Howmet after its spin-off",
    ("2019-06-11", "AMCR", "BMS"): "Bemis line renamed Amcor after the merger",
    ("2019-06-03", "DD", "DWDP"): "DowDuPont renamed DuPont after the Corteva spin",
    # Same ticker on both sides: 21st Century Fox out, Fox Corporation in.
    # Different companies, but Yahoo holds only the new one under FOXA/FOX,
    # so there is nothing to trade and nothing to re-key.
    ("2019-03-19", "FOXA", "FOXA"): "21st Century Fox -> Fox Corporation (same ticker)",
    ("2019-03-19", "FOX", "FOX"): "21st Century Fox -> Fox Corporation (same ticker)",
    ("2017-11-15", "IQV", "Q"): "QuintilesIMS renamed IQVIA",
    ("2017-09-01", "DWDP", "DOW"): "Dow Chemical renamed DowDuPont",
    ("2017-07-07", "BKR", "BHI"): "Baker Hughes merged with GE Oil & Gas; holders carried over",
}

# Index membership that changes hands with no news trade in it: a share
# class distributed to existing holders is not a stock anyone "buys on the
# announcement".
NO_TRADE: Dict[Tuple[str, str, str], str] = {
    ("2016-04-08", "UA", ""): "Class C shares distributed to existing holders",
}

# Ticker changes the Wikipedia table omits by policy. Only needed for
# companies that JOINED inside the window: a pre-2016 member is already
# keyed by its current ticker in the anchor list, so its rename never shows.
RENAMES: List[Tuple[str, str, str, str]] = [
    # (effective, new, old, note)
    ("2026-06-24", "ECHO", "SATS", "EchoStar ticker change"),
    ("2023-07-10", "EG", "RE", "Everest Re Group renamed Everest Group"),
]

# Announced after the Wikipedia revision this was built from. Taken from
# the press release itself; drop a row here once the table carries it.
PENDING: List[Dict[str, str]] = [
    dict(announced="2026-09-04", effective="2026-09-21", added=a, removed=r, kind="change",
         date_source="press_archive",
         note="https://press.spglobal.com/2026-09-04-Bloom-Energy,-Illumina,-and-Everpure-"
              "Set-to-Join-S-P-500-Others-to-Join-S-P-100,-S-P-MidCap-400,-and-S-P-SmallCap-600")
    for a, r in (("BE", "TAP"), ("P", "TTD"), ("ILMN", "BLDR"))
]


@dataclass
class WikiRow:
    effective: date
    added: str
    added_name: str
    removed: str
    removed_name: str
    reason: str
    ref_bodies: List[str] = field(default_factory=list)


def normalise(ticker: str) -> str:
    """Yahoo's spelling: BRK.B -> BRK-B, the same conversion the pipeline
    applies to the current list, so every file here joins against prices."""
    return (ticker or "").strip().upper().replace(".", "-")


def _strip_markup(cell: str) -> str:
    cell = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", cell, flags=re.S)
    cell = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"\{\{[^}]*\}\}", "", cell)
    return re.sub(r"\s+", " ", cell).strip()


def _parse_date(text: str) -> Optional[date]:
    text = text.strip()
    for fmt in ("%B %d, %Y", "%Y-%m-%d", "%d %B %Y", "%b %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_changes_table(wikitext: str) -> List[WikiRow]:
    """Rows of the `id="changes"` table, with each row's citations resolved
    — including `<ref name=x/>` reuses, whose body lives on another row."""
    start = wikitext.find('id="changes"')
    if start < 0:
        raise RuntimeError("No changes table in the article; has it moved again?")
    table = wikitext[start: wikitext.find("\n|}", start)]

    named = {
        m.group(1).strip(): m.group(2)
        for m in re.finditer(r'<ref\s+name\s*=\s*"?([^">/]+?)"?\s*>(.*?)</ref>', wikitext, re.S)
    }

    rows = []
    for chunk in table.split("\n|-")[2:]:  # [0] is the table opener, [1] the header
        cells = [c.strip() for c in re.split(r"\n\|\|?|\|\|", "\n" + chunk.strip("\n"))[1:]]
        if len(cells) < 7:
            continue
        effective = _parse_date(_strip_markup(cells[0]))
        if effective is None:
            continue
        refs = []
        for m in re.finditer(r'<ref(?:\s+name\s*=\s*"?([^">/]+?)"?)?\s*(?:/>|>(.*?)</ref>)', cells[6], re.S):
            refs.append(m.group(2) if m.group(2) is not None else named.get((m.group(1) or "").strip(), ""))
        rows.append(WikiRow(
            effective=effective,
            added=normalise(_strip_markup(cells[1])), added_name=_strip_markup(cells[2]),
            removed=normalise(_strip_markup(cells[3])), removed_name=_strip_markup(cells[4]),
            reason=_strip_markup(cells[5]), ref_bodies=refs,
        ))
    return rows


def dates_from_refs(ref_bodies: Iterable[str]) -> Dict[str, date]:
    """Announcement-date candidates from a row's citations, by kind.

    Only S&P-published references count: a citation `date=` on a company
    filing or an earnings release is the date of THAT document."""
    found: Dict[str, date] = {}
    for body in ref_bodies:
        for url in re.findall(r"url\s*=\s*(\S+)", body):
            m = re.search(r"announcements/(20\d{6})-", url)
            if m:
                found.setdefault("sp_document_url", datetime.strptime(m.group(1), "%Y%m%d").date())
            m = re.search(r"press\.spglobal\.com/(20\d\d-\d\d-\d\d)", url)
            if m:
                found.setdefault("press_url", date.fromisoformat(m.group(1)))
        sp_published = re.search(r"spglobal|spindices|spice-indices|S&P Dow Jones|S&P Global", body)
        m = re.search(r"\|\s*date\s*=\s*([^|}]+)", body)
        if m and sp_published:
            cited = _parse_date(m.group(1))
            if cited:
                found.setdefault("sp_citation_date", cited)
    return found


def fetch_history_wikitext() -> Tuple[int, str]:  # pragma: no cover - network
    response = requests.get(
        WIKI_API,
        params={"action": "query", "prop": "revisions", "titles": HISTORY_TITLE, "rvlimit": 1,
                "rvprop": "ids|content", "rvslots": "main", "format": "json", "formatversion": 2},
        headers={"User-Agent": USER_AGENT}, timeout=60,
    )
    response.raise_for_status()
    revision = response.json()["query"]["pages"][0]["revisions"][0]
    return revision["revid"], revision["slots"]["main"]["content"]


def search_press(keywords: str, start: date, end: date) -> List[Tuple[date, str, str]]:  # pragma: no cover - network
    """(date, url, title) for S&P DJI releases between `start` and `end`
    matching `keywords`. The archive searches release bodies, not just
    titles, and returns at most one page — hence the date bounds: without
    them a common name's 2016 release sits beyond the first 50 hits."""
    response = requests.get(
        PRESS_ARCHIVE,
        params={"s": PRESS_SECTION, "l": "50", "advanced": "1", "keywords": f'"{keywords}"',
                "start": start.isoformat(), "end": end.isoformat()},
        headers={"User-Agent": USER_AGENT}, timeout=60,
    )
    response.raise_for_status()
    out = []
    for when, url, title in re.findall(
        r'<div class="wd_date">([^<]+)</div>.*?<a href="([^"]+)"[^>]*>(.*?)</a>', response.text, re.S
    ):
        try:
            day = datetime.strptime(when.strip(), "%b %d, %Y").date()
        except ValueError:
            continue
        out.append((day, url, html.unescape(re.sub("<[^>]+>", "", title)).strip()))
    return out


# Titles of an S&P 500 membership release. Both halves are required: "S&P
# 500" alone also matches the buyback and dividend reports, which is how a
# first draft dated one addition by a quarterly buyback release. The
# quarterly "Announces Changes to U.S. Indices" releases name no company.
_MEMBERSHIP_RELEASE = re.compile(
    r"(?=.*(?:S&P 500|U\.S\. Indices))"
    r"(?=.*(?:\bJoin|\bReplace|Changes to (?:the )?(?:U\.S\. Indices|S&P 500)))",
    re.I,
)
_NAME_NOISE = re.compile(r"\(.*?\)|\b(inc|corp|corporation|co|company|plc|ltd|the|class [a-z])\b\.?", re.I)


def search_terms(name: str) -> str:
    return re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", name)).strip(" ,.&")


def press_date(row: WikiRow, search=search_press) -> Optional[Tuple[date, str]]:
    """The S&P 500 membership release for this change: among releases in
    the window before the effective date that mention the company, one
    whose title names it if there is one, else the latest. Latest, because
    a company can be announced for the MidCap 400 months before the 500."""
    name = row.added_name if row.added else row.removed_name
    if not name:
        return None
    terms = search_terms(name)
    lo = row.effective - timedelta(days=MAX_LEAD_DAYS)
    hits = [
        (day, url, title) for day, url, title in search(terms, lo, row.effective)
        if lo <= day <= row.effective and _MEMBERSHIP_RELEASE.search(title)
    ]
    first_word = terms.split()[0].lower() if terms else ""
    named = [h for h in hits if first_word and first_word in h[2].lower()]
    best = max(named or hits, default=None)
    return (best[0], f"{best[2]} <{best[1]}>") if best else None


def _plausible(announced: date, effective: date) -> bool:
    return effective - timedelta(days=MAX_LEAD_DAYS) <= announced <= effective


def build_events(rows: List[WikiRow], start: date, search=search_press, pause: float = 1.0) -> List[dict]:
    """One dict per change on or after `start`, with its announcement date
    and where that date came from. Rows no source can date are kept with an
    empty `announced` and `date_source="none"` so they are visible, not
    silently dropped."""
    events = []
    for row in rows:
        if row.effective < start:
            continue
        key = (row.effective.isoformat(), row.added, row.removed)
        base = dict(effective=row.effective.isoformat(), added=row.added, removed=row.removed)

        if key in CONTINUATIONS or key in NO_TRADE:
            kind = "continuation" if key in CONTINUATIONS else "no_trade"
            events.append(dict(base, announced=row.effective.isoformat(), kind=kind,
                               date_source=kind, note=(CONTINUATIONS | NO_TRADE)[key]))
            continue

        refs = dates_from_refs(row.ref_bodies)
        found = press_date(row, search)
        time.sleep(pause)
        if found:
            announced, source, note = found[0], "press_archive", found[1]
            cited = refs.get("sp_document_url") or refs.get("press_url")
            if cited and cited != announced:
                note += f" (Wikipedia cites {cited})"
        else:
            announced, source, note = None, "none", row.reason[:120]
            for kind in ("press_url", "sp_document_url", "sp_citation_date"):
                if kind in refs and _plausible(refs[kind], row.effective):
                    announced, source = refs[kind], kind
                    break

        events.append(dict(base, announced=announced.isoformat() if announced else "",
                           kind="change", date_source=source, note=note))
    return events


def write_events(events: List[dict], header: List[str], path: Path = EVENTS_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(events, key=lambda e: (e["effective"], e["added"], e["removed"]), reverse=True)
    buffer = io.StringIO()
    for line in header:
        buffer.write(f"# {line}\n")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows({k: e.get(k, "") for k in FIELDS} for e in ordered)
    path.write_text(buffer.getvalue())
    return path


def main() -> int:  # pragma: no cover - one-off rebuild against live sources
    from .membership import build_schedule, load_events

    today = date.today()
    revid, wikitext = fetch_history_wikitext()
    rows = parse_changes_table(wikitext)
    print(f"{len(rows)} table rows from revision {revid}")

    events = build_events(rows, start=date(2016, 1, 1))
    events += [dict(announced=e, effective=e, added=new, removed=old, kind="continuation",
                    date_source="continuation", note=note) for e, new, old, note in RENAMES]
    events += PENDING

    response = requests.get(SP500_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    anchor = sorted({normalise(t) for t in pd.read_html(io.StringIO(response.text))[0]["Symbol"]})
    save_membership(anchor, ANCHOR_FILE.stem, source=f"{SP500_URL} as of {today}")

    write_events(events, [
        "S&P 500 changes 2016 onward, dated by announcement. Built by app/backtest/sp500_events.py",
        f"table: https://en.wikipedia.org/w/index.php?oldid={revid}",
        f"anchor: {ANCHOR_FILE.name} as_of {today}",
    ])

    counts: Dict[str, int] = {}
    for e in events:
        counts[e["date_source"]] = counts.get(e["date_source"], 0) + 1
    print("date sources:", counts)
    for e in events:
        if e["date_source"] == "none":
            print("  UNDATED:", e)

    # The cross-check: replayed back to January 2016, does the schedule
    # agree with the independently archived snapshot?
    schedule = build_schedule(load_events(EVENTS_FILE), start=date(2016, 1, 4))
    snapshot = set(load_membership(UNIVERSE_DIR / "sp500_2016.txt"))
    print(f"replayed {len(schedule.initial)} vs snapshot {len(snapshot)}")
    print("  only in replay:  ", sorted(schedule.initial - snapshot))
    print("  only in snapshot:", sorted(snapshot - schedule.initial))
    for warning in schedule.warnings:
        print("  WARNING:", warning)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
