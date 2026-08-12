from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import DATABASE_URL

# check_same_thread is a SQLite-only argument (needed because FastAPI
# serves requests from a threadpool). Guarded so a non-SQLite
# DATABASE_URL doesn't fail on an unexpected keyword.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sync_new_columns() -> None:
    """Adds columns that exist on the models but not yet in the database.

    `create_all()` only creates missing *tables* — it will not touch a
    table that already exists. So adding a column to a model leaves any
    existing database (your real.db, a deployed volume) one column short,
    and every query against that table fails with "no such column" until
    someone runs ALTER by hand.

    This closes that gap for the only case it can close safely: adding a
    new nullable column. Existing rows get NULL, which the models already
    treat as "unknown".

    It is deliberately NOT a migration system. It cannot rename, drop,
    re-type or backfill anything, and it won't notice if a column's
    definition changed. If this project ever needs that, reach for Alembic
    rather than growing this function.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all() handles brand-new tables

        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present or not column.nullable:
                continue
            ddl = column.type.compile(dialect=engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}'))
            print(f"[db] added missing column {table.name}.{column.name}")
