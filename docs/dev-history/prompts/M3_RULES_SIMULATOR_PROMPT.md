# Milestone M3 — Deterministic Incident Rules + Scenario Simulator + Ingest API

You are the IMPLEMENTER for this repository. Read `GEMINI.md`, `AGENTS.md`, `.agents/rules/00-project-constitution.md`, and `.agents/skills/digital-twin/SKILL.md` and `.agents/skills/verification/SKILL.md` before writing code.

This prompt IS the approved plan. Do not redesign it. Do not add features not listed. If something in this prompt is impossible or contradicts the code, STOP and report the exact contradiction instead of improvising.

## Why this milestone

Current verified state (93 unit tests passing):
- Bedrock client (Converse/ConverseStream), model registry, telemetry: DONE.
- Digital Twin engine, SQLite ledger, snapshots, replay, `/api/twin/*` read endpoints: DONE.
- Vision geometry (homography, zone resolver, topology, camera graph, `ObservationPipeline`): DONE but NOT wired into `main.py` or any API.
- `src/rules/` and `src/simulator/` are empty placeholders.
- NOTHING ever creates an `Incident`. The `incidents` table is always empty. Phase 4 (Bedrock investigation) cannot start until incidents exist with supporting event IDs.

This milestone makes the system produce real, deterministic, evidence-linked incidents from simulated camera observations. NO Bedrock, NO frontend, NO YOLO/RTSP in this milestone.

---

## Step 0 — Fix contradictory project config (do this first, exact edits only)

1. `GEMINI.md`: replace the section
   ```
   ## Current Milestone

   Architecture Freeze.

   Do not implement new features until instructed.
   ```
   with
   ```
   ## Current Milestone

   M3 — Deterministic Incident Rules + Scenario Simulator + Ingest API.
   Spec: docs/prompts/M3_RULES_SIMULATOR_PROMPT.md
   ```
2. `.agents/skills/architecture-guard/SKILL.md`: replace the whole "## Project-specific guardrail" paragraph with:
   ```
   ## Project-specific guardrail

   The Python FastAPI backend under `backend/src/` is the single source of truth. There is no C# codebase. Do not introduce a second backend language or framework.
   ```
Change nothing else in those files.

---

## Step 1 — Access policy data file

Create `data/rules/access_policy.yaml`:

```yaml
version: 1
# Synthetic POC identities only. No biometrics.
restricted_zones:
  zone_server_room:
    min_security_level: 4
    authorized_person_ids: ["person_it_admin_01", "person_it_admin_02"]
business_hours_utc:
  start: "08:00"
  end: "19:00"
dwell_alert_min_security_level: 3
```

Add to `backend/src/config.py` Settings (follow existing style exactly): `rules_policy_path: str = "data/rules/access_policy.yaml"`, `simulator_scenarios_dir: str = "data/scenarios"`, `twin_prune_interval_seconds: float = 5.0`. Add matching lines to `backend/.env.example`.

---

## Step 1b — Schema migration v2 (REQUIRED, the DB enforces incident types)

`backend/src/twin/migrations.py` v1 creates `incidents` with `CHECK(incident_type IN ('UNAUTHORIZED_ENTRY','TAILGATING','DWELL_ANOMALY','AFTER_HOURS_BREACH','TRACK_DISCONTINUITY'))`. `ZONE_OVER_CAPACITY` is not allowed, so inserting it fails. SQLite cannot ALTER a CHECK constraint.

Append a NEW entry `(2, "Allow ZONE_OVER_CAPACITY incident type", sql)` to `MIGRATIONS`. Never edit migration v1. The SQL must:
1. `CREATE TABLE incidents_v2 (...)`, an exact copy of the v1 `incidents` definition but with `'ZONE_OVER_CAPACITY'` added to the CHECK list.
2. `INSERT INTO incidents_v2 SELECT * FROM incidents;`
3. `DROP TABLE incidents;` then `ALTER TABLE incidents_v2 RENAME TO incidents;`
4. Recreate the three `idx_incidents_*` indexes exactly as in v1.

Read `run_migrations` first and make sure v2 runs inside the same transaction/rollback pattern it already uses. Add tests to `tests/unit/test_twin_migrations.py`:
- a fresh DB ends at version 2;
- a v1-only DB with one existing incident upgrades to v2 and keeps that row;
- inserting `ZONE_OVER_CAPACITY` succeeds after the upgrade;
- inserting `NOT_A_TYPE` still fails.

If existing tests assert "latest version == 1", update ONLY that assertion to 2 and mention it in the report.

