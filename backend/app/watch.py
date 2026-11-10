"""
The intraday watchlist: which tickers are trading above their 55-day high
right now.

**This is not a signal, and it is not a strategy change.** It never writes
to `signals`. A name here is above the level a close would have to beat,
and nothing more — it can appear at 11:00, fade, close below and never be
flagged. That is the end-of-day rule working, not a miss.

The distinction is load-bearing because the numbers say so. Measured over
52,087 breakouts from 2016-2026: 77.3% crossed the level during the session
rather than gapping through, and for those, entering at the signal day's
close rather than the next open was worth +0.04% — less than one side of
transaction costs. There is no measured edge in acting early, so this
module exists to *show* rather than to decide. The measured system is the
one the pipeline writes and the backtest describes.

What it is genuinely useful for is the other 22.7%: names that gap straight
through the level at the open, which is invisible on an end-of-day
dashboard until that evening. MRNA closed at 62.96 and opened at 116.25,
36% above its 55-day high of 85.60. That is the case this answers.

Note the scan covers the whole universe, not names sitting near their
level. MRNA closed 26% *below* its level the day before it gapped, so any
proximity shortlist would have missed exactly the trade that prompted this.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as time_of_day
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy.orm import Session

from . import models
from .config import DEMO_MODE
from .database import Base, SessionLocal, engine
from .pipeline import (
    CHUNK_SIZE,
    NEW_YORK,
    PAUSE_BETWEEN_CHUNKS_SEC,
    SESSION_COMPLETE_ET,
    configure_yfinance_cache,
    drop_in_progress_bar,
    fetch_ohlc_batch,
    market_now_et,
)
from .signals import compute_signal_frame
from .timeutil import utcnow

# Six months rather than the pipeline's two years: 55 highs plus a 20-bar
# ATR needs about 75 bars, and six months is ~126. Across 547 tickers three
# times a day that is roughly a quarter of the payload for no loss.
WATCH_PERIOD = "6mo"

# Before the one retry pass over tickers whose download came back empty.
# Same cause and same cure as pipeline.RETRY_PAUSE_SEC: a scan launched
# from a battery DarkWake loses every in-flight download when the Mac drops
# back to sleep two seconds later. `caffeinate -i -s` does not prevent that
# (pmset log, 2026-11-10 12:31: the assertion was created and the Mac slept
# anyway; 0 of 547 fetched). The process resumes when the Mac next wakes,
# and the retry then finds the network back.
RETRY_PAUSE_SEC = 10.0

N_ENTRY = 55

SESSION_OPEN_ET = time_of_day(9, 30)
# The upper bound is pipeline.SESSION_COMPLETE_ET (16:05), reused rather
# than redeclared — two constants for one boundary is how they drift.


@dataclass(frozen=True)
class WatchObservation:
    ticker: str
    price: float
    level: float
    atr: Optional[float]
    atrs_above: Optional[float]
    prev_close: Optional[float]
    gap_open: bool


def in_session(now_et: datetime) -> bool:
    """Is the US equity session running?

    Weekends are excluded; holidays are not, because this module has no
    market calendar. A holiday scan is harmless — Yahoo emits no bar dated
    today, so every ticker is skipped and the scan writes an empty list.
    """
    if now_et.weekday() >= 5:
        return False
    return SESSION_OPEN_ET <= now_et.time() < SESSION_COMPLETE_ET


def watch_level(completed: pd.DataFrame, n_entry: int = N_ENTRY) -> Optional[float]:
    """The level the CURRENT session has to close above.

    Unshifted, and that is the entire point. `compute_signal_frame` shifts
    because a level containing today's own high could never be broken — but
    every bar in `completed` is already a *prior* bar relative to the
    session in progress, including the most recent one.

    Reading `signals.donchian_high` instead would use `max(High[D-55..D-1])`
    when the live session needs `max(High[D-54..D])`. Measured over 492,874
    ticker-days: the two differ 15.5% of the time, and on 2.37% the stale
    one would list a ticker that is *not* actually above the level it has to
    beat — about a third of a typical day's list.

    Equivalently, this returns exactly what tomorrow night's pipeline run
    will store as `donchian_high`, computed a day early. That equivalence is
    pinned by a test.
    """
    if completed is None or len(completed) < n_entry:
        return None
    value = completed["High"].rolling(n_entry).max().iloc[-1]
    return None if pd.isna(value) else float(value)


def observe(ticker: str, full: pd.DataFrame, now_et: datetime) -> Optional[WatchObservation]:
    """One ticker's live position relative to its 55-day high, or None.

    The same frame is read two ways, deliberately:

    * `completed` — partial bar dropped — supplies the LEVEL and the ATR.
      Both are properties of finished sessions and would be corrupted by a
      range that is only the range so far.
    * `full` — partial bar kept — supplies the PRICE and the day's open.

    This is the one consumer in the project that wants the in-progress bar,
    and it looks like a hole in the `drop_in_progress_bar` invariant. It
    isn't: it is that invariant applied correctly to two different
    questions. Don't "fix" it.

    Returns None when the guard trimmed nothing — i.e. there is no bar dated
    today. That single condition covers holidays, halted symbols, a scan
    before the first tick, and, importantly, any scan after 16:05 ET, where
    the "partial" bar is a finished one and a level computed from it would
    wrongly include today's own high.
    """
    if full is None or full.empty:
        return None

    completed = drop_in_progress_bar(full, now_et)
    if completed is None or len(completed) == len(full):
        return None

    level = watch_level(completed)
    if level is None:
        return None

    price = full["Close"].iloc[-1]
    day_open = full["Open"].iloc[-1]
    if pd.isna(price):
        return None
    price = float(price)

    try:
        atr_value = compute_signal_frame(completed)["ATR"].iloc[-1]
        atr = None if pd.isna(atr_value) else float(atr_value)
    except Exception:
        atr = None

    prev = completed["Close"].iloc[-1]
    prev_close = None if pd.isna(prev) else float(prev)

    return WatchObservation(
        ticker=ticker,
        price=price,
        level=level,
        atr=atr,
        atrs_above=None if not atr or atr <= 0 else round((price - level) / atr, 4),
        prev_close=prev_close,
        gap_open=bool(not pd.isna(day_open) and float(day_open) > level),
    )


def scan_universe(db: Session) -> List[str]:
    """The tickers to scan: the stored universe plus anything held.

    Read from the database rather than re-scraping Wikipedia. The pipeline
    already fetches and stores the S&P list nightly, so reusing it avoids
    three extra scrapes a day and means the two scans can never disagree
    about the universe.
    """
    universe = [t for (t,) in db.query(models.Universe.ticker).all()]
    held = [t for (t,) in db.query(models.Holding.ticker).all()]
    return sorted(set(universe) | set(held))


def _download_batch(tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """The only function in this module that touches the network.

    Wraps `pipeline.fetch_ohlc_batch` so the two agree about column shape,
    `auto_adjust=False` and how a dead symbol is dropped. Note that
    function does NOT chunk — it issues one download for whatever list it
    is handed — so the chunking lives in `run_watch_scan` below.

    `configure_yfinance_cache()` must run first: under launchd the default
    cache location is unreachable and yfinance reports it as
    `OperationalError('unable to open database file')`, which names nothing
    about the cause. See CLAUDE.md.
    """
    configure_yfinance_cache()
    return fetch_ohlc_batch(tickers, period=WATCH_PERIOD)


def run_watch_scan(now_et: Optional[datetime] = None) -> None:
    """Rebuild the watchlist from live prices. Never touches `signals`."""
    if DEMO_MODE:
        print(
            "[watch] Refusing to run with DEMO_MODE=true — this scan pulls real market "
            "data. Demo mode should show the 'no scan yet' state instead."
        )
        sys.exit(1)

    # Read once: a scan takes the better part of a minute, and one that
    # straddled the cutoff would trim some tickers and not others.
    now = now_et or market_now_et()

    # Checked before anything else, and deliberately before any network
    # call. launchd reruns a missed calendar job when the machine wakes, so
    # this will fire at 02:00 sooner or later — and after 16:05 ET the
    # "partial" bar is a completed one, which would poison every level.
    if not in_session(now):
        print(
            f"[watch] Outside the US session ({now:%Y-%m-%d %H:%M} ET); nothing scanned. "
            f"The existing watchlist is left alone."
        )
        return

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        tickers = scan_universe(db)
        if not tickers:
            print("[watch] Scan universe is empty — has the pipeline ever run?")
            _record_scan(db, now, 0, 0)
            db.commit()
            return

        observations: List[WatchObservation] = []
        fetched = 0

        def scan(batch: List[str], label: str) -> List[str]:
            """Observes `batch` in chunks and returns the tickers whose
            download came back empty. Only those are worth a retry:
            `observe()` also returns None for a ticker that downloaded fine
            but has no bar today (a holiday, a halt), and asking again
            wouldn't change that."""
            nonlocal fetched
            empty: List[str] = []
            for i in range(0, len(batch), CHUNK_SIZE):
                chunk = batch[i:i + CHUNK_SIZE]
                print(f"[watch] {label} {i + 1}-{i + len(chunk)} of {len(batch)}...")
                try:
                    data = _download_batch(chunk)
                except Exception as e:  # noqa: BLE001 - one bad chunk must not end the scan
                    print(f"[watch] WARNING: batch fetch failed for this chunk ({e}).")
                    empty.extend(chunk)
                    continue

                for ticker in chunk:
                    df = data.get(ticker)
                    if df is None or df.empty:
                        empty.append(ticker)
                        continue
                    obs = observe(ticker, df, now)
                    if obs is None:
                        continue
                    fetched += 1
                    if obs.price > obs.level:
                        observations.append(obs)

                if i + CHUNK_SIZE < len(batch):
                    time.sleep(PAUSE_BETWEEN_CHUNKS_SEC)  # be polite to Yahoo's rate limits
            return empty

        missing = scan(tickers, "Fetching")

        # One retry, once. Skipped if the session has closed in the
        # meantime (the Mac can stay asleep for hours): after 16:05 ET the
        # "in-progress" bar is a finished one, and observing it against a
        # clock read before the close would take its high as the price.
        if missing:
            still_open = in_session(now if now_et is not None else market_now_et())
            if still_open:
                print(f"[watch] Retrying {len(missing)} ticker(s) that came back empty...")
                time.sleep(RETRY_PAUSE_SEC)
                before = fetched
                scan(missing, "Retrying")
                # Own line, not part of "Done." — the wrapper parses that one.
                print(f"[watch] Retry recovered {fetched - before} of {len(missing)} ticker(s).")
            else:
                print(f"[watch] Session closed before a retry; {len(missing)} ticker(s) left empty.")

        # Wholesale replacement in one transaction, the way the pipeline
        # rewrites `universe`. A partial update would leave names on screen
        # that this scan found were no longer above their level.
        db.query(models.WatchItem).delete()
        for obs in observations:
            db.add(
                models.WatchItem(
                    ticker=obs.ticker,
                    price=obs.price,
                    level=obs.level,
                    atr=obs.atr,
                    atrs_above=obs.atrs_above,
                    prev_close=obs.prev_close,
                    gap_open=obs.gap_open,
                )
            )
        _record_scan(db, now, len(tickers), fetched)
        db.commit()

        gapped = sum(1 for o in observations if o.gap_open)
        print(
            f"[watch] Done. {len(observations)} above their 55-day high "
            f"({gapped} gapped through the open), {fetched} of {len(tickers)} tickers fetched."
        )
    finally:
        db.close()


