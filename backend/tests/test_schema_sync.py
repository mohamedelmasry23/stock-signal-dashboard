"""
Tests for the additive-column sync.

`create_all()` won't touch a table that already exists, so adding a column
to a model leaves every existing database one column short and every query
against that table failing. This is the safety net for that, and it needs
to be exercised — it only runs when someone changes a model, which is
exactly when nobody is looking at it.
"""

from sqlalchemy import Column, Float, MetaData, String, Table, inspect, text

from app.database import Base, engine, sync_new_columns


def test_adds_a_column_that_exists_on_the_model_but_not_the_table(db):
    """Simulates the real failure: a table created before a column was
    added to its model."""
    Base.metadata.drop_all(bind=engine)

    # Build the table deliberately missing `prev_close`.
    partial = MetaData()
    Table(
        "signals",
        partial,
        Column("ticker", String, primary_key=True),
        Column("close", Float),
    )
    partial.create_all(bind=engine)

    assert "prev_close" not in {c["name"] for c in inspect(engine).get_columns("signals")}

    sync_new_columns()

    assert "prev_close" in {c["name"] for c in inspect(engine).get_columns("signals")}


def test_is_idempotent(db):
    """Runs on every startup, so a second pass must be a no-op rather than
    erroring on a duplicate column."""
    sync_new_columns()
    sync_new_columns()  # would raise "duplicate column name" if not guarded


def test_existing_rows_get_null_for_the_new_column(db):
    """Backfilling isn't this function's job — the models already treat
    null as 'unknown', which is the honest value for a column that didn't
    exist when the row was written."""
    Base.metadata.drop_all(bind=engine)

    partial = MetaData()
    Table(
        "signals",
        partial,
        Column("ticker", String, primary_key=True),
        Column("close", Float),
    )
    partial.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO signals (ticker, close) VALUES ('OLD', 100.0)"))

    sync_new_columns()

    with engine.begin() as conn:
        value = conn.execute(text("SELECT prev_close FROM signals WHERE ticker = 'OLD'")).scalar()
    assert value is None
