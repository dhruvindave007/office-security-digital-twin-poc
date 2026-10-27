# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only description of the running site for the operator dashboard: GET /api/site

Everything comes from the files the backend already runs on (twin camera table, access-control
doors file, live camera config + calibration). Nothing is inferred: camera mounting positions are
not in the site data, so cameras are reported by the room they cover, plus how they position people.
"""
from pathlib import Path
from typing import Any, Dict, List

import yaml
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api", tags=["Site"])


def _settings(request: Request):
    try:
        from src.config import get_settings
    except ImportError:
        from config import get_settings
    return get_settings()


@router.get("/site")
def get_site(request: Request) -> Dict[str, Any]:
    engine = getattr(request.app.state, "twin_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Digital Twin engine is not initialized.")
    settings = _settings(request)
    try:
        from src.runtime import _site_camera_modes
    except ImportError:
        from runtime import _site_camera_modes
    modes = _site_camera_modes(settings)

    live_cams: Dict[str, Dict[str, Any]] = {}
    if settings.live_config_path:
        try:
            try:
                from src.live.config import load_live_config
            except ImportError:
                from live.config import load_live_config
            for c in load_live_config(settings.live_config_path).cameras:
                live_cams[c.camera_id] = {"feed_twin": c.feed_twin, "detect": c.detect}
        except Exception:
            live_cams = {}

    cameras: List[Dict[str, Any]] = []
    for cam in engine.store.get_cameras():
        cameras.append({
            "camera_id": cam.camera_id,
            "name": cam.name,
            "zone_id": cam.primary_zone_id,
            "positioning": modes.get(cam.camera_id),          # metric | room | None (not calibrated)
            "in_live_config": cam.camera_id in live_cams,
            "feeds_twin": live_cams.get(cam.camera_id, {}).get("feed_twin"),
        })

    doors: List[Dict[str, Any]] = []
    path = Path(settings.access_config_path)
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for d in raw.get("doors", []) or []:
            doors.append({
                "door_id": d.get("door_id"), "name": d.get("name"), "zone_id": d.get("zone_id"),
                "camera_id": d.get("camera_id"), "badge_required": bool(d.get("badge_required", False)),
                "jambs_m": d.get("jambs_m"), "connects": d.get("connects"),
            })

    return {
        "name": settings.site_name,
        "floorplan_seed": Path(settings.twin_floorplan_seed_path).parent.name or None,
        "live_config": Path(settings.live_config_path).name if settings.live_config_path else None,
        "cameras": cameras,
        "doors": doors,
        "notes": ["Camera mounting positions are not part of the site data; cameras are shown by the room they cover.",
                  "Track IDs are per camera. The system does not match people across cameras."],
    }
