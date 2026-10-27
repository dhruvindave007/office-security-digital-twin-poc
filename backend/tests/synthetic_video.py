# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
from typing import Tuple
import numpy as np


def generate_synthetic_video(
    output_dir: Path,
    stem: str = "test_clip",
    duration_sec: float = 3.0,
    fps: float = 10.0,
    width: int = 320,
    height: int = 240,
) -> Tuple[Path, str]:
    """Generate a synthetic evaluation video clip in the specified directory.

    Attempts 'mp4v' (.mp4) first; falls back to 'MJPG' (.avi) if 'mp4v' is unavailable.
    Returns (video_path, codec_used).
    """
    import cv2

    num_frames = int(duration_sec * fps)
    target_mp4 = output_dir / f"{stem}.mp4"

    # Try mp4v first
    fourcc_mp4 = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(target_mp4), fourcc_mp4, fps, (width, height))

    codec_used = "mp4v"
    video_path = target_mp4

    if not writer.isOpened():
        # Fallback to MJPG .avi
        target_avi = output_dir / f"{stem}.avi"
        fourcc_avi = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(str(target_avi), fourcc_avi, fps, (width, height))
        codec_used = "MJPG"
        video_path = target_avi

    if not writer.isOpened():
        raise RuntimeError("Failed to open cv2.VideoWriter with both mp4v and MJPG codecs")

    try:
        for i in range(num_frames):
            # Create a simple frame with changing color intensity per frame
            val = int((i * 15) % 255)
            frame = np.full((height, width, 3), val, dtype=np.uint8)
            writer.write(frame)
    finally:
        writer.release()

    return video_path, codec_used
