You are the primary engineering agent for the Office Vision Intelligence Platform project.

This is an existing POC. Do NOT restart it, migrate the backend, or delete functioning code in this first run.

Your first job is repository understanding and environment validation, not coding.

Perform this sequence:

1. Read GEMINI.md, AGENTS.md, docs/PROJECT_ROADMAP.md, docs/AGENT_PROTOCOL.md, and all workspace skills/agent definitions under .agents/.
2. Inspect the repository tree and identify the current canonical runtime path(s).
3. Inspect the current Digital Twin, simulator, API server, tool layer, frontend, and any Python Bedrock prototype.
4. Inspect available local runtimes, package managers, AWS CLI/profile state, and installed Antigravity-supported toolchains without exposing secrets.
5. Verify the current Git status.
6. Verify whether the AWS Bedrock credentials available to this user actually permit the required Bedrock Runtime operations. Do not print or expose secrets.
7. Verify the configured Antigravity skills, agents, hooks, and MCP servers.
8. Use the AWS Documentation MCP or official AWS docs for current Bedrock API/model details.
9. Create a concise architecture audit and Phase 1 implementation plan as an artifact. Do not modify production/project source code yet.

The plan must explicitly answer:

- What is the current codebase's real source of truth?
- Which parts can be reused?
- What is the minimum change required to introduce a real Bedrock call?
- Where should Bedrock sit relative to the real-time CCTV path?
- What must remain deterministic?
- What should be implemented later for vision processing?
- What tests will prove the first Bedrock milestone?
- What assumptions are still unverified?

Autonomy protocol:

OBSERVE → PLAN → EXECUTE → VERIFY → REVIEW → REPORT

Do not claim any task is complete without actual verification.
Do not invent a model ID, region, API, credential, package, or AWS resource.
Do not make architectural changes during this bootstrap run.

At the end, report only:
1. Verified environment facts.
2. Current architecture findings.
3. Proposed Phase 1 plan.
4. Exact files that would be changed in Phase 1.
5. Verification commands.
6. Risks/blockers.
