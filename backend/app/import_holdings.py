"""
Wealthsimple CSV import.

Wealthsimple has no live API for personal accounts, so this is the bridge:
export your account's full Activity history as a CSV (Wealthsimple ->
Activity -> set the date range to "All time" -> Export -> CSV), drop the
file into `data/imports/`, and either run this module directly or just
run the daily pipeline — it checks the imports folder automatically on
every run (see run_pipeline() in app/pipeline.py).

Each CSV is treated as a FULL transaction history, not an incremental
diff. Net position per ticker (total bought - total sold, across every
buy/sell row in the file) becomes the new `holdings` table, replacing
whatever was there before. Practically that means:

  - export the full history each time ("All time"), not just
    transactions since your last export — a partial export would make
    it look like you'd sold things you still hold
  - a ticker you've fully sold out of correctly disappears from holdings
  - any holding added manually through the API that ISN'T in the export
    will also be removed on the next import — Wealthsimple becomes the
    single source of truth for the real instance once you start using this
  - if multiple CSVs are sitting in the folder, they're processed oldest
    to newest by file-modified time, so the most recent export wins

Wealthsimple's exact export column names aren't publicly documented and
appear to shift between account types, so columns are matched by alias
(case/whitespace-insensitive) rather than a hardcoded schema — see
COLUMN_ALIASES below. If a real export doesn't match, the error message
names the missing field and the header row it actually saw; add the
real header spelling to COLUMN_ALIASES and re-run.

    python -m app.import_holdings
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .config import DEMO_MODE, IMPORTS_DIR, IMPORTS_PROCESSED_DIR
from .database import Base, SessionLocal, engine
from .models import Holding
from .timeutil import utcnow

# canonical field -> set of header spellings that map to it, matched after
# lowercasing and stripping non-alphanumeric characters (so "Transaction
# Date", "transaction_date", and "TransactionDate" all match the same alias)
COLUMN_ALIASES = {
    "ticker": {"ticker", "symbol", "security", "asset", "stock"},
    "action": {"type", "action", "transaction", "transactiontype", "description", "activity"},
    "quantity": {"quantity", "shares", "units", "qty"},
}

BUY_KEYWORDS = ("buy", "bought", "purchase")
SELL_KEYWORDS = ("sell", "sold", "sale")


def _normalize_header(h: str) -> str:
    return "".join(ch for ch in str(h).strip().lower() if ch.isalnum())


def _map_columns(columns) -> Dict[str, str]:
    """Canonical field name -> actual CSV column name, by alias match."""
    normalized = {_normalize_header(c): c for c in columns}
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized[alias]
                break
    return mapping


def _normalize_ticker(raw) -> str:
    # Yahoo uses '-' where some sources use '.' for share classes (BRK.B -> BRK-B),
    # matching the same convention app/pipeline.py uses for the S&P 500 list.
    return str(raw).strip().upper().replace(".", "-")


def parse_net_positions(csv_path: Path) -> Dict[str, float]:
    """Reads one Wealthsimple activity CSV and nets buy/sell rows into
    {ticker: net_shares}, dropping anything that nets to zero or below
    (fully sold, or a data issue)."""
    df = pd.read_csv(csv_path)
    mapping = _map_columns(df.columns)

    missing = {"ticker", "action", "quantity"} - mapping.keys()
    if missing:
        raise ValueError(
            f"couldn't find columns for {sorted(missing)} in {csv_path.name} — "
            f"got headers {list(df.columns)}. Check COLUMN_ALIASES in "
            f"app/import_holdings.py against this file's actual header row."
        )

    positions: Dict[str, float] = {}
    for _, row in df.iterrows():
        ticker_raw = row.get(mapping["ticker"])
        action_raw = row.get(mapping["action"])
        qty_raw = row.get(mapping["quantity"])

        if pd.isna(ticker_raw) or pd.isna(action_raw) or pd.isna(qty_raw):
            continue

        action = str(action_raw).strip().lower()
        try:
            qty = abs(float(qty_raw))
        except (TypeError, ValueError):
            continue

        if any(k in action for k in BUY_KEYWORDS):
            sign = 1
        elif any(k in action for k in SELL_KEYWORDS):
            sign = -1
        else:
            continue  # dividend, deposit, fee, transfer, etc. — not a position change

        ticker = _normalize_ticker(ticker_raw)
        positions[ticker] = positions.get(ticker, 0.0) + sign * qty

    return {t: s for t, s in positions.items() if s > 1e-9}


def resync_holdings(db, positions: Dict[str, float]) -> None:
    """Full replace: the holdings table becomes exactly `positions`.
    Preserves added_at for tickers that were already held, rather than
    resetting it on every sync."""
    existing = {h.ticker: h for h in db.query(Holding).all()}

    for ticker, shares in positions.items():
        if ticker in existing:
            existing[ticker].shares = shares
        else:
            db.add(Holding(ticker=ticker, shares=shares, added_at=utcnow()))

    for ticker, h in existing.items():
        if ticker not in positions:
            db.delete(h)

    db.commit()


def _pending_csvs() -> List[Path]:
    return sorted(
        (p for p in IMPORTS_DIR.glob("*.csv") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )


def _archive(csv_path: Path) -> None:
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    csv_path.rename(IMPORTS_PROCESSED_DIR / f"{stamp}_{csv_path.name}")


def run_import(db: Optional[object] = None) -> int:
    """Processes every CSV sitting in data/imports/, oldest first, so the
    most recently exported file wins if more than one is present. Safe to
    call with nothing there — it's a no-op. Returns files processed."""
    pending = _pending_csvs()
    if not pending:
        return 0

    owns_session = db is None
    if owns_session:
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()

    processed = 0
    try:
        for csv_path in pending:
            print(f"[import] Processing {csv_path.name}...")
            try:
                positions = parse_net_positions(csv_path)
            except Exception as e:
                print(f"[import] WARNING: skipping {csv_path.name} ({e})")
                continue

            resync_holdings(db, positions)
            print(f"[import] {csv_path.name}: {len(positions)} ticker(s) now held.")
            _archive(csv_path)
            processed += 1
    finally:
        if owns_session:
            db.close()

    return processed


if __name__ == "__main__":
    if DEMO_MODE:
        print(
            "[import] Refusing to run with DEMO_MODE=true — this would import your "
            "real Wealthsimple holdings into the fake demo database. Unset DEMO_MODE."
        )
        sys.exit(1)

    n = run_import()
    if n:
        print(f"[import] Done. {n} file(s) processed.")
    else:
        print("[import] Nothing to do — no CSVs in data/imports/.")
