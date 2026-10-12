---
name: architecture-guard
description: Review and protect the project's approved architecture before implementation or refactoring.
---
# Architecture Guard

Use for multi-file changes, refactors, migrations, or new external services.

## Procedure

1. Inspect repository structure and relevant code paths.
2. Identify current and target architecture.
3. Produce a minimal change plan with exact files.
4. Mark each proposed change as required, optional, or deferred.
5. Refuse speculative rewrites.
6. Require explicit confirmation in the task specification before changing the project language/framework or replacing the source of truth.
7. After implementation, verify that unrelated modules were not changed.

## Project-specific guardrail

The Python FastAPI backend under `backend/src/` is the single source of truth. There is no C# codebase. Do not introduce a second backend language or framework.
