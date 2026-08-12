import os
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
