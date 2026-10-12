from src.twin.telemetry import TwinTelemetry


def test_twin_telemetry_counters():
    """Verify telemetry records ingestion events, latency, and computes rate."""
    telemetry = TwinTelemetry(rate_window_seconds=10.0)

    telemetry.record_ingest(event_count=3, latency_ms=12.5)
    telemetry.record_ingest(event_count=2, latency_ms=8.0)
    telemetry.record_snapshot()

    metrics = telemetry.get_metrics(
        active_tracks_count=5,
        zone_occupancies={"zone_reception": 2, "zone_workspace": 3},
    )

    assert metrics["total_events_ingested"] == 5
    assert metrics["snapshot_count"] == 1
    assert metrics["last_ingest_latency_ms"] == 8.0
    assert metrics["active_tracks_count"] == 5
    assert metrics["zone_occupancies"]["zone_reception"] == 2
    assert metrics["event_rate_per_sec"] > 0
    assert "timestamp" in metrics
