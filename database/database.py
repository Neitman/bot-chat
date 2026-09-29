"""Database connection and session management module.

Sets up the SQLAlchemy engine, session maker, base model, and provides
a context manager `get_db` for safe, scoped session handling.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from config import DATABASE_URL

# For SQLite, check_same_thread needs to be False for multithreaded / async handlers
connect_args: dict[str, Any] = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

# SQLAlchemy Engine
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    future=True,
)

# Configured sessionmaker for thread-local sessions
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# Declarative Base class for all ORM models
Base = declarative_base()


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Provide a transactional database session scope.

    Yields:
        Session: Active SQLAlchemy session.

    Usage:
        with get_db() as db:
            user = db.query(User).first()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
