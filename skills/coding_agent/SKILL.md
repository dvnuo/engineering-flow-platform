---
name: coding-agent
description: Run Codex CLI, Claude Code, OpenCode, or Pi Coding Agent via bash with PTY mode for interactive terminal control.
metadata:
  emoji: 🧩
  requires:
    bins: [bash, git]
    anyBins: [codex, claude, opencode, pi]
    env: []
    config: []
---

# Coding Agent (bash-first)

Use **bash** with PTY mode for all coding agent work.

## ⚠️ PTY Mode Required!

Coding agents (Codex, Claude Code, Pi) are **interactive terminal applications** that need a pseudo-terminal (PTY) to work correctly. Without PTY, output breaks, colors are missing, or the agent hangs.

**Always use `pty:true`** when running coding agents:

```bash
# ✅ Correct - with PTY
bash pty:true command:"codex exec 'Your prompt'"

# ❌ Wrong - no PTY, agent may break
bash command:"codex exec 'Your prompt'"
```

### Bash Tool Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `command` | string | Shell command to run |
| `pty` | boolean | **Use for coding agents!** Allocates pseudo-terminal |
| `workdir` | string | Working directory (agent sees only this folder) |
| `background` | boolean | Run in background, returns sessionId for monitoring |
| `timeout` | number | Timeout in seconds (kills process on expiry) |
| `elevated` | boolean | Run on host instead of sandbox |

---

## Quick Start: One-Shot Tasks

For quick prompts, create a temp git repo and run:

```bash
# Quick chat (Codex needs a git repo!)
SCRATCH=$(mktemp -d) && cd $SCRATCH && git init && codex exec "Your prompt"

# Or in a real project - with PTY!
bash pty:true workdir:~/Projects/myproject command:"codex exec 'Add error handling'"
```

**Why git init?** Codex refuses to run outside a trusted git directory. Creating a temp repo solves this for scratch work.

---

## The Pattern: workdir + background + pty

For longer tasks, use background mode with PTY:

```bash
# Start agent in target directory (with PTY!)
bash pty:true workdir:~/project background:true command:"codex exec --full-auto 'Build a snake game'"
# Returns sessionId for tracking

# Monitor progress
process action:log sessionId:XXX

# Check if done
process action:poll sessionId:XXX

# Send input (if agent asks a question)
process action:write sessionId:XXX data:"y"

# Submit with Enter (like typing "yes" and pressing Enter)
process action:submit sessionId:XXX data:"yes"

# Kill if needed
process action:kill sessionId:XXX
```

**Why workdir matters:** Agent wakes up in a focused directory, doesn't wander off reading unrelated files.

---

## Supported Coding Agents

### Codex CLI

**Model:** `gpt-4o` is the default

| Flag | Effect |
|------|--------|
| `exec "prompt"` | One-shot execution, exits when done |
| `--full-auto` | Sandboxed but auto-approves in workspace |
| `--yolo` | NO sandbox, NO approvals (fastest, most dangerous) |

#### Building/Creating

```bash
# Quick one-shot (auto-approves) - remember PTY!
bash pty:true workdir:~/project command:"codex exec --full-auto 'Build a dark mode toggle'"

# Background for longer work
bash pty:true workdir:~/project background:true command:"codex --yolo 'Refactor the auth module'"
```

#### Reviewing PRs

```bash
# Clone to temp for safe review
REVIEW_DIR=$(mktemp -d)
git clone https://github.com/user/repo.git $REVIEW_DIR
cd $REVIEW_DIR && gh pr checkout 130
bash pty:true workdir:$REVIEW_DIR command:"codex review --base origin/main"

# Or use git worktree (keeps main intact)
git worktree add /tmp/pr-130-review pr-130-branch
bash pty:true workdir:/tmp/pr-130-review command:"codex review --base main"
```

#### Batch PR Reviews (parallel)

```bash
# Fetch all PR refs
git fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'

# Deploy agents - one per PR (all with PTY!)
bash pty:true workdir:~/project background:true command:"codex exec 'Review PR #86'"
bash pty:true workdir:~/project background:true command:"codex exec 'Review PR #87'"

# Monitor all
process action:list
```

---

### Claude Code

```bash
# With PTY for proper terminal output
bash pty:true workdir:~/project command:"claude 'Your task'"

# Background
bash pty:true workdir:~/project background:true command:"claude 'Your task'"
```

---

### OpenCode

```bash
bash pty:true workdir:~/project command:"opencode run 'Your task'"
```

---

### Pi Coding Agent

```bash
# Install: npm install -g @mariozechner/pi-coding-agent

# With PTY
bash pty:true workdir:~/project command:"pi 'Your task'"

# Non-interactive mode (PTY still recommended)
bash pty:true command:"pi -p 'Summarize src/'"

# Different provider/model
bash pty:true command:"pi --provider openai --model gpt-4o-mini -p 'Your task'"
```

---

## Modes Explained

### --full-auto

- Sandboxed environment
- Auto-approves changes in workspace
- Safe for building and creating

### --yolo

