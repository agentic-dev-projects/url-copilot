"""
Integration tests for authentication endpoints.

Uses the SQLite-backed TestClient fixture from conftest.py — no live DB needed.
"""

from fastapi.testclient import TestClient


def test_register_returns_api_key(client: TestClient):
    res = client.post("/api/v1/auth/register", json={"email": "test@example.com"})
    assert res.status_code == 201
    data = res.json()
    assert data["api_key"].startswith("sk_")
    assert "user_id" in data
    assert "key_prefix" in data


def test_register_duplicate_email_returns_409(client: TestClient):
    client.post("/api/v1/auth/register", json={"email": "dup@example.com"})
    res = client.post("/api/v1/auth/register", json={"email": "dup@example.com"})
    assert res.status_code == 409


def test_register_invalid_email_returns_422(client: TestClient):
    res = client.post("/api/v1/auth/register", json={"email": "not-an-email"})
    assert res.status_code == 422


def test_rotate_key_returns_new_key(client: TestClient):
    reg = client.post("/api/v1/auth/register", json={"email": "rotate@example.com"})
    old_key = reg.json()["api_key"]

    res = client.post("/api/v1/auth/rotate-key", headers={"x-api-key": old_key})
    assert res.status_code == 200
    new_key = res.json()["api_key"]
    assert new_key != old_key
    assert new_key.startswith("sk_")


def test_rotate_key_invalidates_old_key(client: TestClient):
    reg = client.post("/api/v1/auth/register", json={"email": "invalidate@example.com"})
    old_key = reg.json()["api_key"]

    client.post("/api/v1/auth/rotate-key", headers={"x-api-key": old_key})

    # Old key should now be rejected
    res = client.get("/api/v1/urls", headers={"x-api-key": old_key})
    assert res.status_code == 401


def test_request_without_api_key_returns_401(client: TestClient):
    res = client.get("/api/v1/urls")
    assert res.status_code == 401
