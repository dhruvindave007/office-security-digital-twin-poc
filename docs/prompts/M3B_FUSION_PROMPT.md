# Milestone M3b: Fusion (Access Control + Cross-Camera Correlation + History APIs)

You are the IMPLEMENTER. Before writing code, read `GEMINI.md`, `AGENTS.md`, `.agents/rules/00-project-constitution.md`, `.agents/skills/digital-twin/SKILL.md` and `.agents/skills/verification/SKILL.md`.

This prompt IS the approved plan. Do not redesign it, and do not add features that are not listed. If anything here contradicts the code, STOP and report the exact contradiction (file, line, what the prompt says, what the code says). Do not improvise.

## Precondition: check before anything else

M3 (`docs/prompts/M3_RULES_SIMULATOR_PROMPT.md`) must already be implemented and reviewed. Verify all of the following, and STOP if any one fails:
- `backend/src/rules/engine.py` exists and defines `IncidentRuleEngine`.
- `backend/src/simulator/runner.py` exists and defines `ScenarioRunner`.
- `backend/src/twin/migrations.py` has migration version 2.
- From `backend/`, `.\.venv\Scripts\python.exe -m pytest tests/unit -q` passes with zero failures. Record the exact count; this is your baseline.

Then read these files in full: `rules/engine.py`, `rules/policy.py`, `simulator/scenarios.py`, `simulator/runner.py`, `vision/topology.py`, `twin/store.py`, `twin/migrations.py`, `twin/validators.py`, `api/routes_twin.py`, `main.py`. Everything below builds on them.

## Why this milestone

The product is a surveillance fusion system: it understands situations by correlating observations across cameras, access control and time. After M3, every rule looks at a single camera event. This milestone adds:
1. Access-control (badge/door) events as a second input source, stored in the same event ledger.
2. Correlation rules that combine badge events, camera tracks and camera topology.
3. History APIs (track paths, occupancy over time, a position heatmap) for the dashboard.

Everything stays deterministic. NO Bedrock, NO frontend, NO real video, NO wall-clock time in rule decisions: use event timestamps only.

---

## Step 1: Schema migration v3

The DB enforces event and incident types with CHECK constraints, which SQLite cannot ALTER. Append migration `(3, "Access-control events and correlation incident types", sql)` to `MIGRATIONS`. Never edit v1 or v2. Follow exactly the rebuild pattern that v2 uses for `incidents`.

1. Rebuild `events`. Take the current `events` definition and add `'ACCESS_GRANTED'` and `'ACCESS_DENIED'` to its `event_type` CHECK list. Steps: `CREATE TABLE events_v3 (...)`, then `INSERT INTO events_v3 SELECT * FROM events;`, then `DROP TABLE events;`, then `ALTER TABLE events_v3 RENAME TO events;`. Then recreate all four `idx_events_*` indexes exactly as in v1. `sequence_num` values MUST be preserved (snapshots and replay depend on them).
2. Rebuild `incidents`. Take the current v2 definition and add `'ACCESS_WITHOUT_BADGE'` and `'BADGE_WITHOUT_ENTRY'` to the CHECK list, then recreate its indexes. `TAILGATING` and `TRACK_DISCONTINUITY` already exist; reuse them.
3. Add `"ACCESS_GRANTED"` and `"ACCESS_DENIED"` to `EventValidator.ALLOWED_EVENT_TYPES` in `backend/src/twin/validators.py`.

Add these tests to `tests/unit/test_twin_migrations.py`:
- A fresh DB ends at version 3.
- A v2 DB containing events with sequence numbers 1..5 plus one incident upgrades to v3, keeps every row and the same `sequence_num` values, and the next appended event gets `sequence_num` 6.
- Inserting events/incidents of the new types succeeds after the upgrade, and an invalid type still fails.

`DigitalTwinEngine.replay_events` already ignores unknown event types (it only increments a counter). Do NOT modify `twin/engine.py`. Add a test proving that replaying a ledger that contains `ACCESS_GRANTED` events leaves TwinState unchanged for tracks named `badge:*`.

---

## Step 2: Access-control configuration and ingestion

Create `data/access/doors.yaml`:

