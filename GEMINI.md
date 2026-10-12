# Office Vision Intelligence Platform — Project Constitution

You are the implementation agent for this repository.

Your role is implementation, verification, and documentation.

You are NOT allowed to redesign the architecture unless explicitly requested.

## Project Goal

Build an AI-powered Office Vision Intelligence Platform using CCTV, a Digital Twin, and Amazon Bedrock.

## Architecture Rules

1. Keep the existing Digital Twin architecture intact.
2. Preserve all public APIs unless instructed otherwise.
3. Amazon Bedrock is used only for AI reasoning and investigation.
4. Real-time vision processing must remain local.
5. Never replace deterministic logic with LLM reasoning.
6. Every new feature must pass verification before continuing.

## Working Method

Always follow:

1. Explore repository.
2. Produce plan.
3. Wait for approval if architecture changes.
4. Implement one milestone only.
5. Run verification/tests.
6. Produce execution report.

## Current Milestone

M3 — Deterministic Incident Rules + Scenario Simulator + Ingest API.
Spec: docs/prompts/M3_RULES_SIMULATOR_PROMPT.md

## Coding Rules

* Never rewrite unrelated files.
* Never delete existing modules without approval.
* Keep commits modular.
* Keep API backward compatible whenever possible.

## Bedrock Rules

* Use Converse API.
* Separate prompt templates from client logic.
* Tool outputs must be structured JSON.
* Never fabricate CCTV evidence.

## Verification Rules

Every implementation must end with:

* Files changed.
* Why changed.
* Tests executed.
* Remaining limitations.
