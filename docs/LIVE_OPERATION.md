# Live operation: how it works and what was measured

Measured 2026-10-15 on the development laptop (Intel Core Ultra 7 155H, integrated Arc GPU + NPU, no
NVIDIA GPU) with the WiseNET building (6 real cameras, recordings replayed as live). WiseNET footage is
CC BY-NC-SA 4.0: internal use only.

## Start it

    backend\.venv\Scripts\python.exe scripts\demo.py --fresh          # cameras from files, played in real time
    backend\.venv\Scripts\python.exe scripts\demo.py --fresh --rtsp   # cameras over RTSP, like IP cameras

`READY` is printed only after it has verified: API health, every camera live and processing, a camera
picture, and the push channel delivering twin state.

## The pipeline, per camera

    camera stream -> newest frame only -> detect + track (YOLO11s, OpenVINO) -> feet -> floor position
                  -> digital twin (rooms, events) -> rules (+ badge correlation) -> incidents
                  -> evidence clip, push to screens, automatic AI review (serious incidents only)

| Piece | File | What it guarantees |
|---|---|---|
| Stream reader | `backend/src/live/source.py` | Keeps only the newest frame (never a queue), stamps it with wall-clock UTC, reconnects with backoff, RTSP over TCP with timeouts, reopens a stream that runs at under half its own frame rate for 6 s, hides camera passwords in status. |
| Camera worker | `backend/src/live/worker.py` | Processes `process_fps` frames/s; if it falls behind it skips slots (counted) instead of queueing, so delay cannot grow. Warms the model up before going live. |
| Floor position | `backend/src/live/projector.py` | Calibrated cameras (cam_1, cam_2): measured floor position. Other cameras: room level (person placed at the room centre). |
| Badge door gate | `backend/src/live/entry_gate.py` | An entry counts only if the person's first box is at the door and they are tracked >= 5 times; decided at the 5 s late-badge deadline using only what was seen so far. |
| Evidence | `backend/src/live/evidence.py` | 12 s before + 8 s after each HIGH/CRITICAL incident, H.264 + key frame; bursts share one clip; disk quota. |
| Auto AI review | `backend/src/ai/investigation/auto.py` | One per (incident type, room) per 10 min, max 12/hour, $1/day budget, waits for the clip, pauses if AWS sign-in expired. |
| Push channel | `backend/src/api/routes_stream.py` | `GET /api/stream` (SSE): incidents as raised, twin state every 1 s, camera health every 2 s. |

## Measured results

**Speed.** The twin used to take 95 ms per camera frame (a new SQLite connection per event); with one
connection per thread it takes 2.2 ms (p95 6 ms). Inference at the cameras' own 4:3 shape (480x640)
instead of padding to 640x640: 19 ms instead of 26 ms on the GPU, same accuracy against the dataset's
20,725 hand-drawn boxes (precision 0.848 vs 0.845, recall 0.721 vs 0.725). Batching 6 frames into one
GPU call was slower (204 ms vs 176 ms), so each camera runs separately: 4 on the GPU, 2 on the NPU.

**Six cameras live (files played in real time), 3-minute steady state:**

| Camera | Device | Processed fps | Frame -> twin p50 / p95 |
|---|---|---|---|
| cam_1 | NPU | 4.99 | 82 / 112 ms |
| cam_2 | GPU | 4.99 | 90 / 121 ms |
| cam_3 | GPU | 4.99 | 71 / 116 ms |
| cam_4 | GPU | 5.00 | 77 / 102 ms |
| cam_5 | NPU | 5.02 | 80 / 114 ms |
| cam_6 | GPU | 5.03 | 67 / 95 ms |

Total 30.0 detections/s; 1.3% of processing slots skipped (never queued).

**20-minute soak** (`backend/data/benchmarks/soak_20261015T142429Z.md`): 30.0 fps throughout, worst
camera p95 90-160 ms, no skipped slots after warm-up, no reconnects, server memory ~920-965 MB (6 models).

**RTSP (MediaMTX + ffmpeg, H.264 like IP cameras):** all 6 streams at 25 fps; processed 4.6-5.0 fps
because the 6 replay encoders run on the same laptop (real cameras encode on the camera).
*Interruption test:* all streams killed mid-run -> cameras show `reconnecting`, the twin releases their
people; streams restarted -> all 6 live again within 20 s, no restart of the system.

**What "frame -> twin" does not include:** the camera's own encoding and network delay (typically
0.1-0.5 s for RTSP cameras) happens before our clock starts.

**Storage.** Each person's position is stored once per second (zone changes always). Main database
growth fell from 1.2 to ~0.4 MB/min in the busiest scene (a 14-person rush looping every 40 s), about
0.6 GB/day. Not done yet: automatic deletion of old position samples after N days.

**Badges on live video** (set 5, one valid swipe per 40 s loop): the badge holder is admitted without
an alarm; the followers raise TAILGATING + UNAUTHORIZED_ENTRY, about 13 per loop (ground truth: 13
followers). Every serious incident had an evidence clip.

**Automatic AI review on live incidents:** works end to end (Claude Haiku 5.5 on Bedrock), ~$0.009-0.011
per investigation (live evidence packets are larger than the eval's); duplicate suppression skipped
91 of 93 incidents in 2.5 minutes of the rush. Unsupported claims are caught by verification.
With prompt v1 both live reviews had 2 true-but-uncited claims; with v2 (2026-10-15) both had 0
unsupported claims, one passed verification and one failed because it cited an ID that does not exist
(`01a13ffc-4f00-0000-0000-...`, a padded copy of a real ID) - caught by the ID check. Watch this: requiring
an ID on every item may tempt the model to invent one when the evidence packet is large (~33k tokens).

## Known limits

- No cross-camera re-identification: one camera per room feeds the twin (others are display only).
- Room-level cameras (cam_3-6) cannot give positions inside the room.
- The demo's badge swipes are scripted (`badge_replay` in `live.yaml`); the recordings have no reader.
- Badge-door decisions arrive ~5 s after the entry (the late-badge tolerance), by design.
- Tested on one laptop with recorded footage; no real office camera has been connected yet.
