"""Continuous NQ and ES 1-minute bars from Databento, back-adjusted.

Produces `data/NQ.csv` and `data/ES.csv` — one row per minute, prices
additively back-adjusted across contract rolls — plus a roll log per root
recording how every single adjustment was derived.

## Why back-adjustment at all

A futures "continuous" series is a fiction stitched from a sequence of
real contracts. On the day the front month rolls, the quoted price jumps
by the spread between the old and new contract — for NQ that is routinely
tens of points. Left alone, that jump is indistinguishable from a price
move, and a strategy reading it as one would see a gap through every
level it was watching. Every roll would manufacture a sweep.

## Additive (Panama), not ratio

Prices are shifted, never scaled: at each roll the gap is ADDED to all
prior bars, cumulatively backwards. The most recent segment is left
untouched, so the right-hand edge of the series is the real current price.

Additive is the right choice here specifically because this strategy
measures distances in ticks — sweep penetration, gap width, stop buffer.
Ratio adjustment preserves percentage returns but rescales every tick
distance in history, so a "two-tick penetration" in 2022 would not mean
two ticks. Additive preserves the tick, which is the unit the rules are
written in. The cost is that far enough back, an additive series can drift
away from the price that actually traded (and in a long enough history, go
negative). Over this window it does not, and the roll log makes the total
drift auditable.

## How the gap is measured

At each roll — a bar where `instrument_id` changes in the `.v.0` series —
the gap is taken from the LAST bar of the old contract, using both series
at that one timestamp:

    gap = close of .v.1 at T  -  close of .v.0 at T

At T the front series still holds the old contract while `.v.1` already
holds the contract it is about to become, so this is the two contracts'
closes at the same instant: a true spread, with no time passing between
the two observations. That is the only reason to prefer it over the
obvious alternative.

The fallback, used only when `.v.1` has no bar at T or is not holding the
contract the front series rolls into, is

    gap = open of .v.0 at the first new-contract bar - close of .v.0 at T

which spans a minute of real time and so contains one minute of genuine
price move as well as the spread. It is contaminated, which is why every
roll records which method produced it and the validation step flags the
fallbacks rather than burying them.

## Timestamps

`ts_event` on an ohlcv bar is the INCLUSIVE START of its aggregation
window, so the index is bar-open time. Output keeps the America/New_York
UTC offset (`2023-01-03T09:31:00-05:00`) rather than a bare local time.
That is load-bearing, not cosmetic: on the November DST change New York
repeats 01:00-02:00, and a naive local timestamp would print two
different bars with identical labels — which pandas will happily index
and every downstream dedupe would silently collapse.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "backend" / "data"
CACHE_DIR = DATA_DIR / "databento_cache"

DATASET = "GLBX.MDP3"
SCHEMA = "ohlcv-1m"
STYPE_IN = "continuous"
ROOTS = ("NQ", "ES")
#: Rank 0 is the front month, rank 1 the next one out. `.v` selects the
#: volume-based roll, which switches when the back month's volume
#: overtakes the front's — closer to where liquidity actually is than a
#: fixed calendar rule.
RANKS = (0, 1)

OUTPUT_HEADER = "timestamp,open,high,low,close,volume"
NY = "America/New_York"


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------
def load_api_key() -> str:
    """Read the key from the environment, or from the repo-root `.env`.

    Never returned to a caller that logs it, never passed on a command
    line, and never included in any error message — an exception carrying
    a key ends up in a traceback, and tracebacks end up in issues.
    """
    key = os.environ.get("DATABENTO_API_KEY")
    if key:
        return key.strip()

    env = REPO_ROOT / ".env"
    if env.exists():
        match = re.search(r"^DATABENTO_API_KEY=(.*)$", env.read_text(), re.M)
        if match and match.group(1).strip():
            return match.group(1).strip()

    raise SystemExit(
        f"No Databento API key. Set DATABENTO_API_KEY, or put it in {env} as\n"
        f"    DATABENTO_API_KEY=db-...\n"
        f"That file is git-ignored; do not commit a key anywhere."
    )


def make_client():
    import databento as db

    return db.Historical(load_api_key())


def continuous_symbol(root: str, rank: int) -> str:
    return f"{root}.v.{rank}"


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------
def estimate_cost(client, symbols: Sequence[str], start: str, end: str) -> Dict[str, float]:
    """Per-symbol USD cost of the requested range, plus a total.

    `get_cost` is a metadata call and is itself free, so this can be run
    as often as needed before committing to a download.
    """
    out: Dict[str, float] = {}
    for symbol in symbols:
        out[symbol] = float(
            client.metadata.get_cost(
                dataset=DATASET,
                symbols=[symbol],
                schema=SCHEMA,
                stype_in=STYPE_IN,
                start=start,
                end=end,
            )
        )
    out["TOTAL"] = float(sum(out.values()))
    return out


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------
def _year_chunks(start: str, end: str) -> List[tuple]:
    """Split the window at calendar-year boundaries.

    Chunked so a failure halfway through costs one year rather than the
    whole pull, and so a rerun is free: each chunk is cached to disk and
    skipped if already present. Data this is metered by the byte should
    never be fetched twice by accident.
    """
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    chunks = []
    cursor = first
    while cursor < last:
        boundary = min(pd.Timestamp(year=cursor.year + 1, month=1, day=1), last)
        chunks.append((cursor.strftime("%Y-%m-%d"), boundary.strftime("%Y-%m-%d")))
        cursor = boundary
    return chunks


def fetch_series(client, symbol: str, start: str, end: str, verbose: bool = True) -> pd.DataFrame:
    """One continuous symbol's bars, cached per year under `databento_cache/`."""
    import databento as db

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    frames: List[pd.DataFrame] = []

    for chunk_start, chunk_end in _year_chunks(start, end):
        path = CACHE_DIR / f"{symbol}_{chunk_start}_{chunk_end}.dbn.zst"
        if not path.exists():
            if verbose:
                print(f"[fetch] {symbol} {chunk_start} -> {chunk_end}")
            client.timeseries.get_range(
                dataset=DATASET,
                schema=SCHEMA,
                stype_in=STYPE_IN,
                symbols=[symbol],
                start=chunk_start,
                end=chunk_end,
                path=str(path),
            )
        elif verbose:
            print(f"[cache] {symbol} {chunk_start} -> {chunk_end}")

        frame = db.DBNStore.from_file(path).to_df(price_type="float")
        if len(frame):
            frames.append(frame[["instrument_id", "open", "high", "low", "close", "volume"]])

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "ts_event"
    return out


