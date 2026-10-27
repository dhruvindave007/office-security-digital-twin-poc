# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from fastapi.testclient import TestClient
from src.main import app


def test_get_bedrock_system_status():
    """Verify GET /api/system/bedrock returns readiness details without billable calls."""
    with TestClient(app) as client:
        response = client.get("/api/system/bedrock")
        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert data["status"] in ["ready", "uncredentialed"]
        assert "configured_region" in data
        assert "reasoning_model_id" in data
        assert "streaming_supported" in data
        assert "multimodal_supported" in data
        assert "validation_source" in data
        assert "boto3_version" in data
        assert "verified_at" in data
