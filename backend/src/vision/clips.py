from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import yaml

try:
    from src.rules.policy import parse_utc_timestamp
except ImportError:
    from rules.policy import parse_utc_timestamp


@dataclass(frozen=True)
class ClipConfig:
    """Configuration for playing a recorded CCTV video clip through the Digital Twin."""
    name: str
    video_file: str
    camera_id: str
    base_time_utc: str
    source_note: str
    description: str = ""
    process_fps: float = 5.0
    max_frames: Optional[int] = None
    source_fps_override: Optional[float] = None
    track_exit_seconds: float = 2.0

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("ClipConfig 'name' cannot be empty.")
        if not self.video_file or not self.video_file.strip():
            raise ValueError("ClipConfig 'video_file' cannot be empty.")
        if not self.camera_id or not self.camera_id.strip():
            raise ValueError("ClipConfig 'camera_id' cannot be empty.")
        if not self.source_note or not self.source_note.strip():
            raise ValueError("ClipConfig 'source_note' cannot be empty.")
        if self.process_fps is None or self.process_fps <= 0:
            raise ValueError("ClipConfig 'process_fps' must be > 0.")
        if self.max_frames is not None and self.max_frames <= 0:
            raise ValueError("ClipConfig 'max_frames' must be > 0 if specified.")
        if self.source_fps_override is not None and self.source_fps_override <= 0:
            raise ValueError("ClipConfig 'source_fps_override' must be > 0 if set.")
        if self.track_exit_seconds is None or self.track_exit_seconds <= 0:
            raise ValueError("ClipConfig 'track_exit_seconds' must be > 0.")
        if not self.base_time_utc or not self.base_time_utc.strip():
            raise ValueError("ClipConfig 'base_time_utc' cannot be empty.")

        # Ensure base_time_utc is valid ISO UTC timestamp
        try:
            parse_utc_timestamp(self.base_time_utc)
        except Exception as exc:
            raise ValueError(f"Invalid 'base_time_utc' format: {self.base_time_utc}") from exc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "video_file": self.video_file,
            "camera_id": self.camera_id,
            "base_time_utc": self.base_time_utc,
            "process_fps": self.process_fps,
            "max_frames": self.max_frames,
            "source_fps_override": self.source_fps_override,
            "track_exit_seconds": self.track_exit_seconds,
            "source_note": self.source_note,
        }


def load_clip_config(path: Union[str, Path]) -> ClipConfig:
    """Load and validate a ClipConfig from a YAML file.

    Enforces that config 'name' matches the filename stem, camera_id and source_note
    are non-empty, and process_fps > 0.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Clip config file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML content in '{file_path}'; expected a dictionary.")

    name = data.get("name")
    expected_stem = file_path.stem
    if name != expected_stem:
        raise ValueError(
            f"ClipConfig name '{name}' does not match file stem '{expected_stem}'."
        )

    fps_override_raw = data.get("source_fps_override")
    source_fps_override = (
        float(fps_override_raw) if fps_override_raw is not None else None
    )

    track_exit_raw = data.get("track_exit_seconds")
    track_exit_seconds = (
        float(track_exit_raw) if track_exit_raw is not None else 2.0
    )

    return ClipConfig(
        name=name,
        description=data.get("description", ""),
        video_file=data.get("video_file", ""),
        camera_id=data.get("camera_id", ""),
        base_time_utc=data.get("base_time_utc", ""),
        process_fps=float(data.get("process_fps", 5.0)),
        max_frames=int(data["max_frames"]) if data.get("max_frames") is not None else None,
        source_fps_override=source_fps_override,
        track_exit_seconds=track_exit_seconds,
        source_note=data.get("source_note", ""),
    )


def list_clip_configs(clips_dir: Union[str, Path]) -> List[ClipConfig]:
    """Discover and load all valid ClipConfig files in the specified directory."""
    dir_path = Path(clips_dir)
    if not dir_path.exists() or not dir_path.is_dir():
        return []

    configs: List[ClipConfig] = []
    # Collect all yaml and yml files
    found_files = sorted(set(list(dir_path.glob("*.yaml")) + list(dir_path.glob("*.yml"))))
    for file in found_files:
        try:
            cfg = load_clip_config(file)
            configs.append(cfg)
        except Exception:
            continue

    configs.sort(key=lambda c: c.name)
    return configs
