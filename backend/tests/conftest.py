import pytest
from fastapi.testclient import TestClient

try:
    from main import app
except ImportError:
    from src.main import app


@pytest.fixture
def client():
    """Fixture providing a FastAPI TestClient instance."""
    with TestClient(app) as test_client:
        yield test_client
