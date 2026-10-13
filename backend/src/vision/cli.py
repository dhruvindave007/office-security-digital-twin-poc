"""Vision command-line tools. Run from backend/:  python -m src.vision.cli <command> ...

Commands (each prints ONE JSON document on stdout; library logs go to stderr):
  run-clip <name> [--device D] [--model M] [--max-frames N]   run a configured clip through the twin (writes DATABASE_URL!)
  export-openvino [--model M]                       export YOLO weights to OpenVINO IR next to the weights
  benchmark --clip <name> [--configs ...] [--frames N]   detection+tracking latency only (never writes the twin)
  eval-mot --seq-dir <MOT17 sequence> [--max-frames N] [--device D] [--model M]

Heavy libraries (cv2, ultralytics, openvino) are imported inside the commands only.
"""
import argparse
import contextlib
from dataclasses import replace
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Dict, List, Optional
import uuid

try:
    from src.config import get_settings
    from src.core.paths import _resolve_data_path
    from src.runtime import build_runtime, make_tracker
    from src.vision.clips import ClipConfig, load_clip_config
except ImportError:
    from config import get_settings
    from core.paths import _resolve_data_path
    from runtime import build_runtime, make_tracker
    from vision.clips import ClipConfig, load_clip_config

REPO_ROOT = Path(__file__).resolve().parents[3]

# benchmark config name -> (use OpenVINO export?, ultralytics device string, OpenVINO device name)
BENCH_CONFIGS: Dict[str, tuple] = {
    "torch-cpu": (False, "cpu", None),
    "openvino-cpu": (True, "intel:cpu", "CPU"),
    "openvino-gpu": (True, "intel:gpu", "GPU"),
    "openvino-npu": (True, "intel:npu", "NPU"),
}
WARMUP_FRAMES = 10


def _emit(doc: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(doc, indent=2, default=str) + "\n")
    sys.stdout.flush()


@contextlib.contextmanager
def _quiet_stdout():
    """Keep third-party prints (ultralytics/openvino) off stdout so stdout stays pure JSON."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _load_clip(settings: Any, name: str) -> tuple:
    clips_dir = _resolve_data_path(settings.vision_clips_dir, "data/clips")
    for ext in (".yaml", ".yml"):
        path = Path(clips_dir) / f"{name}{ext}"
        if path.exists():
            return load_clip_config(path), Path(clips_dir)
    raise SystemExit(f"Clip config '{name}' not found in {clips_dir}")


def _stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, max(0, int(round(0.95 * len(ordered))) - 1))]
    return {"mean": round(statistics.mean(values), 2), "p50": round(statistics.median(values), 2), "p95": round(p95, 2)}


def _openvino_model_name(model: str) -> str:
    return f"{Path(model).stem}_openvino_model"


def _models_dir(settings: Any) -> Path:
    path = _resolve_data_path(settings.vision_models_dir, "data/models")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _versions() -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for mod, attr in (("ultralytics", "__version__"), ("torch", "__version__"), ("openvino", "__version__"), ("cv2", "__version__")):
        try:
            out[mod] = getattr(__import__(mod), attr)
        except Exception as exc:  # report, never crash the benchmark over a version lookup
            out[mod] = f"unavailable: {exc}"
    return out


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or "unknown"


def export_openvino(settings: Any, model: Optional[str] = None) -> Path:
    """Export the .pt weights to an OpenVINO IR directory next to them (idempotent)."""
    model = model or settings.vision_model
    target = _models_dir(settings) / _openvino_model_name(model)
    if target.exists():
        return target
    with _quiet_stdout():
        tracker = make_tracker(settings, model=model, device="cpu")  # downloads weights if needed
        from ultralytics import YOLO

        exported = YOLO(tracker.weights_path).export(format="openvino")
    exported_path = Path(str(exported))
    if exported_path.resolve() != target.resolve():
        raise RuntimeError(f"OpenVINO export landed at {exported_path}, expected {target}")
    return target


def cmd_run_clip(args: argparse.Namespace) -> None:
    settings = get_settings()
    config, _ = _load_clip(settings, args.name)
    if args.max_frames is not None:
        config = replace(config, max_frames=args.max_frames)
    runtime = build_runtime(settings)
    if runtime.clip_runner is None:
        raise SystemExit("Vision pipeline failed to initialize; see logs.")
    with _quiet_stdout():
        tracker = make_tracker(settings, model=args.model, device=args.device)
        result = runtime.clip_runner.run(config=config, tracker=tracker, run_id=f"run_{uuid.uuid4().hex[:12]}")
    doc = result.to_dict()
    doc["database_url"] = settings.database_url
    _emit(doc)


def cmd_export_openvino(args: argparse.Namespace) -> None:
    settings = get_settings()
    target = export_openvino(settings, args.model)
    _emit({"openvino_model_dir": str(target), "files": sorted(p.name for p in target.iterdir())})


def _read_frames(clip_path: Path, config: ClipConfig, count: int) -> tuple:
    """Read `count` frames at the clip's processing stride (what the tracker really sees)."""
    import cv2

    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video {clip_path}")
    raw_fps = cap.get(cv2.CAP_PROP_FPS)
    src_fps = config.source_fps_override or (raw_fps if 0 < raw_fps <= 120 else 25.0)
    stride = max(1, round(src_fps / config.process_fps))
    frames, idx = [], 0
    try:
        while len(frames) < count:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                frames.append(frame)
            idx += 1
    finally:
        cap.release()
    return frames, stride


