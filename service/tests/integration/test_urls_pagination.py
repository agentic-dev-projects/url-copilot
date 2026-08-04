"""
Integration tests for URL list pagination.
"""

from fastapi.testclient import TestClient


def _register_and_get_key(client: TestClient, email: str = "user@example.com") -> str:
    res = client.post("/api/v1/auth/register", json={"email": email})
    return res.json()["api_key"]


def test_list_urls_pagination_integration(client: TestClient):
    key = _register_and_get_key(client, "paginate-integration@example.com")

    # Create multiple URLs for testing pagination
    for i in range(1, 23):
        client.post(
            "/api/v1/urls",
            json={"original_url": f"https://pagination.com/{i}"},
            headers={"x-api-key": key},
        )

    # Test pages are correct
    res = client.get("/api/v1/urls?skip=0&limit=10", headers={"x-api-key": key})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 10
    assert data["total"] == 22

    res = client.get("/api/v1/urls?skip=10&limit=10", headers={"x-api-key": key})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 10

    res = client.get("/api/v1/urls?skip=20&limit=10", headers={"x-api-key": key})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 2
