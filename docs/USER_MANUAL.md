# Office Vision Twin: user manual

From a fresh download to a building under watch. Each chapter says **who** it is for.

| # | Chapter | For |
|---|---|---|
| 1 | [Install](#1-install) | Administrator |
| 2 | [Sign in and read the screen](#2-sign-in-and-read-the-screen) | Operator |
| 3 | [Work an incident](#3-work-an-incident) | Operator |
| 4 | [Connect your cameras](#4-connect-your-cameras) | Administrator |
| 5 | [Model your building](#5-model-your-building) | Administrator |
| 6 | [Connect badge readers](#6-connect-badge-readers) | Integrator |
| 7 | [Turn on AI investigation](#7-turn-on-ai-investigation) | Administrator |
| 8 | [Run it in production](#8-run-it-in-production) | Administrator |
| 9 | [Troubleshooting](#9-troubleshooting) | Everyone |

```mermaid
flowchart LR
    A["1 · Install<br/>setup.py"] --> B["2 · Try it<br/>simulated scenarios"]
    B --> C["5 · Model your building<br/>rooms · doors · rules"]
    C --> D["4 · Connect cameras<br/>RTSP + calibration"]
    D --> E["6 · Badge readers<br/>access events API"]
    E --> F["7 · AI review<br/>optional"]
    F --> G["8 · Production<br/>TLS · proxy · service"]
```

---

## 1. Install

**You need:** Python 3.11 or newer, and Node.js 20 or newer. Windows 10/11, macOS or Linux; 8 GB RAM.
No GPU is needed (an Intel iGPU/NPU or an NVIDIA GPU is used if you configure it).

```bash
git clone https://github.com/dhruvindave007/office-security-digital-twin-poc.git
cd office-security-digital-twin-poc
python scripts/setup.py              # add --vision to enable camera detection
python scripts/start.py
```

`setup.py` does five things and stops with a clear message if any of them fails:

1. creates an isolated Python environment in `backend/.venv` (uses `uv` if installed, else `venv` + `pip`);
2. installs the backend;
3. builds the operator dashboard;
4. creates `backend/.env` from `backend/.env.example` with a **new random access token**, which it prints once;
5. checks that the backend starts.

Open **http://127.0.0.1:8000**. Run `setup.py` again at any time; an existing `backend/.env` is never overwritten.

> **Windows:** clone to a short path such as `C:\ovt`. Very deep folders exceed Windows' 260-character path limit;
> setup detects this and tells you.

## 2. Sign in and read the screen

<img src="assets/screenshot-signin.png" alt="Sign-in screen" width="560">

Enter the access token from your administrator. The session lasts 12 hours (`SESSION_HOURS`), is held in a
secure cookie, and ends early if the administrator rotates the token.

<img src="assets/screenshot-dashboard.png" alt="Operator dashboard">

```
┌──────────────────────────── top bar: site · cameras live · detections/s · people tracked · open incidents by severity ─┐
│ ┌─────────── camera wall ───────────┐ ┌──────── incident queue ────────┐                                                │
│ │ live pictures; each shows its age │ │ newest first, severity colour,  │                                                │
│ │ click to enlarge                  │ │ filters, bulk actions           │                                                │
│ ├─────────── floor plan ────────────┤ │                                 │                                                │
│ │ rooms with head-counts, people,   │ │ Enter opens the detail:         │                                                │
│ │ badge doors, heatmap (h)          │ │ evidence · events · AI review   │                                                │
│ └───────────────────────────────────┘ └─────────────────────────────────┘                                                │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Signal | Meaning |
|---|---|
| **LIVE** with a small number of seconds | Updates arriving; the number is the age of the last update |
| Yellow banner | Connection interrupted or more than 5 s without updates: the screen may be out of date |
| Camera tile **age** | How old that picture is; a frozen camera can never pass for a live one |
| Room number | Current head-count estimate for that room |

**Keyboard:** `j`/`k` move, `Enter` opens, `x` selects, `u` undoes the last status change, `h` toggles the heatmap, `Esc` closes.

**Practise without cameras:** choose a scenario under **Simulate scenario…** (top right) and press **Run**.
Simulated incidents are labelled as such.

## 3. Work an incident

```mermaid
stateDiagram-v2
    [*] --> OPEN: rule fires
    OPEN --> INVESTIGATING: operator takes it
    OPEN --> FALSE_POSITIVE
    INVESTIGATING --> RESOLVED: handled
    INVESTIGATING --> FALSE_POSITIVE: not a real event
    RESOLVED --> [*]
    FALSE_POSITIVE --> [*]
```

1. **Read the headline:** type, room, severity and the one-line reason (for example *"2 entries, 1 badge granted at door D4"*).
2. **Watch the evidence:** a clip from 12 s before to 8 s after, plus a key frame. *Jump to incident* lands on the moment.
3. **Check the linked events:** every detection, room change and badge swipe that produced the incident, in order.
4. **Read the AI review** (if enabled): a summary and a recommended action, in which every statement cites a record.
   *Verified* means a second check confirmed every citation. Treat an unverified review as a lead, not a fact.
5. **Decide:** *Investigating* → *Resolved*, or *False positive*. Use **Group repeats** and bulk actions for bursts.

| Severity | Typical incidents | Expected response |
|---|---|---|
| CRITICAL | Tailgating or unauthorized entry into a high-security room | Immediately |
| HIGH | Unauthorized entry, after-hours presence in secure areas | Within minutes |
| MEDIUM | Over capacity, long dwell, badge without entry | This shift |
| LOW | Tracking glitches | Review in bulk |

## 4. Connect your cameras

Install detection first: `python scripts/setup.py --vision`.

**4.1 Describe the cameras** in a file such as `data/sites/<your-site>/live.yaml`:

```yaml
process_fps: 5                 # detections per second per camera
track_exit_seconds: 2.0        # unseen this long = left the view
calibration_path: calibration.json

cameras:
  - camera_id: cam_lobby
    url: "rtsp://viewer:${CAMERA_PASSWORD}@10.20.0.11:554/Streaming/Channels/102"   # the sub-stream is enough
    feed_twin: true            # counts people for the room; use false for a second camera on the same room
    device: cpu                # cpu | intel:gpu | intel:npu | cuda:0
    model: yolo11n.pt
```

Use a **read-only camera account**, and keep its password out of the file: `${NAME}` in a `url` is replaced by the
environment variable `NAME` (put `CAMERA_PASSWORD=...` in `backend/.env` or the service environment); a missing variable
stops start-up with a clear message. `url` may also be an HTTP stream or a video file (for testing).

**4.2 Register each camera** in the building model (`floorplan_seed.yaml`, see chapter 5) with the room it covers:

```yaml
cameras:
- camera_id: cam_lobby
  name: Lobby entrance
  primary_zone_id: zone_lobby
  status: active
```

**4.3 Choose how each camera positions people** in `calibration.json`:

| Mode | When | What you provide |
|---|---|---|
| `room` | Cluttered rooms, wide angles, quick start | `{"mode": "room", "zone_id": "zone_lobby"}`: anyone seen counts as being in that room |
| `metric` | Corridors, entrances, open floors | `{"mode": "metric", "image_size": [w, h], "pixel_to_floor_H": 3×3 matrix}` from ≥ 4 points on the floor whose positions you know in metres (`cv2.findHomography`) |

`data/sites/wisenet/calibration.json` is a complete worked example, including the points used.

**4.4 Point the server at it** in `backend/.env`:

```ini
LIVE_CONFIG_PATH=../data/sites/<your-site>/live.yaml
LIVE_AUTOSTART=true
```

Restart. The top bar shows `n/n cameras live`; each tile shows its device, frame rate and latency.

## 5. Model your building

A site is a folder `data/sites/<name>/` with four small files. Point `backend/.env` at them.

```mermaid
flowchart TB
    S["floorplan_seed.yaml<br/>rooms (polygons in metres), security level,<br/>capacity, cameras"] --> T[("Digital twin")]
    P["access_policy.yaml<br/>restricted rooms + who may enter,<br/>business hours, correlation timing"] --> R["Rules"]
    D["doors.yaml<br/>doors, badge-required, which camera sees them"] --> R
    C["calibration.json<br/>pixel → floor per camera"] --> T
    T --> R
```

**Rooms** (`floorplan_seed.yaml`):

```yaml
facility: {name: "HQ floor 3", width_m: 40, height_m: 20, coordinate_system: metric_meters}
zones:
- zone_id: zone_server_room
  name: Server room
  zone_type: restricted
  security_level: 5            # 1 public … 5 most sensitive
  max_occupancy: 2
  geometry_version: 1
  vertices:                    # corners in metres, in order
  - {vertex_order: 0, x: 30.0, y: 2.0}
  - {vertex_order: 1, x: 38.0, y: 2.0}
  - {vertex_order: 2, x: 38.0, y: 9.0}
  - {vertex_order: 3, x: 30.0, y: 9.0}
```

**Rules** (`access_policy.yaml`):

```yaml
version: 1
restricted_zones:
  zone_server_room:
    min_security_level: 4
    authorized_person_ids: [person_it_lead, person_dc_tech]
business_hours_utc: {start: "07:00", end: "19:00"}
dwell_alert_min_security_level: 3
correlation:
  badge_entry_window_seconds: 15     # a swipe and an entry this close together belong together
  tailgating_window_seconds: 10
  transit_slack_seconds: 2
  grant_after_entry_tolerance_seconds: 5
```

**Doors** (`doors.yaml`):

```yaml
version: 1
doors:
- door_id: door_server
  name: Server room door
  zone_id: zone_server_room
  camera_id: cam_server
  badge_required: true
  connects: [zone_corridor, zone_server_room]
  jambs_m: [[30.0, 5.0], [30.0, 6.2]]
```

Then in `backend/.env`:

```ini
SITE_NAME="HQ · floor 3"
TWIN_FLOORPLAN_SEED_PATH=../data/sites/hq3/floorplan_seed.yaml
RULES_POLICY_PATH=../data/sites/hq3/access_policy.yaml
ACCESS_CONFIG_PATH=../data/sites/hq3/doors.yaml
DATABASE_URL=sqlite:///./data/hq3.db
```

Scenarios in `data/sites/<name>/scenarios/` let you rehearse your rules with no cameras (see `data/sites/wisenet/scenarios/`).

## 6. Connect badge readers

Your access-control system (or a small bridge) posts each swipe:

```bash
curl -X POST https://ovt.example.internal/api/access/events \
  -H "Authorization: Bearer $OVT_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"door_id":"door_server","badge_id":"badge_0042","result":"GRANTED","timestamp":"2026-10-27T18:02:11Z"}'
```

`result` is `GRANTED` or `DENIED`. Use the reader's own clock in UTC. The response lists any incidents the swipe raised.

```mermaid
sequenceDiagram
    participant R as Badge reader
    participant T as Twin
    participant C as Camera at door
    R->>T: GRANTED badge_0042 18:02:11
    C->>T: person 1 enters 18:02:12
    C->>T: person 2 enters 18:02:14
    Note over T: 2 entries, 1 grant within 10 s
    T-->>T: TAILGATING (CRITICAL) + evidence
```

## 7. Turn on AI investigation

Optional. Uses Amazon Bedrock in **your** AWS account; video never leaves the site, only the incident's text records.

1. Make AWS credentials available through the standard chain: an EC2/ECS instance role, `aws sso login`, or
   `scripts/aws_sso_login.py` (set `AWS_SSO_START_URL` to your portal). **Never put keys in `.env`.**
2. Enable model access for Claude Haiku 5.5 in the Bedrock console (or set `BEDROCK_INVESTIGATION_MODEL_ID`).
3. In `backend/.env`: `INVESTIGATION_DAILY_BUDGET_USD=1.0` (hard cap) and, for automatic review of serious incidents,
   `AUTO_INVESTIGATE_ENABLED=true`.

Operators can also press **Investigate** on any incident and ask a question. Typical cost is about $0.01.

## 8. Run it in production

Follow the **[deployment checklist in SECURITY.md](../SECURITY.md#deployment-checklist)**. The essentials:

- Keep `HOST=127.0.0.1` and publish through a TLS reverse proxy; set `COOKIE_SECURE=true`.
- Run `python scripts/start.py` as a service under a dedicated account (Windows: NSSM or Task Scheduler "at startup";
  Linux: a systemd unit with `Restart=always`).
- Back up `backend/data/` (ledger database and evidence) and encrypt the disk it lives on.

Minimal nginx in front:

```nginx
server {
    listen 443 ssl;
    server_name ovt.example.internal;
    ssl_certificate     /etc/ssl/ovt.crt;
    ssl_certificate_key /etc/ssl/ovt.key;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;              # live push channel (server-sent events)
        proxy_read_timeout 1h;
    }
}
```

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `This server only accepts local connections` | No token configured and you connected from another machine. Set `OVT_API_TOKEN` (setup generates one). |
| `Too many failed attempts` | 10 wrong tokens in 5 minutes from your address. Wait 5 minutes. |
| Signed out unexpectedly | Session expired (`SESSION_HOURS`) or the token was rotated. |
| Camera shows `reconnecting` | Stream unreachable. Check the URL, account and network; it retries by itself. |
| `0/3 cameras live`, *camera not running* | No `LIVE_CONFIG_PATH`, or vision not installed: `setup.py --vision`. |
| Evidence is a large `.avi` | Install `ffmpeg` and put it on `PATH` (used for H.264 clips). |
| AI review says *paused: AWS sign-in expired* | Refresh credentials; reviews resume by themselves. |
| `circular import` errors on Windows | Path too long: re-clone to a short folder such as `C:\ovt`. |
| Anything else | Server log in the console running `start.py`; `GET /api/health`; `GET /api/live/status` (with token). |
