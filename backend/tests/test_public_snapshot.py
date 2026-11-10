"""The shareable snapshot must carry the dashboard and nothing about the
account it was built from."""

import sqlite3
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bootstrap import bootstrap
from app.public_snapshot import SnapshotRefused, build_snapshot


def _source_db(path):
    """A database with the app's real schema, one owner's holdings and
    settings, and an equity-sized signal — everything the scrub targets."""
    from app.database import Base
    from app import models

    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([
        models.Holding(ticker="ZZOWNED", shares=123.456),
        models.AccountSettings(id=1, cash_balance=98765.43, risk_fraction=0.02, equity_override=55555.0),
        models.Universe(ticker="SPYX", asset_class="equity", active=True),
        models.Signal(
            ticker="SPYX", as_of_date=date(2026, 11, 9), close=110.0,
            donchian_high=100.0, donchian_low=90.0, atr=2.0, stop_distance=4.0,
            entry_signal=True, exit_signal=False, suggested_shares=246.9,
        ),
    ])
    s.commit()
    s.close()
    engine.dispose()
    return path


def test_the_snapshot_keeps_signals_and_drops_everything_personal(tmp_path):
    src = _source_db(tmp_path / "real.db")
    dest = tmp_path / "snap" / "dashboard.db"

    counts = build_snapshot(src, dest)

    assert counts["holdings"] == 0
    assert counts["account_settings"] == 0
    assert counts["signals"] == 1
    conn = sqlite3.connect(dest)
    assert conn.execute("SELECT ticker, suggested_shares FROM signals").fetchall() == [("SPYX", None)]
    conn.close()
    # VACUUM rewrote the file, so the deleted rows aren't sitting in free pages.
    raw = dest.read_bytes()
    assert b"ZZOWNED" not in raw
    # The source is read, never written.
    assert sqlite3.connect(src).execute("SELECT COUNT(*) FROM holdings").fetchone() == (1,)


def test_an_unclassified_table_refuses_and_keeps_the_old_snapshot(tmp_path):
    src = _source_db(tmp_path / "real.db")
    dest = tmp_path / "dashboard.db"
    build_snapshot(src, dest)
    before = dest.read_bytes()

    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE broker_tokens (token TEXT)")
    conn.commit()
    conn.close()

    with pytest.raises(SnapshotRefused, match="broker_tokens"):
        build_snapshot(src, dest)
    assert dest.read_bytes() == before
    assert not (tmp_path / "dashboard.db.tmp").exists()


def test_bootstrap_copies_once_and_never_overwrites(tmp_path):
    snap = tmp_path / "dashboard.db"
    build_snapshot(_source_db(tmp_path / "real_src.db"), snap)
    target = tmp_path / "data" / "real.db"

    bootstrap(snap, target)
    assert target.read_bytes() == snap.read_bytes()

    target.write_bytes(b"someone's real holdings")
    with pytest.raises(FileExistsError):
        bootstrap(snap, target)
    assert target.read_bytes() == b"someone's real holdings"


def test_the_api_on_a_snapshot_shows_no_holdings_and_the_placeholder_account(tmp_path):
    from fastapi.testclient import TestClient

    from app.account import DEFAULT_EQUITY
    from app.database import get_db
    from app.main import app

    snap = tmp_path / "dashboard.db"
    build_snapshot(_source_db(tmp_path / "real.db"), snap)

    engine = create_engine(f"sqlite:///{snap}", connect_args={"check_same_thread": False})
    session = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as c:
            assert c.get("/api/holdings").json() == []
            account = c.get("/api/account").json()
            assert account["equity_source"] == "default"
            assert account["equity"] == DEFAULT_EQUITY
            # Sized from the placeholder at request time: 1% of 100k / 4.0.
            [cand] = c.get("/api/entry-candidates").json()
            assert cand["ticker"] == "SPYX"
            assert cand["suggested_shares"] == pytest.approx(250.0)
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