---

## Step 2 — Rules engine (`backend/src/rules/`)

Create:
- `backend/src/rules/policy.py` — `AccessPolicy` dataclass + `load_access_policy(path) -> AccessPolicy`. Resolve relative paths the same way `vision/seed.py::_resolve_data_path` does (reuse it by importing; do not copy-paste). Validate: unknown keys ignored, missing file raises `FileNotFoundError` with the path in the message, `HH:MM` parse errors raise `ValueError`.
- `backend/src/rules/engine.py` — `IncidentRuleEngine`.

`IncidentRuleEngine.__init__(self, store: TwinStore, state: TwinState, policy: AccessPolicy)`
`IncidentRuleEngine.evaluate(self, events: List[TwinEvent]) -> List[Incident]` — returns ONLY incidents created or changed in this call, and persists each via `store.insert_incident` (it is INSERT OR REPLACE, so updates use it too).

Use the same import style as other modules (`try: from twin... except ImportError: from src.twin...`). Use `generate_uuid7()` from `twin/engine.py` for incident IDs.

Rules (exactly these four, nothing more):

| incident_type | Trigger | Severity | Dedupe key (while status OPEN) | Auto-resolve |
|---|---|---|---|---|
| `UNAUTHORIZED_ENTRY` | `ZONE_ENTERED` into a zone listed in `restricted_zones` AND (`person_id` is None OR not in `authorized_person_ids`) | `HIGH` | (type, zone_id, track_id) | No |
| `ZONE_OVER_CAPACITY` | After any `ZONE_ENTERED`, `state.get_zone_occupancy(zone_id) > zone.max_occupancy` (zone from `store.get_zone`) | `MEDIUM` | (type, zone_id) | Yes: on any `ZONE_EXITED`/`TRACK_LOST` for that zone where occupancy is now `<= max_occupancy`, set `status="RESOLVED"`, `end_time=event.timestamp`, `updated_at=event.timestamp`, append that event_id to supporting events |
| `DWELL_ANOMALY` | `DWELL_TIME_EXCEEDED` in a zone whose `security_level >= dwell_alert_min_security_level` | `MEDIUM` | (type, zone_id, track_id) | No |
| `AFTER_HOURS_BREACH` | `PERSON_DETECTED` whose timestamp (UTC) time-of-day is outside `business_hours_utc` | `LOW` | (type, track_id) | No |

Hard requirements:
- `supporting_event_ids_json` = JSON list of the triggering event IDs. Never empty.
- When a deduped incident already exists, do NOT create a new one; append the new event_id to its supporting list (no duplicates), update `updated_at`, persist, and include it in the return list.
- Dedupe must survive restart: on `__init__`, load OPEN incidents from `store.get_incidents(status="OPEN", limit=500)` into an in-memory dict keyed by dedupe key.
- `summary` is a deterministic template string, e.g. `"Track trk_x (person: unknown) entered restricted zone zone_server_room."` NO LLM. NEVER words like "suspicious", "malicious", "intruder", "threat" — use neutral factual language (project constitution).
- `start_time` = triggering event timestamp (not wall clock). `created_at`/`updated_at` = event timestamp too, so tests are deterministic.
- Timestamps: treat naive ISO timestamps as UTC. Use `datetime.fromisoformat(ts.replace("Z", "+00:00"))`, then `.replace(tzinfo=timezone.utc)` if `tzinfo is None`.
- The rule engine MUST NOT be invoked from `DigitalTwinEngine.replay_events` (replay must never create incidents). Do not modify `twin/engine.py` for rules.

Add store methods to `backend/src/twin/store.py` (additive only):
- `get_incident(self, incident_id: str) -> Optional[Incident]`
- reuse existing row-mapping code in `get_incidents`; do not duplicate the mapping — extract a private `_row_to_incident` helper if needed.

---

## Step 3 — Wire rules into the vision pipeline (additive)

`backend/src/vision/geometry.py`: add field to `TwinObservation`: `raised_incidents: List[Incident] = field(default_factory=list)` (last field, default so existing constructors keep working).

`backend/src/vision/pipeline.py`: add optional constructor arg `rule_engine: Optional[IncidentRuleEngine] = None`. After `twin_engine.ingest_observation(...)`, if `rule_engine` is set: `incidents = rule_engine.evaluate(emitted_events)` and set it on the returned `TwinObservation`. Update the class docstring's "Prohibitions" line to allow rule evaluation. Nothing else changes.