# --------------------------------------------------------------------------
# rolls
# --------------------------------------------------------------------------
@dataclass
class Roll:
    roll_timestamp: str
    prev_timestamp: str
    old_instrument_id: int
    new_instrument_id: int
    old_symbol: str
    new_symbol: str
    old_close: float
    new_close_v1: Optional[float]
    new_open_v0: float
    gap: float
    method: str
    flagged: str


def detect_rolls(front: pd.DataFrame, second: pd.DataFrame, symbols: Dict[int, str]) -> List[Roll]:
    """Every contract change in the front series, with its measured gap."""
    ids = front["instrument_id"].to_numpy()
    changes = np.flatnonzero(ids[1:] != ids[:-1]) + 1   # first bar of each new contract

    rolls: List[Roll] = []
    for position in changes:
        previous = position - 1
        at = front.index[previous]                      # last bar of the old contract
        old_close = float(front["close"].iloc[previous])
        new_open = float(front["open"].iloc[position])
        old_id, new_id = int(ids[previous]), int(ids[position])

        v1_close: Optional[float] = None
        v1_id: Optional[int] = None
        if at in second.index:
            v1_close = float(second["close"].loc[at])
            v1_id = int(second["instrument_id"].loc[at])

        flags: List[str] = []
        # The .v.1 close is only the NEW CONTRACT's close if .v.1 was
        # actually holding that contract at T. When the roll skips a month
        # — which happens around quarterly expiries and on thin back
        # months — it is holding something else, and using it would price
        # the gap against the wrong contract entirely.
        if v1_close is not None and v1_id == new_id:
            gap = v1_close - old_close
            method = "v1_close_minus_v0_close"
        else:
            gap = new_open - old_close
            method = "new_open_minus_old_close"
            flags.append("no_v1_bar" if v1_close is None else f"v1_held_{v1_id}_not_{new_id}")

        rolls.append(
            Roll(
                roll_timestamp=front.index[position].tz_convert(NY).isoformat(),
                prev_timestamp=at.tz_convert(NY).isoformat(),
                old_instrument_id=old_id,
                new_instrument_id=new_id,
                old_symbol=symbols.get(old_id, str(old_id)),
                new_symbol=symbols.get(new_id, str(new_id)),
                old_close=old_close,
                new_close_v1=v1_close,
                new_open_v0=new_open,
                gap=float(gap),
                method=method,
                flagged=";".join(flags),
            )
        )
    return rolls


def panama_adjust(front: pd.DataFrame, rolls: List[Roll]) -> pd.DataFrame:
    """Add each roll's gap to every bar BEFORE it, cumulatively backwards.

    The bar at a roll is the first bar of the new contract and is already
    on the new scale, so it must not receive its own gap — hence the
    strict `>`. Off by one here is invisible in any summary statistic and
    misprices exactly the bars around each roll.
    """
    marks = np.zeros(len(front), dtype=float)
    lookup = {ts: i for i, ts in enumerate(front.index)}
    for roll in rolls:
        position = lookup[pd.Timestamp(roll.roll_timestamp).tz_convert("UTC")]
        marks[position] = roll.gap

    suffix = marks[::-1].cumsum()[::-1]     # gaps at rolls >= j
    adjustment = suffix - marks             # gaps at rolls >  j

    out = front.copy()
    for column in ("open", "high", "low", "close"):
        out[column] = out[column].to_numpy(dtype=float) + adjustment
    out["adjustment"] = adjustment
    return out


