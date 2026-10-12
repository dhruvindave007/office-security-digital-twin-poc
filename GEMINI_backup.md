# Office Vision Intelligence Platform — Antigravity Project Charter

This repository is an internal AI/engineering POC. Treat this file as a hard workspace contract.

## Mission

Evolve the existing Office Security Digital Twin POC into an Office Vision Intelligence Platform centered on CCTV-derived office state and Amazon Bedrock investigation. Do not restart the repository without explicit approval.

## Current canonical codebase

The existing repository is the source of truth. Preserve working behavior unless a task explicitly changes it. The current implementation contains a C# backend, React dashboard, simulator, tool layer, and a separate Python Bedrock prototype. Do not silently choose one implementation or delete the other; first produce a migration/cleanup plan and obtain approval through the task specification.

## Architecture principles

1. Digital Twin state is deterministic and is the source of truth.
2. CCTV/vision processing is the primary sensor path. Biometrics are optional context, not the core product dependency.
3. Real-time processing must not block on Bedrock.
4. Bedrock is an asynchronous intelligence/investigation layer, not the frame-by-frame control loop.
5. All AI claims must be grounded in retrieved evidence.
6. Never infer intent, productivity, guilt, identity, or employee performance from pixels alone.
7. Use synthetic/anonymized identities in the POC. Never introduce biometric templates or facial embeddings unless explicitly approved.
8. Prefer small, testable changes. Do not perform speculative rewrites.
9. Architecture decisions belong to the project specification, not to the coding agent.
10. Use official AWS and Google documentation for current service/API behavior. Do not rely on memory when current verification is practical.

## Agent operating protocol

For non-trivial work, follow this loop:

OBSERVE → PLAN → EXECUTE → VERIFY → REVIEW → REPORT

- Observe: inspect relevant files, tests, docs, configuration, and current behavior.
- Plan: identify exact files, interfaces, assumptions, risks, and verification commands.
- Execute: make the smallest coherent change set.
- Verify: run targeted tests/builds and the end-to-end scenario that the change affects.
- Review: inspect the diff for unintended changes, dead code, regressions, and unsupported claims.
- Report: state what changed, what was verified, what failed, and what remains.

## Architecture-change gate

Do not independently:
- migrate frameworks,
- replace the backend language,
- introduce new cloud infrastructure,
- add production databases,
- add Kafka/Kinesis/ECS/Lambda/API Gateway,
- redesign APIs,
- change the Digital Twin source of truth,
- add real biometric/face-recognition processing,
- delete existing modules,
unless the task explicitly authorizes that architectural change.

When a request would require one of these, first create an architecture plan and impact assessment.

## Research policy

For AWS/Bedrock/model/API/SDK/service availability questions:
- prefer official AWS documentation;
- verify region/model/API assumptions;
- record the verified source in the task artifact or implementation notes.

For Antigravity configuration questions:
- prefer current official Antigravity documentation.

## Verification policy

Never claim “done”, “fixed”, “working”, or “manager-ready” without evidence.

At minimum, verify:
- syntax/compile/build;
- relevant automated tests;
- the user-visible flow affected by the change;
- git diff and changed-file list.

## Autonomy policy

The agent may work autonomously inside the workspace when the task is within the approved architecture. It should use subagents for independent research, large read-only audits, and verification where useful.

Autonomy does not mean unrestricted execution. Permissions, hooks, and repository rules remain authoritative.

## Change discipline

- Do not reformat unrelated files.
- Do not rewrite entire files when a targeted edit is sufficient.
- Do not fabricate files, APIs, credentials, model IDs, or test results.
- Do not report tools/actions as completed when they were only proposed.
- Preserve compatibility with the current POC until a migration milestone explicitly replaces it.
