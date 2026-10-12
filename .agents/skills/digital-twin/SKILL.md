---
name: digital-twin
description: Maintain a deterministic Digital Twin model for office zones, people, cameras, events, incidents, and current state.
---
# Digital Twin Engineering

The Digital Twin is a structured representation of the office, not an LLM memory.

## Required separation

- Historical events: append-only facts.
- Current state: deterministic derived state.
- Incidents: deterministic detections with evidence references.
- AI: read-only reasoning over retrieved evidence.

## Integrity rules

- Every incident must reference supporting event IDs where applicable.
- State changes must be traceable to input events.
- Synthetic identities are allowed for the POC.
- Do not introduce biometric templates or face embeddings.
- Do not infer employee performance or intent from location telemetry.

## Change discipline

Prefer additive schema/interface changes. Preserve existing demo scenarios until migration is explicitly approved.
