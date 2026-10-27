# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Access control and HTTP hardening (src/api/security.py) plus the fixes from the security review."""
import pytest
from fastapi.testclient import TestClient

from src.api import security
from src.config import get_settings

TOKEN = "t" * 40


@pytest.fixture
def client(monkeypatch):
    from src.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_token", TOKEN)
    security.login_limiter._fails.clear()
    yield TOKEN
    security.login_limiter._fails.clear()


def test_no_token_allows_loopback_only(client, monkeypatch):
    assert client.get("/api/twin/zones").status_code == 200        # TestClient counts as local
    monkeypatch.setattr(security, "_is_loopback", lambda host: False)
    r = client.get("/api/twin/zones")
    assert r.status_code == 403 and "OVT_API_TOKEN" in r.json()["detail"]
    assert client.get("/api/health").status_code == 200            # health stays open for probes


@pytest.mark.parametrize("path", ["/api/twin/zones", "/api/twin/incidents", "/api/stream", "/api/live/status",
                                  "/api/evidence/clips/x.mp4", "/docs", "/openapi.json"])
def test_token_required_everywhere(client, token, path):
    assert client.get(path).status_code == 401


def test_bearer_and_cookie_login(client, token):
    assert client.get("/api/twin/zones", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get("/api/twin/zones", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/auth/status").json() == {"auth_required": True, "authenticated": False}
    r = client.post("/api/auth/login", json={"token": TOKEN})
    assert r.status_code == 200
    cookie = r.cookies.get(security.SESSION_COOKIE)
    assert cookie and TOKEN not in cookie                            # cookie never carries the token
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/api/twin/zones").status_code == 200
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/twin/zones").status_code == 401


def test_login_rate_limited(client, token):
    for _ in range(security.LOGIN_MAX_FAILURES):
        assert client.post("/api/auth/login", json={"token": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"token": TOKEN}).status_code == 429   # locked even for the right one


def test_short_token_rejected():
    from src.config import Settings
    with pytest.raises(ValueError):
        Settings(OVT_API_TOKEN="short")


def test_security_headers_and_body_limit(client):
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    r = client.post("/api/twin/incidents/status", content=b"x" * (security.MAX_BODY_BYTES + 1),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_live_start_rejects_client_paths(client):
    r = client.post("/api/live/start", json={"config_path": "C:\Windows\win.ini"})
    assert r.status_code == 400 and "win.ini" not in r.text


def test_huge_offset_is_a_validation_error_not_a_crash(client):
    assert client.get("/api/twin/events?offset=99999999999999999999").status_code == 422


def test_unexpected_errors_are_generic(client, monkeypatch):
    from src.main import app
    monkeypatch.setattr(app.state.twin_engine.store, "get_zones",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret-internal-path")), raising=False)
    r = client.get("/api/twin/zones")
    assert r.status_code == 500 and "secret" not in r.text
