# Milestone M4: Real Vision on Recorded Clips (Detection + Tracking → Digital Twin)

You are the IMPLEMENTER. Before writing code, read `GEMINI.md`, `AGENTS.md`, `.agents/rules/00-project-constitution.md`, `.agents/skills/vision-pipeline/SKILL.md`, `.agents/skills/digital-twin/SKILL.md` and `.agents/skills/verification/SKILL.md`.

This prompt IS the approved plan. Do not redesign it. If anything contradicts the code, STOP and report the exact contradiction (file, line, spec vs. code). Do not improvise.

**Report honesty rule.** Every number, table, file name and scenario in your reports must come from a command you ran in THIS session, or from a file that exists in the repo. Earlier milestones included invented tables. Reviewers check every claim.

## Decisions already made (do not revisit)
- Detector and tracker: **Ultralytics YOLO** with its built-in tracker (ByteTrack). License is AGPL-3.0. That is accepted for the POC; it must be replaced or commercially licensed before any product release. All Ultralytics code must sit behind ONE adapter file so it can be swapped.
- Hardware (verified by the reviewer): **no NVIDIA GPU.** The machine has an Intel Core Ultra 7 155H (CPU + Intel Arc integrated GPU + NPU) and 15.5 GB RAM. Targets: PyTorch on CPU, and OpenVINO (Intel CPU / GPU / NPU). Do NOT install CUDA builds. Do NOT claim any FPS or latency number that the benchmark in Step 7 did not measure.
- Input: **recorded clip files only.** No RTSP or live cameras in M4.
- Identity: **none.** `person_id` stays `None` for every vision observation. No face recognition, no ReID matching, no embeddings stored. Track IDs are anonymous.
- Bedrock is NOT touched in M4. Frames never leave the machine.
- Geometry: a clip is "played as" one of the existing office cameras. Its frames are scaled to that camera's resolution and projected with that camera's existing homography. The reviewer verified that each camera's full frame maps inside its own zone. Floor positions from public clips are **illustrative, not measured**. Say this in the docs and the report.

## Precondition: check before anything else
- M3b is merged: `backend/src/rules/correlation.py`, `backend/src/twin/history.py` and `backend/src/api/routes_access.py` exist.
- From `backend/`, the full unit suite passes. Record the exact count as the baseline.
- `.\.venv\Scripts\python.exe -c "import src.main"` succeeds with PYTHONPATH unset.
- The venv has NO pip. Install packages with **uv** only: `uv pip install --python .\.venv\Scripts\python.exe ...`. Never run `python -m pip`.

STOP if any check fails.

---

## Step 1: Research (Researcher role; no code)

Use official sources only: docs.ultralytics.com, github.com/ultralytics/ultralytics, docs.openvino.ai, pypi.org. Write `docs/research/M4_VISION_RESEARCH.md` containing, with a source link and access date for each item:
1. The latest stable `ultralytics` version on PyPI, and the exact version you will pin. Its license.
2. The exact Python API for tracking with persistence across frames: `model.track(source=frame, persist=True, tracker="bytetrack.yaml", classes=[0], conf=..., device=..., verbose=False)`. Confirm each argument name. Say how to read the boxes (`xyxy`), confidences and track IDs from the result. Say what happens when `boxes.id` is `None`.
3. How to export to OpenVINO (`model.export(format="openvino", ...)`): the output folder name, and how to load it for tracking.
4. The exact `device` strings Ultralytics accepts for OpenVINO inference on Intel CPU, integrated GPU and NPU. If the docs do not state them clearly, write "UNVERIFIED" and do not guess.
5. How to reset the tracker state between independent clips (for example, a new `YOLO` instance or a documented reset). Cite the source.
6. Whether `ultralytics` pulls in `opencv-python` and `torch`. If so, we use its OpenCV and do NOT add `opencv-python-headless`.
7. How to install CPU-only torch on Windows with uv, if the default would pull a CUDA build.

**STOP after Step 1 and report the research file.** The reviewer approves the pinned versions before any install.

---

## Step 2: Dependencies and configuration

