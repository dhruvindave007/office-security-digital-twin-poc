---
name: verification
description: Verify implementation with tests, builds, targeted smoke tests, and diff review before reporting completion.
---
# Verification Loop

For every non-trivial task:

1. Run the narrowest relevant tests first.
2. Run the full available test suite when practical.
3. Exercise the changed user flow.
4. Inspect git diff/status.
5. Check for accidental file churn.
6. Report actual outputs and failures.

Never use phrases like “all tests pass” unless the command was run and succeeded.

If the repository lacks a reliable verification command, create a small targeted verification script before making large changes.
