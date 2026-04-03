---
name: create-cr
description: Inspect local git state, draft a pull request, and create it when required fields are reliable
version: 2.0.0
owner: devops-team
triggers:
  - create pull request
  - create pr
  - open pr
  - /create-cr
tools:
  - run_command
  - github_get_default_branch
  - github_create_pull_request
task_tools:
  - run_command
when_to_use:
  - Use when the user wants to open a PR from current local repository changes.
  - Use when local git evidence must be collected before proposing PR content.
references:
  - ref-create-cr-template.md
model: ""
---

# Skill: create-cr

## Execution Contract (do not skip)
STEP 1: Follow phases in order. Do not skip ahead.
STEP 2: Use `run_command` for local git inspection; prefer git evidence over assumptions.
STEP 3: Do not call `github_create_pull_request` until after drafting PR content.
STEP 4: If required fields are missing or ambiguous, ask the user instead of guessing.
STEP 5: Stop after either (a) creating the PR or (b) asking a blocking question.

## Phase 1 — Check repository state
1. Verify repository context:
   - `git rev-parse --is-inside-work-tree`
   - If not a git repository, ask user how to proceed and stop.
2. Identify current branch:
   - `git branch --show-current`
3. Inspect working tree status:
   - `git status --short`
4. Inspect remotes for GitHub owner/repo hints:
   - `git remote -v`
   - Parse owner/repo cautiously; if multiple remotes or ambiguous naming, ask user.

## Phase 2 — Collect change information
1. Gather summary and changed files:
   - `git diff --stat`
   - `git diff --name-only`
2. Review recent commits:
   - `git log --oneline -5`
3. If base branch is known, prefer comparing against it (often better than working-tree only):
   - `git diff --stat origin/<base>...HEAD`
   - Use detailed per-file diff only when needed.

## Phase 3 — Determine base branch
1. Prefer `github_get_default_branch` (requires `owner`, `repo`).
2. `github_get_default_branch` args: `owner`, `repo`.
3. If unavailable, infer from strong evidence only (`main`, `master`, `develop`).
4. If still uncertain, ask user for base branch and stop.

## Phase 4 — Draft PR content (required before creation)
Produce this structure before any PR creation call:
- PR Title
- Base Branch
- Head Branch
- Summary
- Files/Areas Changed
- Testing
- Risks / Notes

## Phase 5 — Validate required fields
Do not create PR unless all are known and reliable:
- repository owner
- repository name
- base branch
- head branch/current branch
- PR title
- PR body
If anything critical is missing, ask one concise blocking question.

## Phase 6 — Create PR or ask user
- If ready, call `github_create_pull_request`.
- `github_create_pull_request` args: `owner`, `repo`, `title`, `body`, `head`, `base`.
- If not ready, ask user for the missing data and stop.

## Local vs remote reminder
- Local inspection happens via `run_command`.
- GitHub PR creation still requires remote identifiers (`owner`, `repo`).
- Infer from `git remote -v` only when clear; otherwise ask user.