```yaml
version: 1
doors:
  - door_id: door_server_01
    name: "Server Room Badge Door"
    zone_id: zone_server_room        # zone you are in after passing the door
    camera_id: cam_server_01         # camera that sees people entering
    badge_required: true
  - door_id: door_main_entrance
    name: "Main Entrance"
    zone_id: zone_reception
    camera_id: cam_reception_01
    badge_required: false
badges:                              # synthetic POC identities only
  badge_0001: person_it_admin_01
  badge_0002: person_it_admin_02
  badge_0100: person_staff_01
```

Append to `data/rules/access_policy.yaml`. Keep the existing keys; this is additive:

```yaml
correlation:
  badge_entry_window_seconds: 15.0   # a GRANTED swipe admits one entry within this many seconds after the swipe
  tailgating_window_seconds: 10.0    # a second entry within this window of an already-used swipe = tailgating
  transit_slack_seconds: 2.0
```

Update the `AccessPolicy` dataclass and loader. If the `correlation` section is missing, use exactly these defaults (old policy files must keep loading).

Add `access_config_path: str = "data/access/doors.yaml"` to `backend/src/config.py` and to `backend/.env.example`.

Create `backend/src/access/__init__.py`, `backend/src/access/config.py` and `backend/src/access/service.py`:
- `config.py` defines dataclasses `Door` and `AccessConfig(doors: Dict[str, Door], badges: Dict[str, str])`, plus `load_access_config(path)`. Resolve paths the same way `rules/policy.py` does. Validate as follows:
  - Duplicate door_id: raise `ValueError`.
  - Each `zone_id`/`camera_id` must exist in the TwinStore. Do this check in the service constructor, not in the loader; raise `ValueError` that names the bad id.
- `service.py` defines `AccessControlService(store: TwinStore, config: AccessConfig, rule_engine: Optional[IncidentRuleEngine])` with `ingest(door_id: str, badge_id: str, result: str, timestamp: str) -> Tuple[TwinEvent, List[Incident]]`. It behaves as follows:
  - `result` must be `GRANTED` or `DENIED`, otherwise `ValueError`. An unknown `door_id` raises `KeyError`.
  - It builds a `TwinEvent` with:
    - `event_type`: `ACCESS_GRANTED` or `ACCESS_DENIED`.
    - `track_id`: `f"badge:{badge_id}"`.
    - `person_id`: `config.badges.get(badge_id)`. An unknown badge gives `None`; that is allowed.
    - `zone_id` and `camera_id`: taken from the door.
    - `payload_json`: `{"door_id", "badge_id", "result"}`.
    - `event_id`: from `generate_uuid7()`.
    - `confidence`: 1.0.
    - Timestamps are normalized to UTC as in M3.
  - It appends the event via `store.append_event(evt)` and sets `evt.sequence_num`.
  - It does NOT call `DigitalTwinEngine.ingest_observation`. Badge events are not camera tracks.
  - If `rule_engine` is set, it returns `rule_engine.evaluate([evt])` as the incidents.

---

## Step 3: Correlation rules

Create `backend/src/rules/correlation.py` with class `CorrelationEngine(policy: AccessPolicy, access_config: AccessConfig, topology: TopologyService, store: TwinStore)`.

**Integration.** Add an optional constructor arg `correlation: Optional[CorrelationEngine] = None` to `IncidentRuleEngine`. Inside `IncidentRuleEngine.evaluate`, after the M3 rules, call the correlation engine for the same events. Every incident it raises must go through the SAME create-or-append-with-dedupe logic that M3 rules use. Do not write a second persistence path. If M3 inlined that logic, first extract it into one private helper, `_raise_or_append(incident_type, severity, dedupe_key, event, zone_id, track_id, person_id, summary)`, and make the existing rules use it. Re-run the tests after this refactor, BEFORE adding the new rules.

`CorrelationEngine.evaluate(events) -> List[IncidentCandidate]` is a pure decision function. `IncidentCandidate` is a small dataclass containing the `_raise_or_append` arguments plus `extra_event_ids: List[str]` (other evidence to attach). `IncidentRuleEngine` does all persistence.

