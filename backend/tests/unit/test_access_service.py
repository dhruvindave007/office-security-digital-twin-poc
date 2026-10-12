import json
import pytest

from src.access.config import AccessConfig, Door
from src.access.service import AccessControlService
from src.twin.engine import DigitalTwinEngine
from src.twin.geometry import GeometryEngine
from src.twin.models import Camera, Zone
from src.twin.state import TwinState
from src.twin.store import TwinStore


def _setup_service():
    store = TwinStore(":memory:")
    store.insert_zone(
        Zone(zone_id="zone_server_room", name="Server Room", zone_type="secure", security_level=4)
    )
    store.insert_camera(
        Camera(camera_id="cam_server_01", name="Server Cam", primary_zone_id="zone_server_room")
    )
    config = AccessConfig(
        doors={
            "door_server_01": Door(
                door_id="door_server_01",
                name="Server Door",
                zone_id="zone_server_room",
                camera_id="cam_server_01",
                badge_required=True,
            )
        },
        badges={
            "badge_0001": "person_it_admin_01",
        },
    )
    service = AccessControlService(store=store, config=config)
    return service, store


def test_ingest_granted_and_denied_stored():
    """Verify GRANTED and DENIED events are stored in ledger with correct fields."""
    service, store = _setup_service()

    # 1. Ingest GRANTED
    evt_granted, incs = service.ingest(
        door_id="door_server_01",
        badge_id="badge_0001",
        result="GRANTED",
        timestamp="2026-01-15T10:00:00Z",
    )
    assert evt_granted.event_type == "ACCESS_GRANTED"
    assert evt_granted.track_id == "badge:badge_0001"
    assert evt_granted.person_id == "person_it_admin_01"
    assert evt_granted.zone_id == "zone_server_room"
    assert evt_granted.camera_id == "cam_server_01"
    assert evt_granted.confidence == 1.0
    assert evt_granted.sequence_num == 1
    assert incs == []

    payload = json.loads(evt_granted.payload_json)
    assert payload["door_id"] == "door_server_01"
    assert payload["badge_id"] == "badge_0001"
    assert payload["result"] == "GRANTED"

    # 2. Ingest DENIED
    evt_denied, _ = service.ingest(
        door_id="door_server_01",
        badge_id="badge_0001",
        result="DENIED",
        timestamp="2026-01-15T10:01:00Z",
    )
    assert evt_denied.event_type == "ACCESS_DENIED"
    assert evt_denied.sequence_num == 2


def test_ingest_unknown_badge_gives_person_id_none():
    """Verify unknown badge ID yields person_id None without error."""
    service, _ = _setup_service()
    evt, _ = service.ingest(
        door_id="door_server_01",
        badge_id="badge_unknown_9999",
        result="GRANTED",
        timestamp="2026-01-15T10:00:00Z",
    )
    assert evt.person_id is None
    assert evt.track_id == "badge:badge_unknown_9999"


def test_ingest_invalid_result_raises_value_error():
    """Verify invalid result string raises ValueError."""
    service, _ = _setup_service()
    with pytest.raises(ValueError, match=r"'GRANTED' or 'DENIED'"):
        service.ingest(
            door_id="door_server_01",
            badge_id="badge_0001",
            result="PENDING",
            timestamp="2026-01-15T10:00:00Z",
        )


def test_ingest_unknown_door_raises_key_error():
    """Verify unknown door_id raises KeyError."""
    service, _ = _setup_service()
    with pytest.raises(KeyError, match="door_unknown"):
        service.ingest(
            door_id="door_unknown",
            badge_id="badge_0001",
            result="GRANTED",
            timestamp="2026-01-15T10:00:00Z",
        )


def test_ingest_leaves_twin_state_unchanged():
    """Verify that badge ingestion does NOT create active tracks in TwinState."""
    service, store = _setup_service()
    geom = GeometryEngine([], {})
    state = TwinState()
    _engine = DigitalTwinEngine(store=store, state=state, geometry=geom)

    # Ingest badge swipe
    service.ingest(
        door_id="door_server_01",
        badge_id="badge_0001",
        result="GRANTED",
        timestamp="2026-01-15T10:00:00Z",
    )

    # State should remain completely empty of active tracks
    assert state.get_active_tracks() == []
    assert state.get_all_zone_occupancies() == {}
