---
name: create-pull-request
description: Inspect local git evidence, draft a pull request, and create it only when required fields are reliable
version: 1.0.0
owner: devops-team
triggers:
  - create pull request
  - create pr
  - open pr
  - /create-pull-request
tools:
  - run_command
  - github_get_default_branch
  - github_create_pull_request
task_tools:
  - run_command
when_to_use:
  - Use when the user asks to open a PR from current local repository work.
  - Use when PR content should be grounded in local git evidence before creation.
references:
  - ref-template.md
model: ""
---

# Skill: create-pull-request

## Execution Contract (strict)
STEP 1: Execute phases in order. Do not skip steps.
STEP 2: Use `run_command` for local git inspection and prefer git evidence over assumptions.
STEP 3: Do not call `github_create_pull_request` before drafting PR content.
STEP 4: If required info is missing or ambiguous, ask the user instead of guessing.
STEP 5: Stop after either creating the PR or asking one blocking question.

## Phase 1 — Check repository state
1. Confirm repository context with `git rev-parse --is-inside-work-tree`.
2. Get head branch with `git branch --show-current`.
3. Inspect working tree status with `git status --short`.
4. Inspect remotes with `git remote -v`.
5. Infer GitHub owner/repo from remotes only when clear; if ambiguous, ask user.

## Phase 2 — Collect change information
1. Gather summary with `git diff --stat`.
2. Gather changed files with `git diff --name-only`.
3. Review recent commits with `git log --oneline -5`.
4. If base is known, prefer comparison like `git diff --stat origin/<base>...HEAD`.
5. Inspect detailed per-file diffs only when needed.

## Phase 3 — Determine base branch
1. Prefer `github_get_default_branch` using arguments `owner`, `repo`.
2. The tool returns formatted text (for example, `Default branch for owner/repo: **main**`): extract only the raw branch name.
3. Do not pass markdown formatting or the full sentence as `base`.
4. If unavailable, infer cautiously from strong repo evidence.
5. If base branch remains ambiguous, ask user and stop.

## Phase 4 — Draft PR content (required before PR creation)
Prepare these sections first:
- PR Title
- Base Branch
- Head Branch
- Summary
- Files/Areas Changed
- Testing
- Risks / Notes

## Phase 5 — Validate required fields
Do not create PR unless all are reliable:
- repository owner
- repository name
- base branch
- head branch/current branch
- PR title
- PR body
If anything critical is missing, ask one concise blocking question and stop.

## Phase 6 — Create PR or ask user
- If ready, call `github_create_pull_request`.
- Required args are `owner`, `repo`, `title`, `body`, `head`.
- Pass `base` whenever it has been determined reliably.
- If `base` is ambiguous, prefer asking the user and stopping rather than guessing.

## Local vs GitHub distinction
- Local inspection is performed with `run_command`.
- GitHub PR creation still requires explicit `owner` and `repo`.
- If remote parsing from `git remote -v` is unclear, ask user instead of guessing.
