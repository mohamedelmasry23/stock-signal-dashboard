"""Checks on the fetched NQ/ES CSVs before anything trades them.

Every check here exists because the failure it catches is silent. A
duplicate timestamp, a roll priced off the wrong contract, a series that
quietly stops three months early — none of those raise. They produce a
backtest that runs to completion and reports a number.

Run:

    python3 -m research.sweep.validate_futures_data

Exit status is non-zero if any HARD check fails. Flags (fallback roll
methods, outsized gaps) are reported but do not fail the run: they are
judgement calls for a human, not defects.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .fetch_futures_data import DATA_DIR, NY, OUTPUT_HEADER, ROOTS

#: A roll gap this many times the median |gap| is called out for a look.
#: Not a failure: quarterly carry genuinely varies, and the December roll
#: is routinely the year's largest.
OUTLIER_MULTIPLE = 3.0
#: ...and one this large relative to price is suspicious whatever the
#: median says, because it is the scale of a bad contract match rather
#: than of a carry spread.
OUTLIER_PRICE_FRACTION = 0.02


class Report:
    def __init__(self) -> None:
        self.failures: List[str] = []
        self.flags: List[str] = []

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures.append(label)
        return ok

    def flag(self, label: str) -> None:
        self.flags.append(label)


def read_bars(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    # Parsed with utc=True then converted, rather than parsed as local:
    # the offsets in the file are the authority, and letting pandas infer
    # a single timezone would collapse the two 01:xx hours in November.
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
    return frame


def check_header(path: Path, report: Report) -> None:
    header = path.open().readline().strip()
    report.check(header == OUTPUT_HEADER, f"{path.name} header exact", repr(header))


def check_bars(root: str, frame: pd.DataFrame, report: Report) -> Dict[str, object]:
    local = frame["timestamp"].dt.tz_convert(NY)
    duplicates = int(frame["timestamp"].duplicated().sum())
    report.check(duplicates == 0, f"{root}: no duplicate timestamps", f"{duplicates} found")
    report.check(
        frame["timestamp"].is_monotonic_increasing, f"{root}: timestamps sorted"
    )

    ohlc_sane = bool(
        (frame["high"] >= frame[["open", "close"]].max(axis=1)).all()
        and (frame["low"] <= frame[["open", "close"]].min(axis=1)).all()
        and (frame["high"] >= frame["low"]).all()
    )
    report.check(ohlc_sane, f"{root}: high/low bracket open/close on every bar")

    finite = bool(np.isfinite(frame[["open", "high", "low", "close"]].to_numpy()).all())
    report.check(finite, f"{root}: no NaN or inf prices")
    report.check(bool((frame["volume"] >= 0).all()), f"{root}: volume non-negative")
    # Additive adjustment can in principle drive an old series negative.
    # Over this window it must not, and if it ever does the series is
    # unusable rather than merely odd.
    report.check(bool((frame["low"] > 0).all()), f"{root}: no non-positive prices after adjustment")

    # The DST check the timestamp format exists for: New York repeats
    # 01:00-02:00 every November, and a bare local timestamp would print
    # the same label twice.
    naive = local.dt.strftime("%Y-%m-%dT%H:%M:%S")
    collisions = int(naive.duplicated().sum())
    if collisions:
        report.check(
            duplicates == 0,
            f"{root}: {collisions} local-time collisions survive only because the offset is kept",
            "this is the DST hour, and is why the offset is in the file",
        )

    return {
        "rows": len(frame),
        "first": local.iloc[0].isoformat(),
        "last": local.iloc[-1].isoformat(),
        "first_utc": frame["timestamp"].iloc[0],
        "last_utc": frame["timestamp"].iloc[-1],
        "sessions": int(local.dt.date.nunique()),
    }


def check_rolls(root: str, path: Path, report: Report) -> Optional[pd.DataFrame]:
    if not path.exists():
        report.check(False, f"{root}: roll log exists", str(path))
        return None
    rolls = pd.read_csv(path)
    report.check(len(rolls) > 0, f"{root}: roll log is non-empty", f"{len(rolls)} rolls")
    if not len(rolls):
        return rolls

    fallbacks = rolls[rolls["method"] != "v1_close_minus_v0_close"]
    if len(fallbacks):
        report.flag(
            f"{root}: {len(fallbacks)} of {len(rolls)} rolls used the FALLBACK "
            f"(new open - old close), which includes a minute of real price move"
        )

    gaps = rolls["gap"].abs()
    median = float(gaps.median()) if len(gaps) else 0.0
    price = float(rolls["old_close"].abs().median() or 1.0)
    outliers = rolls[
        (gaps > OUTLIER_MULTIPLE * median) | (gaps > OUTLIER_PRICE_FRACTION * price)
    ]
    if len(outliers):
        report.flag(
            f"{root}: {len(outliers)} roll gap(s) beyond {OUTLIER_MULTIPLE}x the median "
            f"({median:.2f}) or {OUTLIER_PRICE_FRACTION:.0%} of price"
        )
    return rolls


def print_rolls(root: str, rolls: pd.DataFrame) -> None:
    print(f"\n--- roll log: {root} ({len(rolls)} rolls) ---")
    columns = ["roll_timestamp", "old_symbol", "new_symbol", "old_close",
               "new_close_v1", "gap", "method", "flagged"]
    view = rolls[[c for c in columns if c in rolls.columns]].copy()
    view["roll_timestamp"] = view["roll_timestamp"].str.slice(0, 16)
    print(view.to_string(index=False))


def validate(out_dir: Path = DATA_DIR, roots: Sequence[str] = ROOTS) -> int:
    report = Report()
    stats: Dict[str, Dict[str, object]] = {}
    logs: Dict[str, pd.DataFrame] = {}

    for root in roots:
        path = out_dir / f"{root}.csv"
        print(f"\n=== {root} ===")
        if not path.exists():
            report.check(False, f"{root}.csv exists", str(path))
            continue
        check_header(path, report)
        frame = read_bars(path)
        stats[root] = check_bars(root, frame, report)
        rolls = check_rolls(root, out_dir / f"roll_log_{root}.csv", report)
        if rolls is not None:
            logs[root] = rolls

    if len(stats) == 2:
        first, second = list(stats)
        print(f"\n=== {first} vs {second} coverage ===")
        same_start = stats[first]["first_utc"] == stats[second]["first_utc"]
        same_end = stats[first]["last_utc"] == stats[second]["last_utc"]
        report.check(same_start, "both series start on the same bar",
                     f"{stats[first]['first']} vs {stats[second]['first']}")
        report.check(same_end, "both series end on the same bar",
                     f"{stats[first]['last']} vs {stats[second]['last']}")
        drift = abs(int(stats[first]["rows"]) - int(stats[second]["rows"]))
        # Not required to be identical: a minute in which one contract
        # simply did not trade produces no bar, and NQ and ES do not go
        # quiet at the same moments. A large divergence is another matter.
        report.check(
            drift < 0.01 * max(int(stats[first]["rows"]), 1),
            "row counts within 1% of each other",
            f"{stats[first]['rows']:,} vs {stats[second]['rows']:,} (diff {drift:,})",
        )

    print("\n=== summary ===")
    for root, s in stats.items():
        print(f"  {root}: {s['rows']:,} bars over {s['sessions']:,} sessions")
        print(f"      first {s['first']}")
        print(f"      last  {s['last']}")

    for root, rolls in logs.items():
        print_rolls(root, rolls)

    if report.flags:
        print("\n=== flags (for a human, not failures) ===")
        for flag in report.flags:
            print(f"  ! {flag}")

    if report.failures:
        print(f"\n{len(report.failures)} CHECK(S) FAILED:")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1

    print("\nAll hard checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(validate())
