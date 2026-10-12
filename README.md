# Antigravity Office Vision Bootstrap

This kit configures Google Antigravity CLI for the Office Vision Intelligence project.

## Verified current Antigravity architecture

- CLI executable: `agy`.
- Workspace rules: `GEMINI.md` / `AGENTS.md`.
- Workspace skills: `.agents/skills/`.
- Workspace custom agents: `.agents/agents/`.
- Workspace MCP: `.agents/mcp_config.json`.
- Workspace hooks: `.agents/hooks.json`.
- Plan mode: `agy --mode=plan`.
- Model selection: `/model` or `agy --model gemini-3.1-pro-high`.
- Subagents: `/agents` and programmatic delegation.
- Context monitoring: `/statusline` exposes context-window usage.

## Install on Windows

From the directory containing this kit:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\Install-AntigravityOfficeVision.ps1 -ProjectPath "C:\path\to\office-security-digital-twin-poc" -InstallUv
```

Do not use `-InstallGlobalRules` unless you want the project charter applied to all Antigravity workspaces.

## First run

```powershell
cd "C:\path\to\office-security-digital-twin-poc"
agy --mode=plan --model=gemini-3.1-pro-high
```

Paste the contents of `BOOTSTRAP_PROMPT.md`.

## MCP

The active workspace MCP is AWS Documentation. It uses `uvx`, so `uv` is required. The kit also includes a disabled example for the managed AWS MCP server. Keep the managed AWS API MCP disabled until the exact IAM scope is reviewed.

## Autonomy model

The project is configured for sandboxed autonomous work with a plan-first default. Destructive commands and AWS infrastructure mutation are gated by a PreToolUse hook. This is deliberate: autonomy should be high for approved software work, but not unrestricted for destructive or infrastructure-changing actions.

## Model/context guidance

Gemini 3.1 Pro High is the requested reasoning model when available. Antigravity controls the actual context window at runtime; there is no supported project-file setting that increases the model's context ceiling. Use concise project rules, skills with progressive disclosure, specialized subagents, and fresh sessions at phase boundaries. The status line can expose context-window usage.
