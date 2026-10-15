"""Automatic AI investigation of serious incidents, off the live path.

The rule engine notifies us of new incidents (inside the twin lock, so we only enqueue). A single
background thread then, per incident:
  1. skips it unless severity >= min_severity, and applies duplicate suppression: one investigation
     per (incident type, room) per cooldown (a 14-person rush would otherwise mean 13 calls),
     plus an hourly cap;
  2. waits (bounded) for its evidence clip, so the investigation starts after the post-roll;
  3. calls the normal InvestigationService (same verification, cost logging and daily budget).
If AWS sign-in has expired, auto-investigation pauses for `pause_on_credentials_seconds` instead of
failing every incident; the deterministic rules and evidence recording keep running regardless.
"""
from collections import deque
from datetime import datetime, timezone
import logging
import queue
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional

try:
    from src.ai.errors import BedrockCredentialsError, BedrockError
    from src.ai.investigation.service import BudgetExceededError, IncidentNotFoundError
except ImportError:
    from ai.errors import BedrockCredentialsError, BedrockError
    from ai.investigation.service import BudgetExceededError, IncidentNotFoundError

logger = logging.getLogger("office_vision.ai.auto")
SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
AUTO_QUESTION = ("Automatic review: what happened, how certain is it from the evidence, and what should the "
                 "operator check first?")


class AutoInvestigator:
    def __init__(self, service, min_severity: str = "HIGH", cooldown_seconds: float = 600.0, max_per_hour: int = 12,
                 evidence_wait_seconds: float = 30.0, pause_on_credentials_seconds: float = 600.0,
                 wait_for_evidence: Optional[Callable[[str, float], bool]] = None, clock=time.monotonic):
        self.service = service
        self.min_rank = SEVERITY_RANK.get(min_severity, 2)
        self.cooldown = cooldown_seconds
        self.max_per_hour = max_per_hour
        self.evidence_wait = evidence_wait_seconds
        self.pause_s = pause_on_credentials_seconds
        self.wait_for_evidence = wait_for_evidence
        self.clock = clock
        self._q: "queue.Queue[Optional[Any]]" = queue.Queue()
        self._last_by_key: Dict[tuple, float] = {}
        self._recent: Deque[float] = deque()
        self._paused_until = 0.0
        self.history: Deque[Dict[str, Any]] = deque(maxlen=100)
        self.counts: Dict[str, int] = {}
        self._thread = threading.Thread(target=self._run, name="auto-investigator", daemon=True)
        self._thread.start()

    # rule-engine listener (inside the twin lock: enqueue only)
    def on_incidents(self, new: List[Any], _changed: List[Any]) -> None:
        for inc in new:
            if SEVERITY_RANK.get(inc.severity, 0) >= self.min_rank:
                self._q.put(inc)

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(10)

    def _record(self, inc, outcome: str, **extra) -> None:
        self.counts[outcome] = self.counts.get(outcome, 0) + 1
        self.history.appendleft({"incident_id": inc.incident_id, "incident_type": inc.incident_type,
                                 "zone_id": inc.zone_id, "outcome": outcome,
                                 "at": datetime.now(timezone.utc).isoformat(), **extra})

    def _run(self) -> None:
        while True:
            inc = self._q.get()
            if inc is None:
                return
            now = self.clock()
            key = (inc.incident_type, inc.zone_id)
            if now < self._paused_until:
                self._record(inc, "skipped_paused_no_aws_sign_in"); continue
            last = self._last_by_key.get(key)
            if last is not None and now - last < self.cooldown:
                self._record(inc, "skipped_duplicate_cooldown"); continue
            while self._recent and now - self._recent[0] > 3600:
                self._recent.popleft()
            if len(self._recent) >= self.max_per_hour:
                self._record(inc, "skipped_hourly_cap"); continue
            self._last_by_key[key] = now
            self._recent.append(now)
            if self.wait_for_evidence is not None:
                try:
                    self.wait_for_evidence(inc.incident_id, self.evidence_wait)
                except Exception:
                    pass
            try:
                out = self.service.investigate(inc.incident_id, AUTO_QUESTION)
                v = out.get("verification") or {}
                self._record(inc, "completed", investigation_status=out.get("status"),
                             verified=v.get("passed"), cost_usd=out.get("cost_usd"))
            except BudgetExceededError as e:
                self._record(inc, "skipped_daily_budget", detail=str(e))
            except BedrockCredentialsError as e:
                self._paused_until = self.clock() + self.pause_s
                self._record(inc, "failed_no_aws_sign_in", detail=str(e)[:200])
                logger.warning("auto-investigation paused %.0fs: AWS sign-in needed", self.pause_s)
            except IncidentNotFoundError:
                self._record(inc, "failed_incident_missing")
            except BedrockError as e:
                self._record(inc, "failed_bedrock", detail=str(e)[:200])
            except Exception as e:
                logger.error("auto-investigation failed", exc_info=True)
                self._record(inc, "failed_error", detail=f"{type(e).__name__}: {e}"[:200])

    def status(self) -> Dict[str, Any]:
        now = self.clock()
        return {"min_severity": [k for k, v in SEVERITY_RANK.items() if v == self.min_rank][0],
                "cooldown_seconds": self.cooldown, "max_per_hour": self.max_per_hour,
                "queued": self._q.qsize(), "paused_seconds_left": max(0, round(self._paused_until - now)),
                "counts": dict(self.counts), "recent": list(self.history)[:20]}