- `backend/pyproject.toml`: add an optional dependency group `vision = ["ultralytics==<pinned version>", "openvino==<pinned version>"]` (pins from Step 1). Core dependencies are unchanged. The app MUST start and all non-vision tests MUST pass WITHOUT the vision extra.
- Add a `vision_model` pytest marker in `[tool.pytest.ini_options]` (`markers = ["vision_model: needs real YOLO weights and a real clip"]`). Add `addopts = "-m 'not vision_model'"` so those tests are skipped by default.
- `backend/src/config.py` and `.env.example`, additive:
  - `vision_model: str = "yolo11n.pt"`. The weights file name; confirm the name in Step 1.
  - `vision_models_dir: str = "data/models"`
  - `vision_device: str = "cpu"`
  - `vision_tracker: str = "bytetrack.yaml"`
  - `vision_clips_dir: str = "data/clips"`
  - `vision_min_confidence: float = 0.35`
  - `vision_process_fps: float = 5.0`
  - `vision_benchmarks_dir: str = "data/benchmarks"`
- `.gitignore`: add `data/models/`, `data/clips/out/`, `data/clips/*.mov`, `data/clips/*.webm`. Keep the existing video rules.
- Install with `uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,vision]"`. Paste the uv output summary, and the installed versions of ultralytics, torch, openvino and opencv.
- Verify the torch build is CPU (`torch.version.cuda` is `None`). Paste the output.

---

## Step 3: Tracker interface, Ultralytics adapter, fake tracker

Create the package `backend/src/vision/tracking/`:
- `base.py`:
  - dataclass `TrackedDetection(local_track_id: int, bbox: BoundingBox2D, confidence: float)`. Reuse `BoundingBox2D` from `vision/geometry.py`.
  - ABC `PersonTracker` with:
    - `update(self, frame) -> List[TrackedDetection]`, where `frame` is an HxWx3 BGR numpy array;
    - `reset(self) -> None`;
    - property `name -> str`.
- `ultralytics_tracker.py`: `UltralyticsPersonTracker(model: str, device: str, tracker_cfg: str, min_confidence: float, models_dir: str)`.
  - **Import `ultralytics` INSIDE `__init__`, never at module top level.** If the import fails, raise `RuntimeError("Vision extra not installed: uv pip install -e .[vision]")`.
  - Weights are loaded from or downloaded into `models_dir`. Resolve the directory with `core.paths._resolve_data_path`.
  - `update()` calls the tracking API exactly as documented in Step 1, with `classes=[0]` (person only). It returns one `TrackedDetection` per box that has a track id. Boxes without an id are skipped.
  - `reset()` restores fresh tracker state using the method documented in Step 1.
  - This is the ONLY file in the repo allowed to import `ultralytics`.
- `fake.py`: `ScriptedTracker(script: Dict[int, List[TrackedDetection]])` returns `script.get(call_index, [])` on each `update()` call, counting calls from 0. Used by all unit tests.
- `__init__.py` exports `TrackedDetection`, `PersonTracker` and `ScriptedTracker`. It must NOT import `ultralytics_tracker`.

---

## Step 4: Twin write lock (thread safety)

Clip processing runs in a background thread, while the API, the scenario simulator and the prune loop also write to the twin. The rule and correlation engines hold unlocked dicts.

- Create `backend/src/core/locks.py` with `TWIN_WRITE_LOCK = threading.RLock()`. It must be an RLock, because the scenario runner calls the pipeline (nested).
- Hold it (`with TWIN_WRITE_LOCK:`) around the whole body of:
  - `ObservationPipeline.process_observation`
  - `AccessControlService.ingest`
  - `_prune_once` in `main.py`
  - the PATCH incident handler in `routes_twin.py`, around its read-modify-write plus `forget`
- Change nothing else in those functions.
- Add `tests/unit/test_twin_lock.py`: 4 threads × 50 observations each, through one pipeline with the rule engine, on a tmp DB. Assert no exceptions, assert the event count in the store equals the sum the pipeline returned, and assert the `sequence_num` values are unique and contiguous.

---

## Step 5: Clip configuration and runner

