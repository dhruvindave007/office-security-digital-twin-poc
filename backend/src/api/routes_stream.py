"""Server-Sent Events push channel for the dashboard: GET /api/stream

One long-lived HTTP response per browser tab (EventSource). Messages:
  event: incident   data: IncidentResponse JSON   (new or changed, the moment the rules decide)
  event: state      data: twin state (tracks, positions, occupancy)   every `state_every` seconds
  event: live       data: live camera status                          every 2 s while cameras run
  event: resync     data: {}   the client fell behind and messages were dropped: refetch lists
Incidents come from the rule engine (any thread); they are handed to each client's asyncio queue
with call_soon_threadsafe, so the twin lock is never held while sending.
"""
import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Set

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("office_vision.api.stream")
router = APIRouter(tags=["Live Push"])
_QUEUE_MAX = 500


class Broadcaster:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self._clients: Set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def add(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        with self._lock:
            self._clients.add(q)
        return q

    def remove(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def publish(self, event: str, data: Any) -> None:
        """Thread-safe: may be called from rule-engine / camera threads."""
        msg = (event, data)
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            self.loop.call_soon_threadsafe(self._offer, q, msg)

    @staticmethod
    def _offer(q: asyncio.Queue, msg) -> None:
        if q.full():          # slow client: drop its backlog and tell it to refetch
            while not q.empty():
                q.get_nowait()
            q.put_nowait(("resync", {}))
        else:
            q.put_nowait(msg)

    def incident_listener(self, to_view):
        def on_incidents(_new: List[Any], changed: List[Any]) -> None:
            if not self._clients:
                return
            for inc in changed:
                try:
                    self.publish("incident", to_view(inc))
                except Exception:
                    logger.warning("incident push failed", exc_info=True)
        return on_incidents


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.get("/api/stream")
async def stream(request: Request, state_every: float = 1.0):
    """Push channel (text/event-stream). Use from a browser: new EventSource('/api/stream')."""
    try:  # local import: avoids a router import cycle
        from src.api.routes_twin import get_twin_state
    except ImportError:
        from api.routes_twin import get_twin_state
    bc: Broadcaster = request.app.state.broadcaster
    q = bc.add()
    state_every = max(0.25, min(state_every, 10.0))

    async def gen():
        loop = asyncio.get_running_loop()
        next_state = next_live = loop.time()
        try:
            yield b"retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                now = loop.time()
                if now >= next_state:
                    next_state = now + state_every
                    st = await get_twin_state(request)
                    yield _sse("state", st.model_dump())
                if now >= next_live:
                    next_live = now + 2.0
                    mgr = getattr(request.app.state, "live_manager", None)
                    if mgr is not None and mgr.running:
                        yield _sse("live", await asyncio.to_thread(mgr.status))
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=max(0.05, min(next_state, next_live) - loop.time()))
                    yield _sse(event, data)
                except asyncio.TimeoutError:
                    pass
        finally:
            bc.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
