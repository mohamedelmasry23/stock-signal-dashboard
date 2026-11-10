"""
Sets up a freshly cloned copy with the real dashboard's data.

    python3 -m app.bootstrap

Copies `data/snapshot/dashboard.db` (built by `app.public_snapshot`, with
no holdings or account settings in it) to `data/real.db`. You get the
same signals, backtests and watchlist the author's dashboard shows, an
empty holdings list for your own positions, and position sizes for the
$100,000 placeholder account until you enter your own cash.

It never overwrites an existing `data/real.db`, since that file holds
whatever holdings and settings you've entered.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .public_snapshot import DATA_DIR, DEFAULT_DEST as SNAPSHOT

DEFAULT_TARGET = DATA_DIR / "real.db"


def bootstrap(snapshot: Path = SNAPSHOT, target: Path = DEFAULT_TARGET) -> None:
    snapshot, target = Path(snapshot), Path(target)
    if target.exists():
        raise FileExistsError(
            f"{target} already exists and may hold your holdings; not overwriting it. "
            "Move it aside first if you really want to start from the snapshot."
        )
    if not snapshot.exists():
        raise FileNotFoundError(f"no snapshot at {snapshot}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args(argv)

    try:
        bootstrap(args.snapshot, args.target)
    except (FileExistsError, FileNotFoundError) as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 1

    print(f"[bootstrap] copied {args.snapshot.name} to {args.target}")
    print("[bootstrap] Signals are as of the day the snapshot was built. To bring")
    print("            them up to today (a few minutes; needs internet):")
    print("                python3 -m app.pipeline")
    print("            Then start the API:")
    print("                python3 -m uvicorn app.main:app --reload --port 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
