from dataclasses import asdict, dataclass
from datetime import timedelta
import logging
import math
from pathlib import Path
import statistics
import threading
import time
from typing import Any, Callable, Dict, List, Optional

try:
    from src.core.locks import TWIN_WRITE_LOCK
    from src.rules.policy import parse_utc_timestamp
    from src.vision.camera_graph import CameraNode
    from src.vision.clips import ClipConfig
    from src.vision.geometry import BoundingBox2D, CameraObservation
    from src.vision.pipeline import ObservationPipeline
    from src.vision.tracking.base import PersonTracker
except ImportError:
    from core.locks import TWIN_WRITE_LOCK
    from rules.policy import parse_utc_timestamp
    from vision.camera_graph import CameraNode
    from vision.clips import ClipConfig
    from vision.geometry import BoundingBox2D, CameraObservation
    from vision.pipeline import ObservationPipeline
    from vision.tracking.base import PersonTracker

logger = logging.getLogger("office_vision.vision.clip_runner")


def _calc_stats(values: List[float]) -> Dict[str, float]:
    """Calculate mean, p50, and p95 for a list of measurement durations in milliseconds."""
    if not values:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0}
    mean_val = statistics.mean(values)
    median_val = statistics.median(values)
    if len(values) >= 2:
        try:
            p95_val = statistics.quantiles(values, n=100)[94]
        except Exception:
            sorted_v = sorted(values)
            idx = min(len(sorted_v) - 1, math.ceil(0.95 * len(sorted_v)) - 1)
            p95_val = sorted_v[idx]
    else:
        p95_val = values[0]
    return {
        "mean": round(mean_val, 2),
        "p50": round(median_val, 2),
        "p95": round(p95_val, 2),
    }


