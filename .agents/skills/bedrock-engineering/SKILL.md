---
name: bedrock-engineering
description: Build and verify Amazon Bedrock integrations with evidence-grounded tool use and current AWS documentation.
---
# Bedrock Engineering

Use when implementing or researching Amazon Bedrock.

## Required workflow

1. Verify current AWS documentation for the API/model/service in question.
2. Verify region and model availability instead of guessing.
3. Prefer the current Bedrock Runtime interface appropriate to the use case.
4. Separate the Bedrock adapter from deterministic Digital Twin logic.
5. Keep tool interfaces structured and read-only unless explicit write actions are required.
6. Use evidence-first prompts: facts come from tool outputs, not model memory.
7. Capture model ID, region, request mode, and test outcome.
8. Never hard-code secrets.
9. Never claim a Bedrock call worked unless it actually succeeded.

## Office Vision rule

Bedrock must not be placed in the frame-by-frame CCTV latency path. Use asynchronous event-triggered investigations and/or short relevant media windows.
