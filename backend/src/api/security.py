"""Access control and HTTP hardening for the whole API.

One middleware guards every route, so a new router cannot be left open by forgetting a dependency:

- No `api_token` configured -> the server answers only the machine it runs on (loopback). Safe default
  for a laptop; anything reachable over the network must set a token.
- `api_token` configured -> every `/api/*` request (and the API docs) needs either
  `Authorization: Bearer <token>` (scripts, integrations) or the HttpOnly session cookie set by
  `POST /api/auth/login` (the dashboard; also covers <img>, <video> and EventSource, which cannot send headers).

The cookie holds an HMAC of the token, never the token itself; changing the token logs everyone out.
Failed logins are rate limited per client address. Every response gets defensive headers, request
bodies are size-capped, and unexpected errors return a generic message (details go to the server log).
"""
import hashlib
import hmac
import ipaddress
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import get_settings

logger = logging.getLogger("office_vision.security")

SESSION_COOKIE = "ovt_session"
OPEN_PATHS = {"/health", "/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}
PROTECTED_PREFIXES = ("/api/", "/docs", "/redoc", "/openapi.json")
MAX_BODY_BYTES = 1_000_000          # the API takes small JSON bodies only
LOGIN_MAX_FAILURES = 10             # per client address ...
LOGIN_WINDOW_SECONDS = 300.0        # ... within this window, then locked out until old failures expire

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    # Dashboard is same-origin static files; camera pictures arrive as blob: URLs.
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


def _session_value(token: str) -> str:
    return hmac.new(token.encode(), b"ovt-session-v1", hashlib.sha256).hexdigest()


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in ("localhost", "testclient"):   # Starlette's TestClient reports "testclient"
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_authenticated(request: Request) -> bool:
    token = get_settings().api_token
    if not token:
        return _is_loopback(request.client.host if request.client else None)
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer " and hmac.compare_digest(auth[7:].strip().encode(), token.encode()):
        return True
    cookie = request.cookies.get(SESSION_COOKIE, "")
    return bool(cookie) and hmac.compare_digest(cookie.encode(), _session_value(token).encode())


class _LoginLimiter:
    def __init__(self) -> None:
        self._fails: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, q: Deque[float], now: float) -> None:
        while q and now - q[0] > LOGIN_WINDOW_SECONDS:
            q.popleft()

    def locked(self, who: str) -> bool:
        with self._lock:
            q = self._fails[who]
            self._trim(q, time.monotonic())
            return len(q) >= LOGIN_MAX_FAILURES

    def fail(self, who: str) -> None:
        with self._lock:
            q = self._fails[who]
            q.append(time.monotonic())
            self._trim(q, q[-1])
            if len(self._fails) > 10_000:          # bound memory under a distributed attack
                for k in [k for k, v in self._fails.items() if not v][:5_000]:
                    del self._fails[k]

    def reset(self, who: str) -> None:
        with self._lock:
            self._fails.pop(who, None)


login_limiter = _LoginLimiter()


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method != "OPTIONS" and path not in OPEN_PATHS and path.startswith(PROTECTED_PREFIXES):
            if not is_authenticated(request):
                detail = ("Authentication required" if get_settings().api_token else
                          "This server only accepts local connections. Set OVT_API_TOKEN to allow remote access.")
                return self._harden(JSONResponse({"detail": detail}, status_code=401 if get_settings().api_token else 403))
        length = request.headers.get("content-length")
        if length is not None:
            try:
                too_big = int(length) > MAX_BODY_BYTES
            except ValueError:
                return self._harden(JSONResponse({"detail": "Invalid Content-Length"}, status_code=400))
            if too_big:
                return self._harden(JSONResponse({"detail": "Request body too large"}, status_code=413))
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error on %s %s", request.method, path)
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)
        return self._harden(response)

    @staticmethod
    def _harden(response: Response) -> Response:
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if get_settings().cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


@router.get("/status")
async def auth_status(request: Request):
    s = get_settings()
    return {"auth_required": bool(s.api_token), "authenticated": is_authenticated(request)}


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    s = get_settings()
    who = request.client.host if request.client else "unknown"
    if not s.api_token:
        return JSONResponse({"detail": "Authentication is not enabled on this server"}, status_code=400)
    if login_limiter.locked(who):
        return JSONResponse({"detail": "Too many failed attempts. Try again in a few minutes."}, status_code=429)
    if not hmac.compare_digest(body.token.encode(), s.api_token.encode()):
        login_limiter.fail(who)
        logger.warning("Failed login from %s", who)
        return JSONResponse({"detail": "Invalid access token"}, status_code=401)
    login_limiter.reset(who)
    resp = JSONResponse({"authenticated": True})
    resp.set_cookie(SESSION_COOKIE, _session_value(s.api_token), httponly=True, samesite="strict",
                    secure=s.cookie_secure, max_age=int(s.session_hours * 3600), path="/")
    return resp


@router.post("/logout")
async def logout():
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