- NO sandbox
- NO approval prompts
- Fastest mode
- Most dangerous - use with caution

### vanilla

- Default mode
- Standard sandbox
- Asks for approval before changes

---

## Process Tool Actions (for background sessions)

| Action | Description |
|--------|-------------|
| `list` | List all running/recent sessions |
| `poll` | Check if session is still running |
| `log` | Get session output (with optional offset/limit) |
| `write` | Send raw data to stdin |
| `submit` | Send data + newline (like typing and pressing Enter) |
| `send-keys` | Send key tokens or hex bytes |
| `paste` | Paste text (with optional bracketed mode) |
| `kill` | Terminate the session |

---

## Parallel Issue Fixing with git worktrees

For fixing multiple issues in parallel:

```bash
# 1. Create worktrees for each issue
git worktree add -b fix/issue-78 /tmp/issue-78 main
git worktree add -b fix/issue-99 /tmp/issue-99 main

# 2. Launch coding agent in each (background + PTY!)
bash pty:true workdir:/tmp/issue-78 background:true command:"codex --yolo 'Fix issue #78'"
bash pty:true workdir:/tmp/issue-99 background:true command:"codex --yolo 'Fix issue #99'"

# 3. Monitor progress
process action:list
process action:log sessionId:XXX

# 4. Create PRs after fixes
cd /tmp/issue-78 && git push -u origin fix/issue-78

# 5. Cleanup
git worktree remove /tmp/issue-78
git worktree remove /tmp/issue-99
```

---

## Rules

1. **Always use pty:true** - coding agents need a terminal!
2. **Respect tool choice** - if user asks for Codex, use Codex
3. **Orchestrator mode** - don't silently take over when agent fails
4. **Be patient** - don't kill sessions because they're "slow"
5. **Monitor with process:log** - check progress without interfering
6. **--full-auto for building** - auto-approves changes
7. **Parallel is OK** - run many agents at once for batch work
8. **Never run in critical directories** - avoid ~/Projects/openclaw/

---

## Progress Updates (Critical)

When spawning coding agents in background, keep the user informed:

- Send 1 short message when starting (what's running + where)
- Update only when something changes:
  - Milestone completes
  - Agent asks a question / needs input
  - Error or user action needed
  - Agent finishes (include what changed + where)
- If killing a session, immediately explain why

---

## Auto-Notify on Completion

For long-running tasks, append a wake trigger so notification arrives immediately:

```bash
bash pty:true workdir:~/project background:true command:"codex --yolo 'Build a REST API.
When finished, run: efp gateway wake --text \"Done: Built todos API\" --mode now'"
```

This triggers an immediate wake event.

---

## Examples

### Example 1: Create a new feature

```bash
# Start coding agent in project directory
bash pty:true workdir:~/project command:"codex --full-auto 'Add user authentication with JWT'"

# If it takes time, run in background
bash pty:true workdir:~/project background:true command:"codex --full-auto 'Build a complete CRUD API'"
```

### Example 2: Fix a bug

```bash
# Analyze and fix the bug
bash pty:true workdir:~/project command:"codex exec 'Fix the memory leak in the image processor'"

# Or use worktree for safety
git worktree add -b fix/bug-123 /tmp/bug-123 main
bash pty:true workdir:/tmp/bug-123 command:"codex --yolo 'Fix null pointer exception in user.py:45'"
```

### Example 3: Code review

```bash
# Review changes
bash pty:true workdir:~/project command:"codex review --base main"

# Batch review multiple PRs
bash pty:true workdir:~/project background:true command:"codex exec 'Review PR #100'"
bash pty:true workdir:~/project background:true command:"codex exec 'Review PR #101'"
```

### Example 4: Generate tests

```bash
# Generate unit tests
bash pty:true workdir:~/project command:"codex exec --full-auto 'Generate unit tests for auth.py'"

# Generate integration tests
bash pty:true workdir:~/project command:"codex exec --full-auto 'Create integration tests for API endpoints'"
```

---

## Best Practices

1. **Always use git repository** - coding agents need version control
2. **Use workdir** - keeps agent focused on relevant files
3. **PTY mode** - essential for interactive terminals
4. **Background for long tasks** - monitor with process actions
5. **--full-auto for building** - safe auto-approval
6. **--yolo for speed** - when you trust the agent
7. **Progress updates** - keep user informed
8. **Auto-notify** - for long-running background tasks

---

## Troubleshooting

### Agent hangs or no output

**Cause:** Missing PTY mode

**Solution:** Always use `pty:true`

### Agent refuses to run

**Cause:** Not in a git repository

**Solution:** Initialize git repo first

```bash
cd /tmp && SCRATCH=$(mktemp -d) && cd $SCRATCH && git init && codex exec "Your prompt"
```

### Changes not saved

**Cause:** Sandbox mode blocking writes

**Solution:** Use `--full-auto` or `--yolo` flag

---

## See Also

- `tools/exec.md` - Bash tool documentation
- `tools/process.md` - Process monitoring tool
- OpenClaw coding-agent: https://github.com/openclaw/openclaw/tree/main/skills/coding-agent
