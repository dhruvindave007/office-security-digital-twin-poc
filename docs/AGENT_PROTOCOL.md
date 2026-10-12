# Autonomous Agent Operating Model

This project uses an agentic loop inspired by the engineering patterns practiced in the Claude certification material: plan before acting, use tools deliberately, separate responsibilities, verify results, and preserve human control over risky actions.

## Primary loop

OBSERVE → PLAN → ACT → VERIFY → REFLECT → REPORT

## Tool-use discipline

- Prefer read/search tools before write tools.
- Use the smallest tool scope that solves the task.
- Keep tool calls structured and auditable.
- Do not treat generated text as evidence.
- After a tool result, reassess the plan before continuing.

## Subagent strategy

Use a research subagent for current external facts.
Use an architecture agent for design review.
Use an implementer for code changes.
Use a reviewer for adversarial validation.
Use parallel subagents when workstreams are independent.

Subagents should return concise findings to the parent context instead of dumping entire files.

## Checkpointing

At major milestones:
- record the current objective;
- record changed files;
- record tests/results;
- record unresolved questions;
- create a git commit or checkpoint before risky changes when the repository is under version control.

## Autonomy boundaries

Autonomous execution is encouraged for approved implementation tasks.
Architecture changes, destructive commands, production infrastructure, real biometric processing, and cloud-resource mutation require explicit approval.

## Context hygiene

Do not attempt to keep an entire repository in the prompt. Use repository search and targeted file reads. Delegate broad audits to the research/reviewer subagents. Start a fresh conversation when the task changes phase materially.
