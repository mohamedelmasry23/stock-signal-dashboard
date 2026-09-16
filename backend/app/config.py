import os
import re
from pathlib import Path

# DEMO_MODE is the single switch for the future public/portfolio deploy.
# When true, the app points at a seeded fake database instead of the real
# one, so no real holdings or signal data can ever be exposed. Same code,
# different db file — set the env var, don't touch anything else.
DEMO_MODE = os.getenv("DEMO_MODE", "false").strip().lower() in ("1", "true", "yes")

# Origins allowed to call the API from a browser. Defaults to the local
# Vite dev server only — `*` was fine while this was localhost-only, but
# it's the wrong default the moment anything is deployed, and a default
# you have to remember to change is a default that stays wrong.
# Set a comma-separated list for deploys, e.g.
#   ALLOWED_ORIGINS=https://stock-dash.example.com
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]

# When set, every mutating endpoint requires an `X-API-Key` header that
# matches. Unset (the default) leaves writes open, which is right for a
# localhost-only instance and wrong for anything reachable from the
# internet — see the startup banner in app/main.py, which says out loud
# which mode is active rather than leaving it to be discovered.
API_KEY = os.getenv("API_KEY", "").strip()

# Blocks all mutating endpoints outright, regardless of API_KEY. This is
# the setting for a public demo: visitors get a fully browsable dashboard
# that they cannot alter for everyone else.
READ_ONLY = os.getenv("READ_ONLY", "false").strip().lower() in ("1", "true", "yes")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILENAME = "demo.db" if DEMO_MODE else "real.db"

# Overridable so a deploy can point at a mounted disk (or another engine)
# without code changes, and so the test suite can use a throwaway file
# instead of the real database.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or f"sqlite:///{DATA_DIR / DB_FILENAME}"

# A backtest's universe label names a point-in-time membership snapshot
# when it carries the snapshot year — `sp500_2016,etfs`, produced by
# universe_label() in app/backtest/__main__.py.
#
# Both the backtest report and the API decide their survivorship wording
# from this, and they must never disagree: one says the run overstates,
# the other says it understates, and the wrong one on the wrong run is
# actively misleading. It lives here rather than in the backtest package
# because app/main.py must not import that package (see its docstring),
# and here is the one dependency-free module both already import.
POINT_IN_TIME_UNIVERSE = re.compile(r"sp500_\d{4}")


def is_point_in_time(universe_label: str) -> bool:
    """True when a run's universe came from a frozen membership snapshot
    rather than from today's scraped index."""
    return bool(POINT_IN_TIME_UNIVERSE.search(universe_label or ""))


# The third kind of S&P run: membership switched day by day, either from
# dated change events (`sp500_events,etfs`) or from the constituents
# workbook's intervals (`sp500_constituents,etfs`, the S&P source from
# 2026-09-14 on) — see app/backtest/membership.py. The only kind a person
# could actually have traded, so when one is saved the API makes it the
# headline and serves the today's-list run beside it as `current_list`.
ROLLING_MEMBERSHIP_UNIVERSE = re.compile(r"sp500_(events|constituents)")


def is_rolling_membership(universe_label: str) -> bool:
    return bool(ROLLING_MEMBERSHIP_UNIVERSE.search(universe_label or ""))