Pruning: `DigitalTwinEngine.prune_tracks()` exists but is never called. Add a helper in `backend/src/rules/engine.py` is NOT the place — instead in `main.py` lifespan, start an `asyncio` background task that every `twin_prune_interval_seconds` calls `lost = twin_engine.prune_tracks()` then `rule_engine.evaluate(lost)` (so over-capacity can auto-resolve). Cancel and await it on shutdown (catch `asyncio.CancelledError`).

---

## Step 4 — Scenario simulator (`backend/src/simulator/`)

Scenario YAML format (`data/scenarios/<name>.yaml`):

```yaml
name: server_room_unauthorized_entry
description: "Unknown visitor walks reception -> workspace -> server room and stays."
base_time_utc: "2026-01-15T10:00:00Z"
steps:
  - t: 0.0            # seconds offset from base_time_utc
    camera_id: cam_reception_01
    track_id: sim_trk_001
    person_id: null
    floor: [5.0, 5.0] # floor meters (x, y)
    confidence: 0.92
```

Create:
- `backend/src/simulator/scenarios.py` — `ScenarioStep`, `Scenario` dataclasses; `load_scenario(path)`, `list_scenarios(dir) -> List[Scenario]`. Validate: steps sorted by `t` (raise `ValueError` if not), camera_id non-empty, floor has 2 numbers.
- `backend/src/simulator/runner.py` — `ScenarioRunner(pipeline: ObservationPipeline, homography_manager: HomographyManager)` with `run(scenario) -> ScenarioRunResult`. For each step: convert `floor` -> pixel using `homography_manager.project_floor_to_pixel(camera_id, FloorPoint(x, y))` (read its real signature first), build `CameraObservation(bottom_center=pixel, timestamp=base_time + t, ...)`, call `pipeline.process_observation`. Runs synchronously as fast as possible — NO `sleep`. `ScenarioRunResult` contains: `scenario_name`, `steps_processed`, `events_emitted` (int), `event_type_counts` (dict), `incidents` (list of incident_ids, unique, in creation order), `errors` (list of str — a step that raises is recorded and the run continues).

Create exactly four scenarios. Choose floor coordinates from `backend/src/twin/floorplan_seed.yaml` zone polygons (read the whole file; reception is x 0–12, y 0–10; workspace x 12–30, y 0–18; server room starts at x 30 — confirm the server-room polygon yourself). Each coordinate must lie strictly inside the zone AND be visible to the chosen camera (the camera whose `primary_zone_id` matches). Base times must be inside business hours except where noted.

