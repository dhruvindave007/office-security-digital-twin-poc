# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
import pytest
from fastapi.testclient import TestClient

try:
    from src.main import app
    from src.config import get_settings
except ImportError:
    from main import app
    from config import get_settings


@pytest.fixture(autouse=True)
def isolate_test_database(tmp_path, monkeypatch):
    """Ensure every test runs against an isolated throwaway database and never touches backend/data/digital_twin.db."""
    test_db = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    """Fixture providing a FastAPI TestClient instance."""
    with TestClient(app) as test_client:
        yield test_client
