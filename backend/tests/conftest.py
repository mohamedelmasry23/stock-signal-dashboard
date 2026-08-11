"""
Shared test fixtures.

The environment setup below runs at import, before any `app.*` module is
imported, and that ordering is load-bearing: `app.config` resolves
DATABASE_URL at import time and `app.main` calls `create_all()` on it, so
importing the app first would create tables in the real `data/real.db`.
Pointing DATABASE_URL at a throwaway file first keeps the suite entirely
off the user's actual data.

The DEMO_MODE / API_KEY / READ_ONLY vars are cleared for the same reason:
a suite that passes or fails depending on what's exported in your shell
isn't testing anything.
"""

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="stock-tracker-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR / 'test.db'}"
for _var in ("DEMO_MODE", "API_KEY", "READ_ONLY", "ALLOWED_ORIGINS"):
    os.environ.pop(_var, None)


@pytest.fixture
def db():
    """A session against an empty database. Tables are dropped and rebuilt
    per test so no test can see another's rows."""
    from app.database import Base, SessionLocal, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    """TestClient wired to the same session as the `db` fixture, so a test
    can set up state directly and then assert on it through the API."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def ohlc():
    """Deterministic OHLC builder: give it a list of closes and it returns
    a frame shaped the way compute_signal_frame() expects. High/low are
    padded a fixed amount around the close so ATR is predictable rather
    than random."""
    import pandas as pd

    def _build(closes, pad=1.0):
        idx = pd.bdate_range(end="2026-08-11", periods=len(closes))
        return pd.DataFrame(
            {
                "Open": closes,
                "High": [c + pad for c in closes],
                "Low": [c - pad for c in closes],
                "Close": closes,
                "Volume": [1_000_000] * len(closes),
            },
            index=idx,
        )

    return _build