def cmd_benchmark(args: argparse.Namespace) -> None:
    settings = get_settings()
    config, clips_dir = _load_clip(settings, args.clip)
    requested = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [c for c in requested if c not in BENCH_CONFIGS]
    if unknown:
        raise SystemExit(f"Unknown benchmark configs {unknown}; choose from {sorted(BENCH_CONFIGS)}")

    frames, stride = _read_frames(clips_dir / config.video_file, config, WARMUP_FRAMES + args.frames)
    if len(frames) <= WARMUP_FRAMES:
        raise SystemExit(f"Clip yielded only {len(frames)} frames; need more than {WARMUP_FRAMES}.")

    try:
        import openvino

        ov_devices = list(openvino.Core().available_devices)
    except Exception as exc:
        ov_devices = [f"unavailable: {exc}"]

    results = []
    for name in requested:
        use_ov, device, ov_name = BENCH_CONFIGS[name]
        entry: Dict[str, Any] = {"config": name, "device": device}
        # OpenVINO silently falls back to CPU/AUTO for a missing device (ultralytics
        # nn/backends/openvino.py), so never benchmark a device that is not actually present.
        if use_ov and not any(d == ov_name or d.startswith(f"{ov_name}.") for d in ov_devices):
            entry.update(status="unavailable", available_devices=ov_devices)
            results.append(entry)
            continue
        try:
            with _quiet_stdout():
                model = settings.vision_model
                if use_ov:
                    model = export_openvino(settings).name
                t0 = time.perf_counter()
                tracker = make_tracker(settings, model=model, device=device)  # fresh model = fresh tracker state
                load_s = time.perf_counter() - t0
                for frame in frames[:WARMUP_FRAMES]:
                    tracker.update(frame)
                lat, dets = [], []
                for frame in frames[WARMUP_FRAMES:]:
                    t = time.perf_counter()
                    out = tracker.update(frame)
                    lat.append((time.perf_counter() - t) * 1000.0)
                    dets.append(len(out))
            mean_ms = statistics.mean(lat)
            entry.update(
                status="ok",
                model=model,
                model_load_seconds=round(load_s, 3),
                frames_measured=len(lat),
                track_ms=_stats(lat),
                fps=round(1000.0 / mean_ms, 2) if mean_ms > 0 else 0.0,
                mean_tracked_people_per_frame=round(statistics.mean(dets), 2),
            )
        except Exception as exc:
            entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        results.append(entry)

    doc = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "hardware": {"platform": platform.platform(), "processor": _cpu_name(), "python": platform.python_version(), "openvino_devices": ov_devices},
        "versions": _versions(),
        "clip": {"name": config.name, "video_file": config.video_file, "process_fps": config.process_fps, "stride": stride},
        "frames": {"warmup": WARMUP_FRAMES, "measured_requested": args.frames},
        "note": "Detection + tracking only (tracker.update). Twin ingest is measured by run-clip.",
        "results": results,
    }
    out_dir = Path(settings.vision_benchmarks_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_benchmark.json"
    out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    doc["written_to"] = str(out_path)
    _emit(doc)


def cmd_eval_mot(args: argparse.Namespace) -> None:
    import cv2

    try:
        from src.vision.eval.mot import evaluate_sequence, parse_mot_gt
    except ImportError:
        from vision.eval.mot import evaluate_sequence, parse_mot_gt

    settings = get_settings()
    seq = Path(args.seq_dir)
    images = sorted((seq / "img1").glob("*.jpg"))
    gt_file = seq / "gt" / "gt.txt"
    if not images or not gt_file.exists():
        raise SystemExit(f"{seq} must contain img1/*.jpg and gt/gt.txt")
    if args.max_frames:
        images = images[: args.max_frames]
    frame_numbers = [int(p.stem) for p in images]  # MOT frames are 1-based and named 000001.jpg ...

    gt_all = parse_mot_gt(gt_file.read_text(encoding="utf-8"))
    gt = {f: gt_all.get(f, []) for f in frame_numbers}

    pred: Dict[int, list] = {}
    lat: List[float] = []
    with _quiet_stdout():
        tracker = make_tracker(settings, model=args.model, device=args.device)
        for path, fno in zip(images, frame_numbers):
            frame = cv2.imread(str(path))
            t = time.perf_counter()
            dets = tracker.update(frame)
            lat.append((time.perf_counter() - t) * 1000.0)
            pred[fno] = [(d.local_track_id, (d.bbox.u_min, d.bbox.v_min, d.bbox.u_max, d.bbox.v_max)) for d in dets]

    metrics = evaluate_sequence(gt, pred)
    _emit(
        {
            "sequence": seq.name,
            "model": args.model or settings.vision_model,
            "device": args.device or settings.vision_device,
            "min_confidence": settings.vision_min_confidence,
            "metrics": metrics,
            "track_ms": _stats(lat),
            "matching": "greedy IoU>=0.5 (simplified; not official MOTA/HOTA)",
            "note": "MOT17 is CC BY-NC-SA; internal validation only.",
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.vision.cli", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run-clip", help="Run a configured clip through the twin (writes to DATABASE_URL).")
    p.add_argument("name")
    p.add_argument("--device", default=None)
    p.add_argument("--model", default=None, help="e.g. yolo11n_openvino_model with --device intel:gpu")
    p.add_argument("--max-frames", type=int, default=None)
    p.set_defaults(func=cmd_run_clip)

    p = sub.add_parser("export-openvino", help="Export YOLO weights to OpenVINO IR.")
    p.add_argument("--model", default=None)
    p.set_defaults(func=cmd_export_openvino)

    p = sub.add_parser("benchmark", help="Detection+tracking latency per backend/device.")
    p.add_argument("--clip", required=True)
    p.add_argument("--configs", default=",".join(BENCH_CONFIGS))
    p.add_argument("--frames", type=int, default=300)
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("eval-mot", help="Precision/recall/ID switches on a MOT17 sequence.")
    p.add_argument("--seq-dir", required=True)
    p.add_argument("--max-frames", type=int, default=None)
    p.add_argument("--device", default=None)
    p.add_argument("--model", default=None)
    p.set_defaults(func=cmd_eval_mot)
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
