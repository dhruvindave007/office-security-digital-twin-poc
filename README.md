


- CLI executable: `agy`.
- Subagents: `/agents` and programmatic delegation.
- Context monitoring: `/statusline` exposes context-window usage.

## Install on Windows

From the directory containing this kit:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```


## First run

```powershell
cd "C:\path\to\office-security-digital-twin-poc"
```

Paste the contents of `BOOTSTRAP_PROMPT.md`.

## MCP

The active workspace MCP is AWS Documentation. It uses `uvx`, so `uv` is required. The kit also includes a disabled example for the managed AWS MCP server. Keep the managed AWS API MCP disabled until the exact IAM scope is reviewed.

## Autonomy model

The project is configured for sandboxed autonomous work with a plan-first default. Destructive commands and AWS infrastructure mutation are gated by a PreToolUse hook. This is deliberate: autonomy should be high for approved software work, but not unrestricted for destructive or infrastructure-changing actions.

## Model/context guidance