1. `normal_workday.yaml` — 3 tracks; `person_it_admin_01` enters server room (authorized). Expected incidents: NONE.
2. `server_room_unauthorized_entry.yaml` — `sim_trk_001`, `person_id: null`, reception -> workspace -> server room, then stays in server room with observations every 30 s for 330 s (exceeds the engine's default 300 s dwell threshold). Expected: exactly 1 `UNAUTHORIZED_ENTRY` and exactly 1 `DWELL_ANOMALY` (deduped, multiple supporting events).
3. `reception_over_capacity.yaml` — 16 distinct tracks enter reception (max_occupancy 15). Expected: exactly 1 `ZONE_OVER_CAPACITY` and zero other incidents.
4. `after_hours_presence.yaml` — `base_time_utc: "2026-01-15T20:30:00Z"`, one track `sim_trk_night_01` with 3 observations in the open workspace. Expected: exactly 1 `AFTER_HOURS_BREACH` (deduped, 3 supporting events) and zero other incidents.

If any expected count does not match when you run it, fix the SCENARIO DATA, not the rules, unless you can show the rule is wrong against the table in Step 2.

---

## Step 5 — API

New file `backend/src/api/routes_vision.py` (`prefix="/api/vision"`):
- `POST /api/vision/observations` — body: `{camera_id, track_id, bottom_center: {u, v} | null, bbox: {...} | null, confidence, timestamp, person_id}`. Map to `CameraObservation`, run `app.state.observation_pipeline.process_observation`. Return `{track_id, camera_id, zone_id, floor_point: {x, y}, emitted_event_ids: [...], raised_incident_ids: [...]}`. Validation errors -> 422; unknown camera -> 404; pipeline not initialized -> 503.

New file `backend/src/api/routes_simulator.py` (`prefix="/api/simulator"`):
- `GET /api/simulator/scenarios` -> `[{name, description, step_count}]`
- `POST /api/simulator/scenarios/{name}/run` -> `ScenarioRunResult` as JSON. Unknown name -> 404.

Extend `backend/src/api/routes_twin.py` (additive):
- `GET /api/twin/incidents/{incident_id}` -> `IncidentResponse`, 404 if missing.
- `PATCH /api/twin/incidents/{incident_id}` body `{status}`; allowed values `OPEN, INVESTIGATING, RESOLVED, FALSE_POSITIVE`; invalid -> 422. Set `updated_at` to now UTC; if `RESOLVED`/`FALSE_POSITIVE` and `end_time` is None set `end_time`. Persist. Also remove from the rule engine's open-dedupe dict (add a public method `IncidentRuleEngine.forget(incident_id)`).

Bug fix in `get_twin_state` (`routes_twin.py`): `datetime.utcnow()` is naive and `.astimezone()` interprets it as LOCAL time, so `dwell_seconds` is wrong by the machine's UTC offset. Replace with `datetime.now(timezone.utc)` and parse `dwell_start` treating naive as UTC (same helper rule as Step 2). Add a regression test.

Pydantic request/response models go in `backend/src/twin/schemas.py` (twin ones) or at the top of the new route files (vision/simulator ones). Follow the existing import style of `routes_twin.py`.

`backend/src/main.py` lifespan, after twin bootstrap:
```
homography_mgr, camera_graph, topology = VisionSeedLoader.load_all(settings.vision_calibration_dir, settings.vision_topology_dir)
zone_resolver = VisionZoneResolver.from_twin_store(twin_engine.store)
policy = load_access_policy(settings.rules_policy_path)
rule_engine = IncidentRuleEngine(twin_engine.store, twin_engine.state, policy)
app.state.homography_manager / rule_engine / observation_pipeline / scenario_runner = ...
```
plus the prune task from Step 3. Include the two new routers. If vision seed loading fails, log the error and leave `observation_pipeline = None` (endpoints return 503) — do not crash startup.

Add `"numpy>=1.26"` to `backend/pyproject.toml` dependencies (it is imported by `vision/calibration.py` but not declared).

---

## Step 6 — Tests (all must use `tmp_path` SQLite DBs, never `data/digital_twin.db`)

Create:
- `tests/unit/test_rules_policy.py` — load valid, missing file, bad HH:MM.
- `tests/unit/test_rules_engine.py` — one test per rule (positive + negative), dedupe appends supporting event, dedupe survives re-instantiation from store, over-capacity auto-resolves, authorized person creates nothing, replay_events creates zero incidents, summaries contain none of: suspicious/malicious/intruder/threat.
- `tests/unit/test_simulator.py` — scenario loader validation; each shipped scenario produces exactly the expected incident types/counts from Step 4; running same scenario twice on fresh DBs yields identical `event_type_counts` (determinism).
- `tests/unit/test_api_vision_simulator.py` — FastAPI `TestClient` against an app whose settings point at `tmp_path` DB: list scenarios, run scenario, 404 unknown, POST observation happy path / unknown camera 404, GET/PATCH incident incl. invalid status 422.
- Regression test for the dwell_seconds UTC bug.

Look at `tests/conftest.py` and `tests/unit/test_twin_api.py` FIRST and reuse their fixtures/patterns for building an app with a temp DB.

---

## Step 7 — Verification (mandatory, run from `backend/`, PowerShell)

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests/unit -q
```
Expected: previous 93 tests still pass + all new tests pass, zero failures. Do not delete, skip, or weaken existing tests to make them pass.

Live smoke (use a throwaway DB so the demo DB is untouched):
```powershell
$env:DATABASE_URL="sqlite:///./data/m3_smoke.db"
.\.venv\Scripts\python.exe -m uvicorn src.main:app --port 8000
# second terminal:
curl.exe http://localhost:8000/api/simulator/scenarios
curl.exe -X POST http://localhost:8000/api/simulator/scenarios/server_room_unauthorized_entry/run
curl.exe "http://localhost:8000/api/twin/incidents?status=OPEN"
```
Paste the real outputs into the report. Delete `m3_smoke.db` afterwards.

## Out of scope (do NOT do)
Bedrock calls or prompts; frontend changes; YOLO/OpenCV/RTSP; WebSockets; auth; Docker; changing `DigitalTwinEngine` event semantics; renaming any existing public function, route, or field.

## Final report format (required)
1. Files created / modified (full list) and why each.
2. Exact test command + real pass/fail counts.
3. Real smoke-test outputs.
4. Any deviation from this spec and the reason.
5. Remaining limitations / open questions.
