"""
Database connection setup.

Defaults to a local SQLite file (sentinel.db) so Section 4 runs with
zero external dependencies. Set DATABASE_URL to a real Postgres
connection string (e.g. postgresql://user:pass@host:5432/sentinel)
and nothing else in this codebase changes -- that's the entire point
of going through SQLAlchemy instead of hand-rolled SQL.

NOTE: this has only been tested against SQLite in this environment
(no Postgres server was reachable here). The schema uses only
standard, portable column types, but treat the first real Postgres
run as a genuine test, not a formality.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base
from ..config import get_database_url

DATABASE_URL = get_database_url()

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create all tables if they don't already exist. Safe to call repeatedly."""
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
