from pathlib import Path
import pytest
import yaml

from src.vision.clips import ClipConfig, list_clip_configs, load_clip_config


def test_clip_config_valid(tmp_path: Path):
    """Test loading a valid ClipConfig yaml."""
    clip_file = tmp_path / "lobby_walk.yaml"
    data = {
        "name": "lobby_walk",
        "description": "Lobby test walkthrough",
        "video_file": "lobby_walk.mp4",
        "camera_id": "cam_reception_01",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "process_fps": 5.0,
        "max_frames": 100,
        "source_note": "Internal consented test recording",
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    cfg = load_clip_config(clip_file)
    assert cfg.name == "lobby_walk"
    assert cfg.description == "Lobby test walkthrough"
    assert cfg.video_file == "lobby_walk.mp4"
    assert cfg.camera_id == "cam_reception_01"
    assert cfg.base_time_utc == "2026-01-20T09:30:00Z"
    assert cfg.process_fps == 5.0
    assert cfg.max_frames == 100
    assert cfg.source_note == "Internal consented test recording"


def test_clip_config_stem_mismatch(tmp_path: Path):
    """Test that ClipConfig raises ValueError when name does not match file stem."""
    clip_file = tmp_path / "different_stem.yaml"
    data = {
        "name": "actual_name",
        "video_file": "video.mp4",
        "camera_id": "cam_01",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "source_note": "consent",
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match file stem"):
        load_clip_config(clip_file)


def test_clip_config_empty_camera_id(tmp_path: Path):
    """Test that empty camera_id raises ValueError."""
    clip_file = tmp_path / "clip.yaml"
    data = {
        "name": "clip",
        "video_file": "video.mp4",
        "camera_id": "",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "source_note": "consent",
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="'camera_id' cannot be empty"):
        load_clip_config(clip_file)


def test_clip_config_empty_source_note(tmp_path: Path):
    """Test that empty source_note raises ValueError."""
    clip_file = tmp_path / "clip.yaml"
    data = {
        "name": "clip",
        "video_file": "video.mp4",
        "camera_id": "cam_01",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "source_note": "",
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="'source_note' cannot be empty"):
        load_clip_config(clip_file)


def test_clip_config_invalid_process_fps(tmp_path: Path):
    """Test that non-positive process_fps raises ValueError."""
    clip_file = tmp_path / "clip.yaml"
    data = {
        "name": "clip",
        "video_file": "video.mp4",
        "camera_id": "cam_01",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "source_note": "consent",
        "process_fps": 0.0,
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="'process_fps' must be > 0"):
        load_clip_config(clip_file)


def test_clip_config_empty_video_file(tmp_path: Path):
    """Test that empty video_file raises ValueError."""
    clip_file = tmp_path / "clip.yaml"
    data = {
        "name": "clip",
        "video_file": "   ",
        "camera_id": "cam_01",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "source_note": "consent",
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="'video_file' cannot be empty"):
        load_clip_config(clip_file)


def test_clip_config_invalid_base_time_utc(tmp_path: Path):
    """Test that malformed base_time_utc raises ValueError."""
    clip_file = tmp_path / "clip.yaml"
    data = {
        "name": "clip",
        "video_file": "video.mp4",
        "camera_id": "cam_01",
        "base_time_utc": "invalid-timestamp",
        "source_note": "consent",
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid 'base_time_utc'"):
        load_clip_config(clip_file)


def test_clip_config_file_not_found(tmp_path: Path):
    """Test that loading a non-existent config file raises FileNotFoundError."""
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        load_clip_config(missing)


def test_list_clip_configs(tmp_path: Path):
    """Test discovery and sorting of multiple ClipConfig manifests."""
    # Write two valid configs
    for name in ["b_clip", "a_clip"]:
        cfg_file = tmp_path / f"{name}.yaml"
        data = {
            "name": name,
            "video_file": f"{name}.mp4",
            "camera_id": "cam_reception_01",
            "base_time_utc": "2026-01-20T09:30:00Z",
            "source_note": "consent",
        }
        cfg_file.write_text(yaml.dump(data), encoding="utf-8")

    # Write an invalid file that should be skipped
    invalid_file = tmp_path / "bad.yaml"
    invalid_file.write_text("invalid: [not a valid config]", encoding="utf-8")

    configs = list_clip_configs(tmp_path)
    assert len(configs) == 2
    assert configs[0].name == "a_clip"
    assert configs[1].name == "b_clip"

    # Nonexistent dir returns empty list
    assert list_clip_configs(tmp_path / "nonexistent") == []


def test_clip_config_source_fps_override(tmp_path: Path):
    """Test valid source_fps_override in ClipConfig and to_dict."""
    clip_file = tmp_path / "override_clip.yaml"
    data = {
        "name": "override_clip",
        "video_file": "override_clip.mp4",
        "camera_id": "cam_reception_01",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "source_note": "consent",
        "source_fps_override": 25.0,
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    cfg = load_clip_config(clip_file)
    assert cfg.source_fps_override == 25.0
    cfg_dict = cfg.to_dict()
    assert cfg_dict["source_fps_override"] == 25.0


def test_clip_config_invalid_source_fps_override(tmp_path: Path):
    """Test that source_fps_override <= 0 raises ValueError."""
    clip_file = tmp_path / "bad_override.yaml"
    data = {
        "name": "bad_override",
        "video_file": "video.mp4",
        "camera_id": "cam_01",
        "base_time_utc": "2026-01-20T09:30:00Z",
        "source_note": "consent",
        "source_fps_override": -5.0,
    }
    clip_file.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError, match="'source_fps_override' must be > 0"):
        load_clip_config(clip_file)


def test_list_clip_configs_loads_all_reviewer_clips():
    """Verify list_clip_configs loads all 4 reviewer manifests in data/clips without error."""
    from src.core.paths import _resolve_data_path
    clips_dir = _resolve_data_path(None, "data/clips")
    configs = list_clip_configs(clips_dir)
    names = [c.name for c in configs]
    assert "ljubljana_after_hours" in names
    assert "ljubljana_reception" in names
    assert "ljubljana_server_room" in names
    assert "pedestrian_area_cc0" in names

