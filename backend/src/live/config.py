"""Live camera configuration (one YAML file per site, e.g. data/sites/wisenet/live.yaml)."""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class LiveCameraConfig:
    camera_id: str
    url: str                      # rtsp://..., http://... (MJPEG/HLS), or a video file path
    feed_twin: bool = True        # False: detect + show only (e.g. a second camera on the same room)
    detect: bool = True           # False: show the picture only, no inference
    device: Optional[str] = None  # e.g. intel:gpu / intel:npu / cpu; None -> settings.vision_device
    model: Optional[str] = None   # None -> settings.vision_model
    realtime: bool = True         # files only: pace reads at the file's frame rate (behave like a camera)
    loop: bool = True             # files only: restart at the end


@dataclass
class LiveConfig:
    cameras: List[LiveCameraConfig]
    process_fps: float = 5.0          # detections per second per camera
    track_exit_seconds: float = 2.0   # a person unseen this long has left the camera's view
    calibration_path: Optional[str] = None
    # Badge-door entry gates per camera: {camera_id: {roi: [x,y,w,h], image_size: [w,h], margin_frac, min_sightings}}
    entry_gates: Dict[str, Dict] = field(default_factory=dict)
    # Demo only: badge swipes replayed in step with a replayed video (seconds after each loop start):
    # {camera_id: cam_3, swipes: [{offset_s: 20.2, door_id: door_d4, badge_id: badge_1003, result: GRANTED}]}
    badge_replay: Optional[Dict] = None
    base_dir: Path = field(default_factory=Path)

    def camera(self, camera_id: str) -> Optional[LiveCameraConfig]:
        return next((c for c in self.cameras if c.camera_id == camera_id), None)


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(url: str, where: str) -> str:
    """Replace ${NAME} with the environment variable NAME, so camera passwords stay out of config files."""
    def sub(m: "re.Match[str]") -> str:
        val = os.environ.get(m.group(1))
        if val is None:
            raise ValueError(f"{where}: environment variable {m.group(1)} is not set")
        return val
    return _ENV_REF.sub(sub, url)


def load_live_config(path: str) -> LiveConfig:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cams = []
    for c in raw.get("cameras", []):
        url = _expand_env(str(c["url"]), f"{path}: camera {c.get('camera_id')}")
        if "://" not in url and not Path(url).is_absolute():  # relative file paths are relative to the YAML
            url = str((p.parent / url).resolve())
        cams.append(LiveCameraConfig(**{**c, "url": url}))
    if len({c.camera_id for c in cams}) != len(cams):
        raise ValueError(f"{path}: duplicate camera_id")
    cal = raw.get("calibration_path")
    if cal and not Path(cal).is_absolute():
        cal = str((p.parent / cal).resolve())
    return LiveConfig(cameras=cams, process_fps=float(raw.get("process_fps", 5.0)),
                      track_exit_seconds=float(raw.get("track_exit_seconds", 2.0)),
                      calibration_path=cal, entry_gates=dict(raw.get("entry_gates") or {}),
                      badge_replay=raw.get("badge_replay"), base_dir=p.parent)
