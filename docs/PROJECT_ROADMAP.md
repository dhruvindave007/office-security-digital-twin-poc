# Office Vision Intelligence Platform — Agent Roadmap

## Current goal

Evolve the existing Office Security Digital Twin POC into a CCTV-first Office Vision Intelligence POC with Amazon Bedrock used for asynchronous investigation and multimodal reasoning.

## Approved architecture direction

CCTV / simulated video
→ low-latency vision processing
→ Digital Twin state
→ local/event intelligence
→ relevant event window
→ Amazon Bedrock investigation
→ evidence-backed answer
→ dashboard

## Phase sequence

### Phase 0 — Repository and architecture audit
No code changes. Confirm current code, constraints, and the smallest migration path.

### Phase 1 — Bedrock foundation
Verify AWS credentials/region/model access, implement a minimal Bedrock client, and prove one real model call.

### Phase 2 — Vision event engine
Introduce or evolve the vision processing path. Keep it separate from Bedrock.

### Phase 3 — Digital Twin integration
Map vision events into the current Twin model without losing existing demo behavior.

### Phase 4 — Event-triggered Bedrock investigation
Only send high-value context/media windows to Bedrock. Measure latency.

### Phase 5 — AI Office Copilot
Ask Camera, Investigate Incident, Cross-camera Story, Daily Summary.

### Phase 6 — Demo hardening
Tests, latency measurements, error handling, evidence display, manager demo.

## Non-goals for the POC

- full production CCTV infrastructure;
- face recognition or biometric template storage;
- production-scale streaming architecture;
- unrestricted AWS resource mutation;
- claims about employee intent, productivity, or performance based on video alone.