@dataclass
class ClipRunResult:
    """Summary of a video clip execution run."""
    run_id: str
    clip_name: str
    camera_id: str
    status: str  # completed / cancelled / failed
    frames_read: int
    frames_processed: int
    source_fps: float
    stride: int
    observations: int
    unique_tracks: int
    events_emitted: int
    event_type_counts: Dict[str, int]
    incident_ids: List[str]
    timings: Dict[str, Dict[str, float]]
    wall_seconds: float
    effective_fps: float
    errors: List[str]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VideoClipRunner:
    """Runs video files through tracking, camera projection, and the Digital Twin pipeline."""

    def __init__(
        self,
        pipeline: ObservationPipeline,
        camera_nodes: Dict[str, CameraNode],
        clips_dir: str,
    ):
        self.pipeline = pipeline
        self.camera_nodes = camera_nodes
        self.clips_dir = clips_dir

    def run(
        self,
        config: ClipConfig,
        tracker: PersonTracker,
        run_id: str,
        cancel_event: Optional[threading.Event] = None,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> ClipRunResult:
        """Execute a clip run with deterministic frame stride and event-time ingestion.

        NOTE: cv2 is imported ONLY inside this method, never at module top level.
        """
        import cv2

        wall_start = time.perf_counter()

        # Fail fast: an unknown camera would otherwise fail every observation silently
        # and the run would still report "completed".
        cam_node = self.camera_nodes.get(config.camera_id)
        if cam_node is None:
            raise ValueError(
                f"Clip '{config.name}' references unknown camera_id '{config.camera_id}'. "
                f"Known cameras: {sorted(self.camera_nodes)}"
            )
        # Clip runs only ever prune tracks they created (never live/other tracks).
        own_track_prefix = f"{config.name}:{run_id}:"

        video_path = Path(self.clips_dir) / config.video_file
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found at: {video_path}")

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise FileNotFoundError(f"Could not open video file at: {video_path}")

        warnings: List[str] = []
        errors: List[str] = []

        try:
            raw_fps = cap.get(cv2.CAP_PROP_FPS)
            if config.source_fps_override is not None:
                src_fps = float(config.source_fps_override)
                warnings.append(f"source fps overridden: container reported {raw_fps}")
            elif raw_fps <= 0 or math.isnan(raw_fps) or math.isinf(raw_fps) or raw_fps > 120.0:
                src_fps = 25.0
                warnings.append(f"Invalid source FPS ({raw_fps}); defaulted to 25.0")
            else:
                src_fps = float(raw_fps)

            process_fps = config.process_fps if config.process_fps > 0 else 5.0
            stride = max(1, round(src_fps / process_fps))

            base_dt = parse_utc_timestamp(config.base_time_utc)

            tracker.reset()

            twin_engine = getattr(self.pipeline, "twin_engine", None)
            rule_engine = getattr(self.pipeline, "rule_engine", None)
            last_frame_dt = None

            frames_read = 0
            frames_processed = 0
            observations_count = 0
            unique_tracks = set()
            events_emitted = 0
            event_type_counts: Dict[str, int] = {}
            incident_ids: List[str] = []

            decode_times_ms: List[float] = []
            track_times_ms: List[float] = []
            ingest_times_ms: List[float] = []

            status = "completed"

            while True:
                if cancel_event is not None and cancel_event.is_set():
                    status = "cancelled"
                    break

                if config.max_frames is not None and frames_read >= config.max_frames:
                    break

                # Frame index before reading this frame
                frame_idx = frames_read
                is_target_frame = (frame_idx % stride == 0)

                t_read_0 = time.perf_counter()
                ret, frame = cap.read()
                t_read_1 = time.perf_counter()

                if not ret or frame is None:
                    break

                frames_read += 1

                if not is_target_frame:
                    continue

                decode_ms = (t_read_1 - t_read_0) * 1000.0
                decode_times_ms.append(decode_ms)
                frames_processed += 1

                # Frame timestamp in event time (never wall clock)
                offset_sec = frame_idx / src_fps
                frame_dt = base_dt + timedelta(seconds=offset_sec)
                frame_iso = frame_dt.isoformat().replace("+00:00", "Z")

                # Tracker update
                t_track_0 = time.perf_counter()
                detections = tracker.update(frame)
                t_track_1 = time.perf_counter()
                track_ms = (t_track_1 - t_track_0) * 1000.0
                track_times_ms.append(track_ms)

                # Ingestion
                t_ingest_0 = time.perf_counter()
                frame_h, frame_w = frame.shape[:2]
                res_w = float(cam_node.resolution_width)
                res_h = float(cam_node.resolution_height)
                scale_x = res_w / frame_w if frame_w > 0 else 1.0
                scale_y = res_h / frame_h if frame_h > 0 else 1.0

                for det in detections:
                    # 1. Clamp bbox to frame
                    u_min_clamped = max(0.0, min(float(frame_w), det.bbox.u_min))
                    u_max_clamped = max(0.0, min(float(frame_w), det.bbox.u_max))
                    v_min_clamped = max(0.0, min(float(frame_h), det.bbox.v_min))
                    v_max_clamped = max(0.0, min(float(frame_h), det.bbox.v_max))
                    if u_max_clamped < u_min_clamped:
                        u_max_clamped = u_min_clamped
                    if v_max_clamped < v_min_clamped:
                        v_max_clamped = v_min_clamped

                    # 2. Scale bbox to camera resolution
                    scaled_bbox = BoundingBox2D(
                        u_min=u_min_clamped * scale_x,
                        v_min=v_min_clamped * scale_y,
                        u_max=u_max_clamped * scale_x,
                        v_max=v_max_clamped * scale_y,
                    )

                    track_id = f"{config.name}:{run_id}:{det.local_track_id}"
                    obs = CameraObservation(
                        camera_id=config.camera_id,
                        track_id=track_id,
                        bbox=scaled_bbox,
                        confidence=det.confidence,
                        timestamp=frame_iso,
                        person_id=None,
                    )

                    try:
                        twin_obs = self.pipeline.process_observation(obs)
                        observations_count += 1
                        unique_tracks.add(track_id)
                        events_emitted += len(twin_obs.emitted_events)
                        for evt in twin_obs.emitted_events:
                            event_type_counts[evt.event_type] = (
                                event_type_counts.get(evt.event_type, 0) + 1
                            )
                        for inc in twin_obs.raised_incidents:
                            if inc.incident_id not in incident_ids:
                                incident_ids.append(inc.incident_id)
                    except Exception as exc:
                        if len(errors) < 50:
                            errors.append(
                                f"Observation error at frame {frame_idx}, track {track_id}: {exc}"
                            )

                t_ingest_1 = time.perf_counter()
                ingest_ms = (t_ingest_1 - t_ingest_0) * 1000.0
                ingest_times_ms.append(ingest_ms)

                last_frame_dt = frame_dt

                # Event-time track pruning per processed frame
                if twin_engine is not None:
                    with TWIN_WRITE_LOCK:
                        lost = twin_engine.prune_tracks(
                            now_iso=frame_iso,
                            ttl_seconds=config.track_exit_seconds,
                            track_id_prefix=own_track_prefix,
                        )
                        if lost:
                            events_emitted += len(lost)
                            for evt in lost:
                                event_type_counts[evt.event_type] = (
                                    event_type_counts.get(evt.event_type, 0) + 1
                                )
                            if rule_engine is not None:
                                lost_incidents = rule_engine.evaluate(lost)
                                for inc in lost_incidents:
                                    if inc.incident_id not in incident_ids:
                                        incident_ids.append(inc.incident_id)

                if progress_cb is not None:
                    try:
                        progress_cb(frames_read)
                    except Exception as cb_exc:
                        logger.warning("Progress callback raised: %s", cb_exc)

            # Final event-time prune after last frame so no clip tracks remain active
            if twin_engine is not None:
                ref_dt = last_frame_dt if last_frame_dt is not None else base_dt
                final_dt = ref_dt + timedelta(seconds=config.track_exit_seconds + 1.0)
                final_iso = final_dt.isoformat().replace("+00:00", "Z")
                with TWIN_WRITE_LOCK:
                    final_lost = twin_engine.prune_tracks(
                        now_iso=final_iso,
                        ttl_seconds=config.track_exit_seconds,
                        track_id_prefix=own_track_prefix,
                    )
                    if final_lost:
                        events_emitted += len(final_lost)
                        for evt in final_lost:
                            event_type_counts[evt.event_type] = (
                                event_type_counts.get(evt.event_type, 0) + 1
                            )
                        if rule_engine is not None:
                            final_incidents = rule_engine.evaluate(final_lost)
                            for inc in final_incidents:
                                if inc.incident_id not in incident_ids:
                                    incident_ids.append(inc.incident_id)

            wall_end = time.perf_counter()
            wall_seconds = round(wall_end - wall_start, 4)
            effective_fps = (
                round(frames_processed / wall_seconds, 2) if wall_seconds > 0 else 0.0
            )

            timings = {
                "decode_ms": _calc_stats(decode_times_ms),
                "track_ms": _calc_stats(track_times_ms),
                "ingest_ms": _calc_stats(ingest_times_ms),
            }

            return ClipRunResult(
                run_id=run_id,
                clip_name=config.name,
                camera_id=config.camera_id,
                status=status,
                frames_read=frames_read,
                frames_processed=frames_processed,
                source_fps=round(src_fps, 2),
                stride=stride,
                observations=observations_count,
                unique_tracks=len(unique_tracks),
                events_emitted=events_emitted,
                event_type_counts=event_type_counts,
                incident_ids=incident_ids,
                timings=timings,
                wall_seconds=wall_seconds,
                effective_fps=effective_fps,
                errors=errors,
                warnings=warnings,
            )
        finally:
            cap.release()