def resolve_symbols(client, front: pd.DataFrame) -> Dict[int, str]:
    """instrument_id -> raw contract symbol (NQH3, ESM4, ...), best effort.

    Each id is resolved ONLY over the dates it was actually the front
    contract. CME recycles instrument ids: the id NQM3 carried in 2023 can
    belong to an unrelated instrument in another year. The first version
    resolved every id over the whole window and took the first answer,
    which labelled NQ rolls as `CLTH2` and `USCX4-USCQ5` — wrong names on
    correct data, since names never enter roll detection or the gap.

    Only for readability. Symbology is a free metadata call, but a failure
    here must not lose a paid download, so anything going wrong degrades
    to bare ids.
    """
    out: Dict[int, str] = {}
    try:
        for instrument_id, rows in front.groupby("instrument_id"):
            first = rows.index.min().strftime("%Y-%m-%d")
            last = (rows.index.max() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            result = client.symbology.resolve(
                dataset=DATASET,
                symbols=[str(int(instrument_id))],
                stype_in="instrument_id",
                stype_out="raw_symbol",
                start_date=first,
                end_date=last,
            )
            entries = (result.get("result") or {}).get(str(int(instrument_id))) or []
            if entries:
                out[int(instrument_id)] = entries[-1].get("s", str(instrument_id))
    except Exception as exc:                      # noqa: BLE001 - see docstring
        print(f"[symbology] could not resolve contract names ({type(exc).__name__}); "
              f"the roll log will show instrument ids instead.")
    return out


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
def to_output_frame(adjusted: pd.DataFrame) -> pd.DataFrame:
    """The exact five columns, with New York offsets on the timestamps."""
    local = adjusted.index.tz_convert(NY)
    stamps = local.strftime("%Y-%m-%dT%H:%M:%S%z")
    # strftime gives -0500; ISO 8601 wants -05:00.
    stamps = pd.Index([f"{s[:-2]}:{s[-2:]}" for s in stamps])

    return pd.DataFrame(
        {
            "timestamp": stamps,
            "open": adjusted["open"].to_numpy(),
            "high": adjusted["high"].to_numpy(),
            "low": adjusted["low"].to_numpy(),
            "close": adjusted["close"].to_numpy(),
            "volume": adjusted["volume"].to_numpy(),
        }
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, float_format="%.10g")
    header = path.open().readline().strip()
    if header != OUTPUT_HEADER:
        raise RuntimeError(f"{path.name} header is {header!r}, expected {OUTPUT_HEADER!r}")


def write_roll_log(rolls: List[Roll], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([asdict(r) for r in rolls]) if rolls else pd.DataFrame(
        columns=[f.name for f in Roll.__dataclass_fields__.values()]
    )
    frame.to_csv(path, index=False)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="fetch_futures_data", description=__doc__)
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default="2026-09-01", help="exclusive")
    p.add_argument("--roots", default=",".join(ROOTS))
    p.add_argument("--out-dir", default=str(DATA_DIR))
    p.add_argument("--cost-only", action="store_true",
                   help="price the request with metadata.get_cost and stop")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    roots = [r.strip().upper() for r in args.roots.split(",") if r.strip()]
    symbols = [continuous_symbol(r, rank) for r in roots for rank in RANKS]
    out_dir = Path(args.out_dir)

    client = make_client()

    if args.cost_only:
        costs = estimate_cost(client, symbols, args.start, args.end)
        print(f"\nCost estimate — {DATASET} {SCHEMA}, {args.start} to {args.end} (exclusive)\n")
        for symbol in symbols:
            print(f"  {symbol:<10} ${costs[symbol]:>8.4f}")
        print(f"  {'-' * 20}")
        print(f"  {'TOTAL':<10} ${costs['TOTAL']:>8.4f}\n")
        return 0

    for root in roots:
        print(f"\n=== {root} ===")
        front = fetch_series(client, continuous_symbol(root, 0), args.start, args.end)
        second = fetch_series(client, continuous_symbol(root, 1), args.start, args.end)
        if front.empty:
            print(f"[{root}] no data returned; skipping")
            continue

        names = resolve_symbols(client, front)
        rolls = detect_rolls(front, second, names)
        adjusted = panama_adjust(front, rolls)

        write_csv(to_output_frame(adjusted), out_dir / f"{root}.csv")
        write_roll_log(rolls, out_dir / f"roll_log_{root}.csv")
        print(f"[{root}] {len(front):,} bars, {len(rolls)} rolls, "
              f"total adjustment {adjusted['adjustment'].iloc[0]:+.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
