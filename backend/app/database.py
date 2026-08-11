from sqlalchemy import create_engine
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
