# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
def test_health_endpoint(client):
    """Test /health endpoint returns expected status, service name, and version."""
    response = client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload.get("status") == "ok"
    assert payload.get("service") == "office-vision-backend"
    assert payload.get("version") == "0.1.0"
    assert "environment" in payload
