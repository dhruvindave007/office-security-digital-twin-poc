"""Position sampling: fewer stored PERSON_DETECTED rows, transitions always stored, cited samples never lost."""
import json

from src.rules.engine import IncidentRuleEngine
from src.rules.policy import AccessPolicy, RestrictedZonePolicy
from src.twin.engine import DigitalTwinEngine
from src.twin.models import Camera, Zone, ZoneVertex
from src.twin.store import TwinStore


def _engine(sample):
    store = TwinStore(":memory:")
    store.insert_zone(Zone(zone_id="z", name="Z", zone_type="restricted", security_level=3))
    store.insert_vertices([ZoneVertex(zone_id="z", vertex_order=i, x=x, y=y) for i, (x, y) in enumerate([(0, 0), (10, 0), (10, 10), (0, 10)])])
    store.insert_camera(Camera(camera_id="c", name="C", primary_zone_id="z"))
    return DigitalTwinEngine(store=store, position_sample_seconds=sample), store


def _count(store, etype):
    return sum(1 for e in store.get_events_since(0) if e.event_type == etype)


def test_samples_stored_once_per_interval_and_transitions_always():
    eng, store = _engine(1.0)
    for i in range(25):                                    # 5 s at 5 fps inside the zone
        eng.ingest_observation("t1", "c", 5, 5, f"2026-01-15T10:00:{i // 5:02d}.{(i % 5) * 2}00000Z")
    eng.ingest_observation("t1", "c", 20, 20, "2026-01-15T10:00:05.100000Z")   # leaves the zone
    assert _count(store, "PERSON_DETECTED") == 6           # 10:00:00..04 once each + the exit sample
    assert _count(store, "ZONE_ENTERED") == 1 and _count(store, "ZONE_EXITED") == 1


def test_default_keeps_every_detection():
    eng, store = _engine(0.0)
    for i in range(10):
        eng.ingest_observation("t1", "c", 5, 5, f"2026-01-15T10:00:00.{i}00000Z")
    assert _count(store, "PERSON_DETECTED") == 10


def test_cited_skipped_sample_is_written_with_the_incident():
    """After-hours rule cites every sighting, including ones the sampler skipped: all must be stored."""
    eng, store = _engine(1.0)
    rules = IncidentRuleEngine(store, eng.state, AccessPolicy(version=1, restricted_zones={}))
    incidents = []
    for i in range(10):                                    # 02:00 UTC is outside business hours
        incidents += rules.evaluate(eng.ingest_observation("a", "c", 5, 5, f"2026-01-15T02:00:00.{i}00000Z"))
    assert incidents
    stored = {e.event_id for e in store.get_events_since(0)}
    cited = json.loads(store.get_incident(incidents[-1].incident_id).supporting_event_ids_json)
    assert len(cited) == 10 and set(cited) <= stored


def test_open_incident_evidence_list_is_bounded():
    eng, store = _engine(0.0)
    rules = IncidentRuleEngine(store, eng.state, AccessPolicy(version=1, restricted_zones={}))
    first = None
    for i in range(200):
        out = rules.evaluate(eng.ingest_observation("a", "c", 5, 5, f"2026-01-15T02:{i // 60:02d}:{i % 60:02d}Z"))
        first = first or json.loads(out[0].supporting_event_ids_json)[0]
    ids = json.loads(store.get_incident(out[0].incident_id).supporting_event_ids_json)
    assert len(ids) == 50 and ids[0] == first             # start of the incident kept, then the latest 30