Clip config files are `data/clips/<name>.yaml`:
```yaml
name: lobby_walkthrough
description: "Recorded walkthrough played as the reception camera."
video_file: lobby_walkthrough.mp4      # relative to vision_clips_dir
camera_id: cam_reception_01            # must exist in the twin store and calibration
base_time_utc: "2026-01-20T09:30:00Z"  # event time of frame 0
process_fps: 5.0                       # optional; default settings.vision_process_fps
max_frames: null                       # optional cap on frames READ
source_note: "Own recording, consented"  # required free text: dataset name + license, or recording consent
```

Create `backend/src/vision/clips.py`:
- dataclass `ClipConfig`, `load_clip_config(path)`, `list_clip_configs(dir)`. Validation:
  - `name` matches the file stem;
  - `camera_id` is non-empty;
  - `source_note` is non-empty;
  - `process_fps` > 0.

  Anything invalid raises `ValueError`.

Create `backend/src/vision/clip_runner.py`, with class `VideoClipRunner(pipeline: ObservationPipeline, camera_nodes: Dict[str, CameraNode], clips_dir: str)`. Its method is `run(config, tracker: PersonTracker, run_id: str, cancel_event: Optional[threading.Event] = None, progress_cb: Optional[Callable[[int], None]] = None) -> ClipRunResult`.

**Import `cv2` inside `run()`, never at module top level.**

1. Open the video with `cv2.VideoCapture`. If it cannot be opened, raise `FileNotFoundError` with the path.
2. `src_fps = cap.get(CAP_PROP_FPS)`. If it is ≤ 0 or NaN, use 25.0 and add a warning to `result.warnings`.
3. `stride = max(1, round(src_fps / process_fps))`. Read every frame and process only those where `frame_index % stride == 0`. Stop at `max_frames` frames read. Check `cancel_event` before each frame; if it is set, stop with `status="cancelled"`.
4. Call `tracker.reset()` once before the first frame.
5. The frame timestamp is `base_time_utc + frame_index / src_fps` seconds, as a UTC ISO string. That is event time, never wall clock.
6. For each `TrackedDetection`:
   - Clamp the bbox to the frame.
   - Scale it from frame size (w, h) to the camera's `resolution_width` × `resolution_height`.
   - Build `CameraObservation(camera_id=config.camera_id, track_id=f"{config.name}:{run_id[:8]}:{local_track_id}", bbox=scaled_bbox, confidence=det.confidence, timestamp=ts, person_id=None)` and call `pipeline.process_observation`.
   - If one observation fails, record the error (keep the first 50 errors) and continue.
7. Measure per processed frame, with `time.perf_counter`:
   - `decode_ms`: frame read;
   - `track_ms`: `tracker.update`;
   - `ingest_ms`: sum of `process_observation` calls for that frame.
8. Call `progress_cb(frames_read)` every processed frame, if it is given.

`ClipRunResult` fields:
- `run_id`, `clip_name`, `camera_id`, `status` (`completed` / `cancelled` / `failed`);
- `frames_read`, `frames_processed`, `source_fps`, `stride`;
- `observations`, `unique_tracks`, `events_emitted`, `event_type_counts`;
- `incident_ids` (unique, creation order);
- `timings` = `{decode_ms, track_ms, ingest_ms}`, each `{mean, p50, p95}`, computed with the stdlib `statistics` module;
- `wall_seconds`, `effective_fps` (`frames_processed / wall_seconds`);
- `errors`, `warnings`.

Write `docs/VISION_CLIPS.md`. It covers:
- where to put clips and how to write the YAML;
- that floor positions are illustrative because the clip is played through an office camera's calibration;
- dataset license notes: MOT17 is CC BY-NC-SA, non-commercial, internal validation only and never client-facing; PETS2009 is research use; own office recordings need consent and signage per company policy.

---

## Step 6: Clip job API

Create `backend/src/vision/clip_jobs.py` with class `ClipJobManager`:
- At most ONE active run at a time.
- `start(config, tracker_factory) -> run_id` spawns a `threading.Thread(daemon=True)`.
- It holds an in-memory registry `run_id -> {status: queued|running|completed|failed|cancelled, frames_read, started_at, finished_at, result (dict) or None, error or None}`.
- `cancel(run_id)` sets that run's `cancel_event`.
- `get(run_id)`.
- If the tracker factory or the runner raises, the status becomes `failed` with the error message, and the thread never crashes the server.