**In-memory state** (kept inside CorrelationEngine, keyed by event time and never wall clock):
- `pending_grants[door_id]`: a list of `{event_id, timestamp, person_id, used_by_track: Optional[str], used_at: Optional[ts]}`.
- `last_seen[track_id]`: `(camera_id, timestamp, event_id)`, updated on `PERSON_DETECTED`.

Process events in the order given. Use these exact rules:

| incident_type | Severity | Trigger and logic | Dedupe key |
|---|---|---|---|
| (none, bookkeeping only) | n/a | `ACCESS_GRANTED` at a door with `badge_required: true`: append it to `pending_grants[door]`. `ACCESS_DENIED` is logged in the ledger only. | n/a |
| `ACCESS_WITHOUT_BADGE` | MEDIUM | `ZONE_ENTERED` into a zone that has a `badge_required` door (look up the door by `zone_id`). Search that door's grants for the EARLIEST one with `used_by_track is None` and `0 <= entry_ts - grant_ts <= badge_entry_window_seconds`. If found, mark it used by this track and raise nothing. If none is found, AND no `TAILGATING` applies (next row), raise this incident. Evidence: the entry event. | (type, zone_id, track_id) |
| `TAILGATING` | HIGH | Same `ZONE_ENTERED` case where no unused grant was found, BUT a grant at that door was already used by a DIFFERENT track and `0 <= entry_ts - grant.used_at <= tailgating_window_seconds`. Raise TAILGATING instead of ACCESS_WITHOUT_BADGE. Evidence: entry event + grant event_id (`extra_event_ids`). | (type, zone_id, track_id) |
| `BADGE_WITHOUT_ENTRY` | LOW | On EVERY event processed, first sweep all doors. Any grant with `used_by_track is None` and `current_event_ts - grant_ts > badge_entry_window_seconds` gets removed and raises this incident, with `track_id = f"badge:{badge_id}"` (from the grant event) and the grant event as evidence. Note: the sweep only runs when a later event arrives. That is intended (event-time determinism); state it as a known limitation in the report. | (type, track_id, grant event_id) |
| `TRACK_DISCONTINUITY` | LOW | `PERSON_DETECTED` for a track whose `last_seen` camera differs from this event's camera. Let `elapsed = ts - last_seen_ts` and `min_s = topology.min_transit_seconds(prev_cam, cam)`. Raise if `min_s is None` (unreachable) OR `elapsed < min_s - transit_slack_seconds`. Evidence: the previous and current detection event ids. Do NOT raise when elapsed is too LONG (people stop and wait). | (type, track_id) |

Add an additive method to `backend/src/vision/topology.py`: `min_transit_seconds(self, from_camera_id, to_camera_id) -> Optional[float]`. It returns 0.0 for the same camera, the sum of `min_transit_seconds` along `get_shortest_path(..., weight="min_transit")`, and None if unreachable. Do NOT change `is_transit_feasible`. Add tests for it.

**Summaries** are deterministic templates in neutral language. Examples:
- `"Track sim_trk_002 entered zone_server_room 3.0 s after badge badge_0001 admitted track sim_trk_001; no separate badge event matched."`
- `"Track sim_trk_009 was observed on cam_server_01 1.0 s after cam_reception_01; topology minimum is 9.0 s. Possible tracking or ID-association error."`

Forbidden words in any summary: suspicious, malicious, intruder, threat, attacker. TRACK_DISCONTINUITY summaries MUST describe a possible tracking error, never a person's behavior.

**Restart and replay.** CorrelationEngine state is in-memory only. It is NOT rebuilt on restart and NEVER fed by `replay_events`. Write this in the class docstring and in the report as a known limitation.

**Wiring** in `main.py`: build `AccessConfig` and `CorrelationEngine` (`topology` comes from `VisionSeedLoader.load_all`), pass the correlation engine into `IncidentRuleEngine`, create `AccessControlService`, and store it in `app.state.access_service`. If vision or topology loading failed, then create `IncidentRuleEngine` with `correlation=None`, log a warning, and keep startup alive.

---

## Step 4: History functions and APIs

