"""
Point-in-time universe lists.

The live pipeline scrapes *today's* S&P 500 membership. Backtesting that
over a past decade only tests companies that survived and stayed in the
index, which inflates results — see the survivorship section in
backend/README.md.

This module fetches a historical membership snapshot from the Wayback
Machine so a backtest can at least run against the index as it stood at
the start of the window, rather than as it stands now.

## What this fixes, and what it doesn't

It removes the *survivor filter*: companies that were members in 2016 and
later collapsed are back in the universe. Measured on the 2015-12-30
snapshot, that is 186 tickers the current list omits — 37% of the index.

It does NOT give a bias-free backtest, for two reasons:

1. **Yahoo has purged most of the failures.** Of a 40-ticker sample of
   removed names, 23 returned no data at all and only 3 showed a genuine
   delisting (price history ending early). The bankruptcies — the losses
   that matter most — are largely unavailable at any price from a free
   source. That is exactly why survivorship-free datasets are sold
   commercially.

2. **It introduces the opposite bias.** A fixed snapshot excludes the 185
   companies added after 2016, several of which were enormous winners. So
   this run tends to *understate* where the current-list run overstates.

Used together the two runs bracket the truth rather than pinpoint it,
which is still far more than either says alone.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import List

import pandas as pd
import requests

from ..config import DATA_DIR
from ..pipeline import USER_AGENT

UNIVERSE_DIR = DATA_DIR / "universes"
WAYBACK_AVAILABLE = "https://archive.org/wayback/available"
WIKI_SP500 = "en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# The column holding the symbol has been renamed on the Wikipedia page over
# the years ("Ticker symbol" in 2015, "Symbol" today), so match by alias
# rather than pinning one spelling.
SYMBOL_COLUMNS = ("Ticker symbol", "Symbol", "Ticker")


def snapshot_url(timestamp: str) -> str:
    """Nearest archived copy of the S&P 500 page to `timestamp` (YYYYMMDD)."""
    response = requests.get(
        WAYBACK_AVAILABLE,
        params={"url": WIKI_SP500, "timestamp": timestamp},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    closest = response.json().get("archived_snapshots", {}).get("closest")
    if not closest or not closest.get("available"):
        raise RuntimeError(f"No Wayback snapshot of the S&P 500 page near {timestamp}")
    return closest["url"]


def fetch_membership(timestamp: str) -> List[str]:
    """S&P 500 tickers as of the archived snapshot nearest `timestamp`."""
    url = snapshot_url(timestamp)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()

    table = pd.read_html(io.StringIO(response.text))[0]
    column = next((c for c in SYMBOL_COLUMNS if c in table.columns), None)
    if column is None:
        raise RuntimeError(
            f"No symbol column in the {timestamp} snapshot; saw {list(table.columns)}"
        )

    # Same '.' -> '-' conversion the live pipeline applies, so these join
    # against Yahoo symbols (BRK.B -> BRK-B).
    return sorted({str(s).strip().upper().replace(".", "-") for s in table[column] if str(s).strip()})


def save_membership(tickers: List[str], name: str, source: str = "") -> Path:
    """Writes a universe file. Committed to the repo so a run is
    reproducible without re-scraping an archive that may change."""
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    path = UNIVERSE_DIR / f"{name}.txt"
    header = [f"# {name}", f"# {len(tickers)} tickers"]
    if source:
        header.append(f"# source: {source}")
    path.write_text("\n".join(header + tickers) + "\n")
    return path


def load_membership(path: Path) -> List[str]:
    """Reads a universe file, ignoring '#' comments and blank lines."""
    lines = Path(path).read_text().splitlines()
    return sorted({l.strip().upper() for l in lines if l.strip() and not l.startswith("#")})


if __name__ == "__main__":  # pragma: no cover - one-off fetch
    import sys

    stamp = sys.argv[1] if len(sys.argv) > 1 else "20160101"
    name = sys.argv[2] if len(sys.argv) > 2 else f"sp500_{stamp[:4]}"
    url = snapshot_url(stamp)
    tickers = fetch_membership(stamp)
    written = save_membership(tickers, name, source=url)
    print(f"{len(tickers)} tickers -> {written}")
    print(f"source: {url}")
