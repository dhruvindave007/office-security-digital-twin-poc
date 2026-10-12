# How to run a milestone with Gemini (Antigravity `agy`)

Golden rule: **one milestone = one fresh session = one spec file.** Never paste the spec text in; point Gemini at the file path. Never add "also, could you…" requests mid-milestone.

Milestone order: `M3_RULES_SIMULATOR_PROMPT.md` → (review) → `M3B_FUSION_PROMPT.md` → (review) → next spec.

---

## 0. One-time setup (you, not Gemini)

```powershell
cd "C:\Users\DhruvinDave(DataAI)\office-security-digital-twin-poc"
git init
git add -A
git commit -m "Baseline before M3"
```
`.gitignore` already excludes `.venv`. Check `git status` to make sure `data/digital_twin.db` isn't staged; if it is, add it to `.gitignore`.

---

## 1. Per milestone: start clean

```powershell
git status            # must be clean. If not, commit or stash first.
git checkout -b m3    # one branch per milestone (m3, m3b, m4...)
agy --mode=plan
```

## 2. Message 1: plan only (copy exactly, change the file name)

```
Read docs/prompts/M3_RULES_SIMULATOR_PROMPT.md completely, plus every file it tells you to read.
Do NOT write or edit any file yet.

Reply with:
1. Every file you will create or modify, grouped by step.
2. For each step, the test command you will run after finishing it.
3. Any place where the spec contradicts the actual code (file + line + what differs).
4. Anything in the spec you do not understand.

Do not propose improvements or extra features.
```

Check its answer:
- The file list matches the spec's steps. Nothing extra (no frontend, no Bedrock, no rename of existing files).
- If it lists contradictions, **stop and bring them to Claude.** Do not let Gemini resolve them.

## 3. Message 2: implement step by step

Switch to edit mode (`/permissions` → accept-edits, or restart with `agy --mode=accept-edits`), then:

```
Plan approved. Implement docs/prompts/M3_RULES_SIMULATOR_PROMPT.md.

Rules:
- Do ONE step at a time, in order (Step 0, 1, 1b, 2, ...).
- After each step, run: .\.venv\Scripts\python.exe -m pytest tests/unit -q  (from backend/)
  and show me the last 5 lines of real output.
- If a test fails, fix it before moving on. Never delete, skip, or weaken an existing test.
- If the spec is impossible or contradicts the code, STOP and tell me. Do not improvise.
- Do not touch files the spec does not mention.
Start with Step 0 and stop after Step 2 to report.
```

Splitting the work (Steps 0–2, then 3–5, then 6–7) keeps Flash from losing track. After each chunk:

```
Continue with Steps 3 to 5, same rules. Stop and report after Step 5.
```
```
Continue with Steps 6 and 7, same rules, then give the Final report in the exact format the spec requires.
```

## 4. Rescue prompts (paste when needed)

**It claims "done" or "all tests pass" without output:**
```
Show me the actual terminal output of: .\.venv\Scripts\python.exe -m pytest tests/unit -q
Do not summarize it. Paste it.
```

**It went outside the spec:**
```
You modified <file>, which is not in the spec. Revert that change with: git checkout -- <file>
Then explain why you touched it. Stay strictly inside docs/prompts/<SPEC>.md.
```

**A test keeps failing after two attempts:**
```
Stop fixing. Report: the failing test name, the full error, the code it exercises,
and your hypothesis. Do not change anything else.
```
Bring that report to Claude.

**It wants to "improve"/refactor something:**
```
No. Out of scope. Note it under "Remaining limitations" in the final report and continue.
```

**The session gets long or confused** (it repeats itself, forgets earlier steps, or the context bar is high):
```
Summarize: which steps are done, which files changed, the current test count, and what is next.
```
Then `/exit`, start a fresh `agy` session, and send:
```
Continue implementing docs/prompts/<SPEC>.md from Step <N>. Steps before N are done.
First run the test suite and show the output, then continue with the same rules as before.
```

## 5. When Gemini finishes: bring this to Claude for review

Run these yourself (don't trust Gemini's copy):
```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit -q
cd ..
git status
git diff --stat
```
Then tell Claude: **"Review milestone M3 on branch m3"** and paste Gemini's final report. Claude can read the diff directly from the repo.

**Do not commit or merge until the review passes.** After it passes:
```powershell
git add -A
git commit -m "M3: rules engine, simulator, ingest API"
git checkout main ; git merge m3
```
(Use `master` instead of `main` if that is your default branch name.)

## 6. Things never to do
- Don't run in an "always proceed" or unrestricted mode.
- Don't give two milestones in one session.
- Don't let Gemini edit `GEMINI.md`, `AGENTS.md`, or `.agents/*` except where a spec's Step 0 says so.
- Don't accept "I verified it" without pasted command output.
- Don't let it touch `data/digital_twin.db`. Tests use temp DBs, smoke tests use throwaway DBs.