Create `backend/src/twin/history.py` with pure functions. They take no store access and are easy to test.
- `track_path(events) -> List[dict]`. Input is a track's `PERSON_DETECTED` events. Output is sorted ascending by `sequence_num`, one dict per point: `{event_id, timestamp, x, y, zone_id, camera_id}`. Events without coordinates are skipped.
- `occupancy_series(events, start, end, bucket_seconds) -> List[{bucket_start, occupancy: Dict[zone_id, int]}]`. Input is ALL ledger events from the beginning up to `end`, sorted ascending. Walk them while maintaining `track -> zone`:
  - `ZONE_ENTERED` and `CROSS_ZONE_TRANSITION` set the zone.
  - `ZONE_EXITED` and `TRACK_LOST` set None.
  - Ignore `badge:*` tracks and all other event types.

  Emit one sample at each bucket boundary from `start` (inclusive) to `end` (exclusive), counting the state after applying all events with `timestamp <= boundary`. Include every zone that appears, with 0 when a zone is empty.
- `position_heatmap(events, width_m, height_m, cell_size_m) -> {width_m, height_m, cell_size_m, cols, rows, cells: List[List[int]]}`. Input is `PERSON_DETECTED` events in the time range. Each point goes into cell `(int(x // cell), int(y // cell))`, clamped into the grid. `cells[row][col]`.

Add to `backend/src/api/routes_twin.py` (additive):
- `GET /api/twin/tracks/{track_id}/path?start_time&end_time`. A track with no points returns 404.
- `GET /api/twin/occupancy/history?start_time&end_time&bucket_seconds=60&zone_id=`. `start_time` and `end_time` are required. Return 422 if `end <= start`, if `bucket_seconds < 1`, or if the result would exceed 1440 buckets. If `zone_id` is given, filter the output to that zone.
- `GET /api/twin/heatmap?start_time&end_time&cell_size_m=1.0`. `cell_size_m` must be between 0.25 and 10, else 422. `width_m` = max zone `max_x` and `height_m` = max zone `max_y` from `store.get_zones()`.

Use `store.get_events(...)` / `store.get_events_since(0)`. Read both first, check their sort order, and sort explicitly by `sequence_num` in `history.py`. Do not rely on the store's order.

New file `backend/src/api/routes_access.py` (`prefix="/api/access"`):
- `GET /api/access/doors` returns a list of doors.
- `POST /api/access/events` takes body `{door_id, badge_id, result, timestamp}` and returns `{event_id, sequence_num, person_id, raised_incident_ids}`. Unknown door gives 404, bad result gives 422, and a missing service gives 503.

Register the router in `main.py`.

---

## Step 5: Simulator support and scenarios

Extend the scenario step format (additive). Each step gets `kind: observation | access`, defaulting to `observation` so old files still load. An access step has `t, door_id, badge_id, result` and no camera/floor fields. Validate that each kind has its required fields, otherwise `ValueError`. `ScenarioRunner` gets an optional `access_service` constructor arg. Access steps call `access_service.ingest(...)`; if `access_service` is None, they record an error in `errors`. Add the new incident ids to the run result exactly as observation steps do.

