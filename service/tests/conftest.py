"""
Shared pytest fixtures for the url-copilot service test suite.

Provides:
- `test_db`  — isolated SQLite session, schema created/torn down per test
- `client`   — FastAPI TestClient wired to test_db via dependency override

Tests that need a real PostgreSQL connection (e.g. full integration tests)
should create their own fixtures and skip the override below.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from service.db.base import Base
from service.db.session import get_db
from service.main import app

_TEST_DATABASE_URL = "sqlite:///./test.db"

_test_engine = create_engine(
    _TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
_TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=_test_engine
)


@pytest.fixture()
def test_db():
    """Yield a fresh SQLite session; drop all tables after the test."""
    Base.metadata.create_all(bind=_test_engine)
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture()
def client(test_db):
    """Yield a TestClient whose get_db dependency is overridden with test_db."""

    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
