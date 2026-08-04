from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def test_generate_qr_code_endpoint_happy_path(client: TestClient):
    # Register a user and create a URL first
    key = client.post("/api/v1/auth/register", json={"email": "qr@example.com"}).json()["api_key"]
    url_id = client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com"},
        headers={"x-api-key": key},
    ).json()["id"]

    with patch("service.api.v1.endpoints.urls.url_service.generate_qr_code") as mock_qr:
        mock_qr.return_value = BytesIO(b"\x89PNG fake-bytes")
        res = client.get(f"/api/v1/urls/{url_id}/qr", headers={"x-api-key": key})

    assert res.status_code == 200
    data = res.json()
    assert "qr_code_url" in data
    assert data["qr_code_url"].startswith("data:image/png;base64,")


def test_generate_qr_code_endpoint_unauthenticated(client: TestClient):
    res = client.get("/api/v1/urls/any-id/qr")
    assert res.status_code == 401  # missing x-api-key header


def test_generate_qr_code_endpoint_not_found(client: TestClient):
    key = client.post("/api/v1/auth/register", json={"email": "qr2@example.com"}).json()["api_key"]
    res = client.get(
        "/api/v1/urls/00000000-0000-0000-0000-000000000000/qr",
        headers={"x-api-key": key},
    )
    assert res.status_code == 404