`tracker_factory` is a zero-argument callable stored at `app.state.tracker_factory`. In `main.py` its default builds `UltralyticsPersonTracker` from settings, importing the module lazily inside the factory. Tests replace it with a function returning a `ScriptedTracker`.

Add to `backend/src/api/routes_vision.py` (additive):
- `GET /api/vision/clips` returns `[{name, description, camera_id, video_file, video_present: bool, source_note}]`.
- `POST /api/vision/clips/{name}/run` returns 202 with `{run_id}`. Errors:
  - unknown clip: 404;
  - video file missing: 422;
  - another run active: 409;
  - pipeline not initialized: 503.
- `GET /api/vision/clips/runs/{run_id}` returns the registry entry; unknown id: 404.
- `POST /api/vision/clips/runs/{run_id}/cancel`: 202, or 404 for an unknown id, or 409 if the run is already finished.

Wire `ClipJobManager`, `VideoClipRunner` and the default `tracker_factory` in the `main.py` lifespan, inside the existing vision try block. Use camera nodes from `VisionSeedLoader.load_cameras`.

**Import rule:** `import src.main` must not import `cv2`, `torch` or `ultralytics`. Add a subprocess test to `tests/unit/test_app_import.py` that runs `import src.main, sys; assert not ({"cv2","torch","ultralytics"} & set(sys.modules))`.

---

## Step 7: CLI (run, benchmark, export, MOT evaluation)

Create `backend/src/vision/cli.py`, runnable as `python -m src.vision.cli <command>`, using `argparse`. Each command prints one JSON document to stdout.

1. **`run-clip <name> [--device D] [--max-frames N]`**
   - Bootstraps the twin, the rules and the pipeline the same way `main.py` does. Refactor the shared setup into one function in `main.py` if needed, but do not duplicate it.
   - Runs `VideoClipRunner` synchronously with `UltralyticsPersonTracker` and prints the `ClipRunResult`.
   - Uses `settings.database_url`. The docs must tell users to point `DATABASE_URL` at a throwaway DB for experiments.
2. **`export-openvino [--model M]`**
   - Exports the weights to OpenVINO in `vision_models_dir`, using the API from Step 1.
   - Prints the output path.
3. **`benchmark --clip <name> --configs torch-cpu,openvino-cpu,openvino-gpu,openvino-npu --frames 300`**
   - Measures detection + tracking ONLY. It does NOT write to the twin.
   - For each config:
     - build the tracker (`openvino-*` uses the exported model, exporting it first if missing, with the device string from Step 1);
     - run 10 warm-up frames, which are excluded from the stats;
     - measure `track_ms` mean/p50/p95 and FPS over `--frames` frames;
     - record `model_load_seconds`.
   - A config that fails (for example an unverified device string) is recorded as `{status: "failed", error}`. It never aborts the other configs.
   - Writes `data/benchmarks/<UTC timestamp>_benchmark.json` containing `{hardware: {platform, processor, python}, versions: {ultralytics, torch, openvino, opencv}, clip, frames, results: [...]}`.
4. **`eval-mot --seq-dir <path to a MOT17 sequence> [--max-frames N] [--device D]`**
   - Reads `img1/*.jpg` in order and `gt/gt.txt`.
   - Ground-truth columns are `frame,id,x,y,w,h,conf,class,visibility`. Keep only rows with `conf == 1` and `class == 1`.
   - Runs the tracker on the frames.
   - Uses pure functions in `backend/src/vision/eval/mot.py` (numpy only, no new dependencies):
     - `iou_matrix`;
     - `greedy_match(iou >= 0.5)`;
     - `evaluate_sequence(gt_by_frame, pred_by_frame) -> {frames, gt_boxes, pred_boxes, true_positives, precision, recall, id_switches}`. An ID switch counts each time a ground-truth id is matched to a different predicted id than at its previous match.
   - States in its output: `"note": "MOT17 is CC BY-NC-SA; internal validation only."`

---

## Step 8: Tests

None of these tests may use the network, YOLO weights or a GPU. They use tmp_path DBs and no `time.sleep` (wait on threads with `join(timeout=...)`).

