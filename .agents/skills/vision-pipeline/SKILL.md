---
name: vision-pipeline
description: Design and implement low-latency CCTV/vision event pipelines while keeping cloud AI off the frame-by-frame control loop.
---
# Vision Pipeline

Use for camera ingestion, detection, tracking, zone mapping, event generation, or latency work.

## Fast path

CCTV → vision processing → tracking → zone state → Digital Twin → local event rules.

## Slow path

Interesting event → relevant context/media window → Bedrock investigation → AI result.

## Rules

- Never send the entire continuous camera stream to Bedrock by default.
- Measure capture, inference, state-update, and cloud round-trip latency separately.
- Prefer event windows over whole-day recordings.
- Treat identity as a separate problem from person detection.
- Use track IDs in the POC unless an approved identity source exists.
- Never claim absence, intent, or productivity from a camera feed without supporting evidence.