def _record_scan(db: Session, now_et: datetime, universe_size: int, fetched: int) -> None:
    row = db.query(models.WatchScan).filter(models.WatchScan.id == 1).first()
    if row is None:
        row = models.WatchScan(id=1)
        db.add(row)
    row.scanned_at = utcnow()
    row.session_date = now_et.date()
    row.universe_size = universe_size
    row.fetched = fetched
    row.market_state = "open" if in_session(now_et) else "closed"


def latest_scan(db: Session) -> Optional[models.WatchScan]:
    return db.query(models.WatchScan).filter(models.WatchScan.id == 1).first()


def is_stale(scan: Optional[models.WatchScan], now_et: datetime, max_age_minutes: int = 45) -> bool:
    """Whether the stored watchlist is too old to present as live.

    Two independent conditions, and both are needed. Age alone can't tell
    yesterday's 15:45 scan from today's — and yesterday's list shown at
    today's open is the most misleading thing this panel could do.
    """
    if not is_current_session(scan, now_et):
        return True
    return (utcnow() - scan.scanned_at).total_seconds() > max_age_minutes * 60


def is_current_session(scan: Optional[models.WatchScan], now_et: datetime) -> bool:
    """Whether the stored scan was taken during today's New York session.

    Separate from `is_stale` because the panel needs the two apart: a scan
    from 15:45 today is stale by 17:00 but is still the last look at the
    session the pipeline is about to judge, so it stays on screen. A scan
    from any earlier session never does — including the weekend after a
    Friday scan, and the two weeks in which a mis-timed schedule skipped
    every scan and the panel kept showing a 17-day-old list.
    """
    if scan is None or scan.scanned_at is None:
        return False
    return scan.session_date == now_et.date()


if __name__ == "__main__":  # pragma: no cover - entry point
    run_watch_scan()
