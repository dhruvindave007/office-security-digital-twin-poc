# Antigravity Setup for Office Vision Intelligence

## Why this setup exists

The repository needs Antigravity configured as a controlled autonomous engineering environment, not as an unconstrained code generator.

## What is configured

- `GEMINI.md` and `AGENTS.md`: project-wide engineering contract.
- `.agents/skills/`: focused workflows using progressive disclosure.
- `.agents/agents/`: architect, implementer, reviewer, researcher, Bedrock specialist.
- `.agents/mcp_config.json`: AWS Documentation MCP.
- `.agents/hooks.json`: command safety gate + verification reminder.
- `.agents/rules/`: project constitution.

## What is deliberately NOT configured

- unrestricted `always-proceed` mode;
- unrestricted AWS mutation MCP;
- arbitrary production deployment tooling;
- guessed Bedrock model IDs.

## Model

Use Gemini 3.1 Pro High for architecture, research, and complex implementation sessions when available. Current Antigravity documentation lists `gemini-3.1-pro-high` as a supported model slug. Use `/model` interactively or `agy --model gemini-3.1-pro-high` for a pinned headless run.

## Execution modes

- `plan` for architecture, unfamiliar code, migrations, or multi-file changes.
- `accept-edits` for approved implementation tasks.
- Use `request-review` or sandboxed execution rather than unrestricted permissions for normal work.

## Context

Do not try to increase the model's context window from project files; the runtime/model controls the actual window. Instead, keep the project charter concise, use subagents for independent work, and start fresh sessions at phase boundaries. Antigravity exposes context-window usage in the status line.

## Useful commands

```text
/plan
/agents
/skills
/mcp
/permissions
/usage
/statusline
/resume
/rewind
/diff
```

## Recommended first command

From the project root:

```powershell
agy --mode=plan --model=gemini-3.1-pro-high
```

Then paste `BOOTSTRAP_PROMPT.md`.