Update existing M3 scenarios (the expected counts change because badge correlation now exists; this is correct, not a regression):
- `normal_workday.yaml`: add an access step `door_server_01 / badge_0001 / GRANTED` 3 s BEFORE `person_it_admin_01` first appears in the server room. Expected incidents: still NONE.
- `server_room_unauthorized_entry.yaml`: no data change. Expected incidents: `UNAUTHORIZED_ENTRY` 1, `DWELL_ANOMALY` 1, `ACCESS_WITHOUT_BADGE` 1 (new).
- `reception_over_capacity.yaml` and `after_hours_presence.yaml`: unchanged, and the expected counts stay unchanged (reception's door is not badge-required).

New scenarios in `data/scenarios/`, all at business-hours base times:
1. `tailgating.yaml`: `person_it_admin_01` badges `door_server_01` (GRANTED) at t=0, its track `sim_trk_admin` appears in the server room at t=2, and an unknown track `sim_trk_tail` (person_id null) appears in the server room at t=4. Expected: `TAILGATING` 1 (on sim_trk_tail), `UNAUTHORIZED_ENTRY` 1 (sim_trk_tail), and nothing else.
2. `badge_without_entry.yaml`: `badge_0002` GRANTED at `door_server_01` at t=0, with no one entering. At t=30, an unrelated workspace observation occurs (it triggers the sweep). Expected: `BADGE_WITHOUT_ENTRY` 1 and nothing else.
3. `impossible_transit.yaml`: track `sim_trk_jump` (person_id `person_staff_01`) is in reception on `cam_reception_01` at t=0, then in the open workspace on `cam_workspace_01` at t=1 (topology min is 5 s, slack 2). Expected: `TRACK_DISCONTINUITY` 1 and nothing else.

WARNING: In EVERY scenario (old and new, except `impossible_transit.yaml`), when the same track moves to a different camera, the time gap must be at least the topology `min_transit_seconds` for that camera pair (`data/topology/topology.yaml`: reception↔workspace min 5 s, workspace↔server min 4 s). Otherwise a `TRACK_DISCONTINUITY` will appear. Check the M3 scenarios for this.

Fix SCENARIO DATA if counts do not match. Change rule code only if you can show it contradicts the table in Step 3, and report it if you do.

---

## Step 6: Tests (tmp_path DBs only, never `data/digital_twin.db`)

- `tests/unit/test_access_config.py`: load valid, duplicate door, unknown zone/camera rejected by the service.
- `tests/unit/test_access_service.py`: GRANTED/DENIED stored with correct fields; unknown badge gives person_id None; bad result gives ValueError; TwinState unchanged after ingest.
- `tests/unit/test_correlation.py`: per rule, a positive case AND a negative case:
  - Badge, then entry within the window: no incident.
  - Entry at window+1 s: ACCESS_WITHOUT_BADGE.
  - Second track inside the tailgating window: TAILGATING, not ACCESS_WITHOUT_BADGE.
  - Second track outside the tailgating window: ACCESS_WITHOUT_BADGE.
  - Grant expiry sweep.
  - Transit: too fast raises, too slow does not, same camera does not, unreachable raises.
  - Dedupe appends evidence.
  - Forbidden-word check on all summaries.
- `tests/unit/test_twin_history.py`: path ordering, occupancy buckets at exact boundaries, `badge:*` ignored, heatmap cell placement and clamping.
- `tests/unit/test_simulator.py`: update expectations for the changed M3 scenarios and add the three new ones. Keep the determinism test (run twice on fresh DBs and get identical results).
- API tests for `/api/access/*` and the three history endpoints, including every 4xx case listed above.

---

## Step 7: Verification (mandatory, run from `backend/` in PowerShell)

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -q
```
Required result: baseline count + new tests, zero failures. Do not delete, skip or weaken any existing test. The ONLY allowed edits to existing assertions are:
- the M3 scenario expectation change listed in Step 5;
- the "latest migration version" number.

Live smoke test on a throwaway DB:
```powershell
$env:DATABASE_URL="sqlite:///./data/m3b_smoke.db"
.\.venv\Scripts\python.exe -m uvicorn src.main:app --port 8000
# second terminal:
curl.exe http://localhost:8000/api/access/doors
curl.exe -X POST http://localhost:8000/api/simulator/scenarios/tailgating/run
curl.exe "http://localhost:8000/api/twin/incidents?status=OPEN"
curl.exe "http://localhost:8000/api/twin/tracks/sim_trk_tail/path"
curl.exe "http://localhost:8000/api/twin/occupancy/history?start_time=2026-01-15T09:59:00Z&end_time=2026-01-15T10:10:00Z&bucket_seconds=60"
curl.exe "http://localhost:8000/api/twin/heatmap?start_time=2026-01-15T00:00:00Z&end_time=2026-01-16T00:00:00Z"
```
(Adjust the times to match your scenario's `base_time_utc`.) Paste the real outputs. Stop the server and delete `m3b_smoke.db`.

## Out of scope (do NOT do)
Bedrock; frontend; YOLO/OpenCV; ReID matching; WebSockets; auth; rebuilding correlation state on restart; changing `DigitalTwinEngine`; renaming any existing public function, route, field or file.

## Final report (required format)
1. Precondition check results (baseline test count).
2. Files created/modified, and why for each.
3. Exact test command and real pass/fail counts (baseline vs now).
4. Real smoke outputs.
5. Every deviation from this spec, with the reason.
6. Known limitations (at minimum: in-memory correlation state; the sweep needs a later event; badge events carry no position).
