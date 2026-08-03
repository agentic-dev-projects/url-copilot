"""
Database session factory.

Provides:
- `engine`        — SQLAlchemy engine (one per process)
- `SessionLocal`  — session factory for use outside HTTP requests
- `get_db()`      — FastAPI dependency that yields a session per request

Usage in a route:
    from fastapi import Depends
    from sqlalchemy.orm import Session
    from service.db.session import get_db

    @router.get("/example")
    def example(db: Session = Depends(get_db)):
        ...
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from service.config import settings

engine = create_engine(
    settings.database_url,
    # Recycle connections that were dropped by the server (handles idle timeouts)
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a DB session and guarantees cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
