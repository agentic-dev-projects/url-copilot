from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def test_list_urls_pagination(client: TestClient):
    key = client.post("/api/v1/auth/register", json={"email": "paginate@example.com"}).json()["api_key"]

    # Create multiple URLs
    for i in range(1, 15):
        client.post(
            "/api/v1/urls",
            json={"original_url": f"https://example.com/{i}"},
            headers={"x-api-key": key},
        )

    # Test default pagination
    res = client.get("/api/v1/urls", headers={"x-api-key": key})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 10  # default limit
    assert data["total"] == 14

    # Test custom pagination
    res = client.get("/api/v1/urls?skip=10&limit=5", headers={"x-api-key": key})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 4  # remaining items

    res = client.get("/api/v1/urls?skip=0&limit=5", headers={"x-api-key": key})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 5
    assert data["total"] == 14

    # Test skip exceeding total
    res = client.get("/api/v1/urls?skip=20&limit=5", headers={"x-api-key": key})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 0
