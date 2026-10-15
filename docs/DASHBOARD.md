# Operator dashboard (M11)

A single-screen operations view of the live site: camera wall, floor plan with room counts, the incident
queue, and per-incident evidence, events and AI review. Everything on it comes from the running backend;
there is no sample data in the app.

## Run it

    backend\.venv\Scripts\python.exe scripts\demo.py --fresh        # then open http://127.0.0.1:8000/

`demo.py` rebuilds the dashboard when `frontend/dist` is missing or older than `frontend/src` (needs Node/npm;
`--no-build` serves the existing build), starts the WiseNET live site, and prints `READY` only after it has
verified the API, all cameras, a camera picture, the push channel and that the dashboard is served.

The backend serves the built dashboard itself (`frontend/dist` at `/`, setting `FRONTEND_DIST_DIR`), so the
browser talks to one origin. For frontend development with hot reload:

    cd frontend
    npm install
    npm run dev          # http://localhost:5173, proxies /api to the backend on :8000
    npm test             # 19 component tests (vitest + jsdom, fake API and fake push channel)
    npm run build        # typecheck + production build into frontend/dist

End-to-end checks against a running system (Playwright driving the installed Microsoft Edge; dev-only:
`uv pip install --python backend\.venv\Scripts\python.exe playwright`):

    backend\.venv\Scripts\python.exe scripts\e2e_dashboard.py [--ai]      # with demo.py running
    backend\.venv\Scripts\python.exe scripts\e2e_interruptions.py         # starts its own RTSP replay + backend on :8015

## Screen

| Area | Shows | Source |
|---|---|---|
| Top bar | push-channel health (LIVE / STALE / RECONNECTING / OFFLINE + age), cameras live, detections/s, people tracked, active incidents by severity, AI auto-review state, scenario simulator | `/api/stream`, `/api/live/status`, `/api/twin/investigations/auto`, `/api/simulator/*` |
| Camera wall | every configured camera, its stream state, people in view, fps, frame-to-twin latency, whether it feeds the twin or is view-only; click to enlarge | `/api/site`, `/api/live/status` (pushed), `/api/live/cameras/{id}/frame.jpg` |
| Floor plan | real room polygons (north up), badge door at its building-model position, twin headcount per room vs capacity, people dots for calibrated cameras only, active-incident marker per room, optional 10-min heatmap (`h`) | `/api/twin/zones`, `/api/site`, pushed twin state, `/api/twin/heatmap` |
| Incident queue | active (default) or all; severity and type filters; newest first within severity | `/api/twin/incidents`, pushed `incident` events |
| Incident detail | rule summary, track/person labels (marked as system labels), cameras; lifecycle actions; tabs Evidence / Events / AI review | `PATCH /api/twin/incidents/{id}`, `/evidence`, `/api/twin/events?event_ids=`, `/investigations`, `POST /investigate` |

Keyboard: `j`/`k` (or arrows) move through the queue, `Enter` opens, `Esc` closes the detail or the enlarged
camera, `1`/`2`/`3` switch detail tabs, `h` toggles the heatmap.

## How it stays live and honest

- **Push, not polling.** One `EventSource` on `GET /api/stream` (Server-Sent Events): `state` every second,
  `incident` the moment the rules raise or change one, `live` camera health every 2 s, `resync` when the
  browser fell behind. Incidents are kept by ID, so repeats update instead of duplicating.
- **Reconnect and resync.** The browser retries the stream on its own; if it gives up, the dashboard opens a
  new one after 3 s. After any interruption it refetches incidents, twin state and camera health once,
  because anything raised while disconnected was never pushed. Listeners are removed on unmount.
- **Stale is shown as stale.** The top bar turns STALE and a banner appears if no push arrives for 5 s; the
  floor plan shows the twin's age. Camera tiles judge freshness by the frame's capture time
  (`X-Frame-Age-Ms` from the server), not by whether the request succeeded: the backend keeps serving the
  last picture after a stream stops, and the tile then says "Picture frozen" instead of looking live.
- **Camera pictures are short requests, not MJPEG streams.** Browsers allow about 6 connections per host and
  the push channel holds one, so six endless MJPEG streams would starve the API. Each tile fetches the next
  picture after the previous one (at most 4/s), pausing while the tab is hidden. Measured: detection stays at
  30.0 frames/s with the dashboard open.
- **The backend is authoritative.** A status change is shown only after the server confirms it; a failure
  shows the server's reason, keeps the old status and offers Retry.
- **AI verification is never upgraded.** `VERIFIED` only when the server's verification passed; otherwise
  `NOT VERIFIED` / `PARTIALLY SUPPORTED` / `FAILED` / `INVALID OUTPUT` with the reasons (invented IDs are
  struck through, unsupported claims counted). Findings are split into observed vs inferred, each with its
  checker verdict and cited IDs. "Investigate" calls the same server endpoint as before, so the daily budget
  (HTTP 429), AWS sign-in (503) and Bedrock errors (502) are enforced there and explained here.
- **No implied identity.** Track IDs are labelled as per-camera labels, person IDs as system labels; the
  floor plan states there is no cross-camera identity and that room counts are estimates.

## Simulating an incident on the real site

The WiseNET site has its own scenarios (`data/sites/wisenet/scenarios`, all labelled SIMULATED, played at
the current time): `lab_unauthorized_visitor`, `print_room_over_capacity`, `lab_badge_without_entry`. Run one
from the top bar. A simulated incident in a room with a live camera gets that camera's real clip of the
moment; the evidence tab warns that the simulated person is not in the footage.

## Verified end to end (2026-10-15)

`scripts/e2e_dashboard.py --ai` against `demo.py --fresh`: 17/17 checks - API healthy; 6/6 replayed streams
live; push channel LIVE; 6/6 tiles with live pictures; 6 real rooms drawn; on-screen room count matches the
twin; scenario run from the UI; incident appeared without a page reload; 20 s evidence clip loaded and
playable in the browser and identical to the backend's; on-screen AI verdict (VERIFIED) matched the
backend's verification; Start investigating -> backend INVESTIGATING; Resolve -> backend RESOLVED; no
JavaScript errors.

`scripts/e2e_interruptions.py` (RTSP): 9/9 - all streams killed -> every tile left LIVE and showed "Picture
frozen", top bar 0/6 cameras live; streams restored -> all tiles live again in 11 s without reload; backend
killed -> RECONNECTING + banner; backend restarted -> LIVE, banner gone, cameras live, no reload. One earlier
run timed out waiting 120 s for the restarted backend; it was not logged at the time and the cause is
unknown; the logged rerun passed.

## Limits

- Camera mounting positions are not in the site data: cameras are shown by the room they cover, not placed.
- People dots exist only for the two calibrated corridor cameras; other rooms show counts only.
- Tracks are per camera; there is no cross-camera re-identification.
- Room counts undercount dense groups (see `data/sites/wisenet/runs/validation.md`).
- The six feeds are WiseNET recordings replayed in real time (files or RTSP); no real office camera has been
  connected. The demo's badge swipes are scripted.
- The queue keeps the 500 most recent incidents in view (the WiseNET rush loop raises ~13 per 40 s).
- Tested in Microsoft Edge (Chromium) at 1440x900; other browsers and small screens were not tested by hand.
