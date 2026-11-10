"""
Builds a shareable copy of the real database: every signal, backtest and
watchlist row, and nothing about whoever's account it came from.

    python3 -m app.public_snapshot            # data/real.db -> data/snapshot/dashboard.db

The snapshot is what `python3 -m app.bootstrap` hands to someone who has
just cloned the repo, so they see the dashboard as it really runs (real
signals, both real backtests) instead of the fictitious demo data.

What counts as personal, and what's done about it:

  holdings            every row deleted
  account_settings    every row deleted; get_settings() recreates the
                      default row on first use, so equity resolves to the
                      labelled DEFAULT_EQUITY placeholder
  suggested_shares    set to NULL in signals and rsi_signals. The pipeline
                      stores it sized to the owner's equity, and beside the
                      stored stop_distance it gives that equity straight
                      back. The API never reads it (it re-sizes at request
                      time), so nothing on screen changes.

The copy is then VACUUMed, so deleted rows don't linger in free pages of
the file, and checked again before it replaces the previous snapshot.

Any table not named below makes the build refuse. A personal table added
later has to be classified here first, rather than leaking by default.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_SOURCE = DATA_DIR / "real.db"
DEFAULT_DEST = DATA_DIR / "snapshot" / "dashboard.db"

PERSONAL_TABLES = ("holdings", "account_settings")
PUBLIC_TABLES = frozenset({
    "universe",
    "signals",
    "rsi_signals",
    "backtest_runs",
    "backtest_equity",
    "backtest_trades",
    "watch_items",
    "watch_scan",
})
# Columns sized to the owner's equity (see the module docstring).
EQUITY_DERIVED_COLUMNS = (("signals", "suggested_shares"), ("rsi_signals", "suggested_shares"))


class SnapshotRefused(RuntimeError):
    """The source holds something this module doesn't know how to treat."""


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {name for (name,) in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _check_clean(conn: sqlite3.Connection) -> None:
    """Raises unless the copy holds nothing personal. Run after scrubbing,
    on the file that is about to be published."""
    unknown = _tables(conn) - PUBLIC_TABLES - set(PERSONAL_TABLES)
    if unknown:
        raise SnapshotRefused(
            f"unclassified table(s) {sorted(unknown)}: add each to PUBLIC_TABLES "
            "or PERSONAL_TABLES in app/public_snapshot.py first"
        )
    present = _tables(conn)
    for table in PERSONAL_TABLES:
        if table in present:
            (n,) = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            if n:
                raise SnapshotRefused(f"{table} still has {n} row(s)")
    for table, column in EQUITY_DERIVED_COLUMNS:
        if table in present and column in _columns(conn, table):
            (n,) = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IS NOT NULL'
            ).fetchone()
            if n:
                raise SnapshotRefused(f"{table}.{column} still set on {n} row(s)")


def build_snapshot(source: Path = DEFAULT_SOURCE, dest: Path = DEFAULT_DEST) -> dict[str, int]:
    """Writes a scrubbed copy of `source` to `dest` and returns row counts
    per table. `dest` is only replaced once the copy has passed the check,
    so a refused build leaves the previous snapshot untouched."""
    source, dest = Path(source), Path(dest)
    if not source.exists():
        raise FileNotFoundError(f"no database at {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)

    # The online backup API copies a consistent image even while the API
    # or the nightly pipeline has the source open. Read-only on the source.
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    out = sqlite3.connect(tmp)
    try:
        src.backup(out)
        src.close()

        present = _tables(out)
        for table in PERSONAL_TABLES:
            if table in present:
                out.execute(f'DELETE FROM "{table}"')
        for table, column in EQUITY_DERIVED_COLUMNS:
            if table in present and column in _columns(out, table):
                out.execute(f'UPDATE "{table}" SET "{column}" = NULL')
        out.commit()

        # A plain rollback-journal file, so the snapshot is one
        # self-contained file with no -wal sidecar to forget.
        out.execute("PRAGMA journal_mode = DELETE")
        out.execute("VACUUM")
        _check_clean(out)

        counts = {
            t: out.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            for t in sorted(_tables(out))
        }
    except BaseException:
        out.close()
        tmp.unlink(missing_ok=True)
        raise
    out.close()
    os.replace(tmp, dest)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args(argv)

    try:
        counts = build_snapshot(args.source, args.dest)
    except (SnapshotRefused, FileNotFoundError) as exc:
        print(f"[snapshot] REFUSED: {exc}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{args.dest}?mode=ro", uri=True)
    newest = conn.execute("SELECT MAX(as_of_date) FROM signals").fetchone()[0] if "signals" in counts else None
    runs = conn.execute(
        "SELECT strategy, label FROM backtest_runs ORDER BY id"
    ).fetchall() if "backtest_runs" in counts else []
    conn.close()

    size_kb = args.dest.stat().st_size // 1024
    print(f"[snapshot] wrote {args.dest} ({size_kb} KB); signals as of {newest}")
    for table, n in counts.items():
        print(f"  {table:<18} {n:>6}")
    for strategy, label in runs:
        print(f"  backtest: {strategy or 'donchian'} — {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
