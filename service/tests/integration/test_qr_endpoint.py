from fastapi.testclient import TestClient

def _register_and_get_key(client: TestClient, email: str = "user@example.com") -> str:
    res = client.post("/api/v1/auth/register", json={"email": email})
    return res.json()["api_key"]

def test_qr_code_generation_integration(client: TestClient):
    key = _register_and_get_key(client, "integration@example.com")

    # Create a URL
    create_response = client.post(
        "/api/v1/urls",
        json={"original_url": "https://example.com/integration"},
        headers={"x-api-key": key},
    )
    assert create_response.status_code == 201
    url_id = create_response.json()["id"]

    # Request QR code
    qr_response = client.get(f"/api/v1/urls/{url_id}/qr", headers={"x-api-key": key})
    assert qr_response.status_code == 200
    data = qr_response.json()
    assert "qr_code_url" in data
    assert data["qr_code_url"].startswith("data:image/png;base64,")
