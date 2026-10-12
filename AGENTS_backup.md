# AGENTS.md — Autonomous Engineering Rules

Read and obey `GEMINI.md` before acting.

## Hard rules

- Inspect before editing.
- For complex changes, use plan mode first.
- Never claim verification without actually running verification.
- Never invent APIs, AWS model IDs, credentials, measurements, or integrations.
- Never make architecture changes implicitly.
- Prefer official documentation for changing external systems.
- Keep the Digital Twin deterministic and evidence-backed.
- Keep Bedrock outside the real-time CCTV control loop.
- Use subagents for isolated research/review tasks instead of flooding the main context.
- Keep user-facing behavior and tests stable unless the task explicitly changes them.

## Required completion report

At the end of a coding task report:

1. Files changed.
2. Why each changed.
3. Tests/builds run.
4. Actual results.
5. Any unresolved risk.
6. Any architecture decision that remains pending.