- **Synthetic video helper** (in a test util module): generates a 3-second 320×240 clip at 10 fps with `cv2.VideoWriter` (`mp4v` codec) in tmp_path. If the codec is unavailable, fall back to MJPG `.avi` and record which one was used.
- `tests/unit/test_vision_clips.py`: config validation (each rule) and the list function.
- `tests/unit/test_clip_runner.py`, using `ScriptedTracker`:
  - stride: src 10 fps, process 5 fps gives every 2nd frame;
  - event-time timestamps;
  - bbox clamping and scaling to 1920×1080 (assert the exact numbers);
  - track ID namespacing;
  - observations reach the twin and its events;
  - a track entering the server-room camera produces `UNAUTHORIZED_ENTRY` and `ACCESS_WITHOUT_BADGE` (person_id is None);
  - `max_frames`;
  - cancel;
  - an unopenable file gives `FileNotFoundError`;
  - an fps fallback warning when it applies;
  - one failing observation is recorded and the run continues.
- `tests/unit/test_clip_jobs.py` and API tests in `tests/unit/test_api_vision_clips.py`, with `app.state.tracker_factory` overridden to return a `ScriptedTracker`:
  - 202 with completion polling (join with timeout);
  - 404 / 409 / 422 / 503;
  - cancel;
  - a failing factory makes the status `failed` while the server stays up.
- `tests/unit/test_mot_eval.py`: hand-computed IoU, greedy matching, precision/recall, and an ID-switch count on tiny synthetic data.
- `tests/unit/test_ultralytics_tracker.py`: monkeypatch the `ultralytics` import so it fails, and assert the `RuntimeError` message. Do NOT load real weights.
- `tests/integration/test_yolo_real.py`, marked `@pytest.mark.vision_model`: runs the real tracker on the clip named by env `VISION_E2E_CLIP` and asserts at least 1 detection. It is skipped by default (see addopts).

---

## Step 9: Verification (mandatory, from `backend/`, PowerShell)

1. `.\.venv\Scripts\python.exe -m pytest tests/unit -q`: baseline count + new tests, 0 failures, **0 skipped**. Paste the last 5 lines.
2. Import checks with PYTHONPATH unset: `import src.main` succeeds, and the cv2/torch/ultralytics-not-loaded test passes.
3. Run the DB-isolation check from M3b (the `backend/data/digital_twin.db` mtime is unchanged).
4. **Real clip.** List `data/clips/`. If no real video and YAML exist, STOP and ask the user to place one, giving them the instructions from `docs/VISION_CLIPS.md`. Do NOT download datasets yourself, and do NOT invent a clip.
5. With a real clip and `$env:DATABASE_URL="sqlite:///./data/m4_smoke.db"`:
   - `python -m src.vision.cli run-clip <name> --max-frames 300`. Paste the JSON.
   - `python -m src.vision.cli export-openvino`, then `python -m src.vision.cli benchmark --clip <name> --frames 200 --configs torch-cpu,openvino-cpu,openvino-gpu,openvino-npu`. Paste the full benchmark JSON. Failed configs are acceptable if their error is recorded.
   - Start uvicorn on the same smoke DB. Then:
     - `GET /api/vision/clips`;
     - `POST .../run`;
     - poll `GET .../runs/{id}` until finished, pasting the first and the final response;
     - `GET /api/twin/heatmap` over the clip's time window;
     - `GET /api/twin/incidents`.
   - Delete `m4_smoke.db` (and its `-wal` / `-shm` files) afterwards. Keep the benchmark JSON (commit it).

## Out of scope (do NOT do)
RTSP or live cameras; ReID or cross-camera identity; faces; saving frames or crops; Bedrock; frontend; new zones or calibrations; changing existing rule semantics; CUDA; downloading datasets; renaming any existing public function, route, field or file.

## Final report (required format)
1. Precondition results (baseline count).
2. Pinned versions, with links to the research file.
3. Files created/modified, and why for each.
4. Test command and real counts (baseline vs now, 0 skipped).
5. Real `run-clip` JSON, benchmark JSON, and API smoke outputs.
6. Every deviation from this spec, with the reason.
7. Known limitations. At minimum:
   - floor positions from a non-office clip are illustrative;
   - there is no cross-camera identity;
   - the tracker ID resets per run;
   - one active clip run at a time;
   - AGPL license;
   - CPU/OpenVINO performance is as measured only.
