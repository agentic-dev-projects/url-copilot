"""
Integration tests for URL management and redirect endpoints.
"""

from fastapi.testclient import TestClient


def _register_and_get_key(client: TestClient, email: str = "user@example.com") -> str:
    res = client.post("/api/v1/auth/register", json={"email": email})
    return res.json()["api_key"]


def test_shorten_url_returns_201(client: TestClient):
    key = _register_and_get_key(client)
    res = client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com/very/long/path"},
        headers={"x-api-key": key},
    )
    assert res.status_code == 201
    data = res.json()
    assert "short_code" in data
    assert data["short_url"].endswith(data["short_code"])
    assert data["click_count"] == 0


def test_shorten_url_with_custom_alias(client: TestClient):
    key = _register_and_get_key(client, "alias@example.com")
    res = client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com", "custom_alias": "my-link"},
        headers={"x-api-key": key},
    )
    assert res.status_code == 201
    assert res.json()["short_code"] == "my-link"


def test_duplicate_alias_returns_422(client: TestClient):
    key = _register_and_get_key(client, "dup-alias@example.com")
    payload = {"original_url": "https://example.com", "custom_alias": "taken"}
    client.post("/api/v1/urls", json=payload, headers={"x-api-key": key})
    res = client.post("/api/v1/urls", json=payload, headers={"x-api-key": key})
    assert res.status_code == 422


def test_list_urls_returns_created_links(client: TestClient):
    key = _register_and_get_key(client, "list@example.com")
    client.post("/api/v1/urls", json={"original_url": "https://a.com"}, headers={"x-api-key": key})
    client.post("/api/v1/urls", json={"original_url": "https://b.com"}, headers={"x-api-key": key})

    res = client.get("/api/v1/urls", headers={"x-api-key": key})
    assert res.status_code == 200
    assert res.json()["total"] == 2


def test_delete_url_returns_204(client: TestClient):
    key = _register_and_get_key(client, "delete@example.com")
    create = client.post("/api/v1/urls", json={"original_url": "https://del.com"}, headers={"x-api-key": key})
    url_id = create.json()["id"]

    res = client.delete(f"/api/v1/urls/{url_id}", headers={"x-api-key": key})
    assert res.status_code == 204


def test_redirect_returns_302(client: TestClient):
    key = _register_and_get_key(client, "redirect@example.com")
    create = client.post(
        "/api/v1/urls",
        json={"original_url": "https://destination.com"},
        headers={"x-api-key": key},
    )
    short_code = create.json()["short_code"]

    res = client.get(f"/{short_code}", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"].rstrip("/") == "https://destination.com"


def test_redirect_unknown_code_returns_404(client: TestClient):
    res = client.get("/nonexistent-code", follow_redirects=False)
    assert res.status_code == 404


def test_unauthenticated_shorten_returns_422(client: TestClient):
    res = client.post("/api/v1/urls", json={"original_url": "https://example.com"})
    assert res.status_code == 422
