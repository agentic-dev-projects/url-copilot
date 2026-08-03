"""
Shared pytest fixtures for the url-copilot service test suite.

Provides:
- `test_db`  — isolated SQLite session, schema created/torn down per test
- `client`   — FastAPI TestClient wired to test_db via dependency override

Infrastructure dependencies (Redis, PostgreSQL) are replaced with in-process
stubs so tests run without any external services.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from service.api.deps import check_rate_limit
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
    """
    Yield a TestClient with all infrastructure dependencies stubbed out.

    Overrides:
    - get_db          → SQLite test session (no PostgreSQL needed)
    - check_rate_limit → no-op (no Redis needed)
    """

    def _override_get_db():
        yield test_db

    def _override_rate_limit():
        pass  # always allow requests in tests

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[check_rate_limit] = _override_rate_limit

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
