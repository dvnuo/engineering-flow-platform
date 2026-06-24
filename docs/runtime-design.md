# EFP Runtime Design

This document describes the Python EFP runtime as the direct runtime for the
service. It is not a compatibility layer for an older agent loop.

The implementation package is `src/efp_runtime` and imports as `efp_runtime`
with `PYTHONPATH=src`. Internal runtime tests live under `tests/runtime`
and form the current runtime test boundary.

## Runtime Boundary

- The runtime owns the agent loop, provider requests, tool execution,
  permissions, session history, context rendering, compaction, and Copilot
  provider transport.
- The gateway is API-only. It serves runtime HTTP endpoints such as `/api/chat`,
  `/api/chat/stream`, `/api/tasks/execute`, `/api/capabilities`, `/api/skills`,
  and session endpoints. It does not serve a root HTML page, browser UI,
  bundled templates, or static frontend assets.
- Portal is the UI and control plane. Portal provides trusted runtime profile
  metadata and provisions the external skills checkout. The runtime validates
  trusted Portal request metadata but owns execution behavior.
- MCP and external protocol tool servers are intentionally excluded.

## Removed Legacy Surfaces

The old Python model-facing tool surface is gone:

- `src/context_tools.py` is absent.
- `src/bash_tools` is absent.
- `src.efp_runtime.tools.local` and `src.efp_runtime.tools.external` are absent.
- Workspace-local Python tool loaders are not available.
- External Python tool loaders and `EFP_TOOLS_DIR` /
  `EFP_EXTERNAL_TOOLS_*` are not part of runtime execution.
- Jira, GitHub, Confluence, Git, Bash, and context Python tool packages are not
  aggregated into `src.get_tools_schema()`.

Tests may reference these names only to assert that they remain absent.

## Tool Surface

Model-facing tools come from the EFP-owned built-in registry under
`efp_runtime.tools.builtin`.

Default core tool ids are:

- `apply_patch`
- `bash`
- `edit`
- `glob`
- `grep`
- `invalid`
- `read`
- `skill`
- `task`
- `todowrite`
- `webfetch`
- `write`

Conditional built-ins include:

- `question`, when interactive question support is enabled.
- `lsp`, when an LSP client or explicit LSP flag is supplied.
- `plan_exit`, in plan mode or when explicitly enabled.
- `repo_clone` and `repo_overview`, when repository scout tools are explicitly
  requested.
- `websearch`, only when a caller injects a provider-neutral websearch runner.

Removed aliases such as `fetch`, `read_file`, `write_file`, `shell_exec`,
`shell_status`, `shell_kill`, `list_dir`, `todo_write`, `task_status`,
`task_cancel`, and `skill_list` are not registered.

`src.__init__` remains only as a small built-in registry wrapper so internal
callers can ask for schemas or execute a registered built-in by id. It does not
aggregate old Python tool packages.

The runtime image can also provide prebuilt `engineering-flow-platform-tools`
CLI binaries on `PATH` under `/usr/local/bin`. `scripts/prepare-runtime-tools.sh`
discovers every `cmd/<tool>/main.go` from that repository and writes generated
binaries to `runtime-tools/` for Docker/CI to copy into the image. Current tools
include `jira`, `confluence`, `browser`, and `mobile`; future `cmd/<tool>` binaries should
enter the image the same way. Agents reach these CLIs by invoking the model-facing
`bash` built-in in the workspace-full-access workspace. They should inspect
`<tool> commands --json` and `<tool> schema <command> --json`, prefer JSON
output, use `--dry-run` before writes, and reserve `--yes` for destructive
operations. These CLIs are not projected as separate model-facing function tools.

Private managed mobile sessions additionally require the BrowserStackLocal
binary staged as `runtime-tools/BrowserStackLocal` or configured through
`BROWSERSTACK_LOCAL_BINARY`.

Runtime profile application still writes the config files those CLIs and system
tools expect: Atlassian config for Jira/Confluence, `gh` hosts config for
GitHub, mobile BrowserStack config, and Git user include config. The old `EFP_TOOLS_DIR` and
`EFP_EXTERNAL_TOOLS_*` Python loader settings remain ignored; `runtime-tools/*`
is only a build input for PATH binaries.

## Loop And Provider

`efp_runtime.runtime.AgentRuntime` builds provider requests from:

1. Provider-only system prompt and runtime reminders.
2. Workspace instruction files and configured instruction text.
3. Available-skill and active-skill context.
4. Persisted session history.

The loop keeps typed session history: user, assistant, tool, task, attachment,
reasoning, error, and compaction parts remain structured. Tool calls and tool
results stay paired through rendering and compaction.

Production native chat supports GitHub Copilot only. The gateway reads
`llm.provider`, `llm.api_key`, `llm.api_base`, and `llm.model`, with
`EFP_GITHUB_COPILOT_TOKEN`, `GITHUB_COPILOT_TOKEN`, and
`EFP_GITHUB_COPILOT_BASE_URL` as environment overrides. Unsupported providers
fail explicitly instead of falling back to OpenAI, Anthropic, or Ollama.

## Sessions

The active gateway session API is
`src.efp_runtime.session.gateway_facade.RuntimeSessionManager`.

`src/sessions/manager.py` has been removed. Active gateway/runtime code imports
the runtime session facade directly, and `src/sessions` now contains only
support modules such as pruning, persistence, and usage.

The runtime facade backs gateway history, session list/delete/rename, metadata,
pending permissions/questions, todos, fork/revert/summary/query helpers, and
recovery metadata with `FileSessionStore`.

The default runtime session root is `~/.efp/runtime`. `EFP_RUNTIME_SESSION_ROOT`
overrides it explicitly. When that override is absent and `EFP_WORKSPACE_DIR` is
set, the runtime uses `$EFP_WORKSPACE_DIR/.efp/runtime` so Portal-managed native
agents persist sessions on the workspace mount.

## Runtime Task Recovery

The gateway persists runtime task tracker records under
`$EFP_WORKSPACE_DIR/.efp/runtime_tasks` by default. This lets a restarted native
runtime reconcile accepted Portal tasks instead of leaving them permanently
running in Portal. Startup reconciliation follows the upstream OpenCode safety
boundary: completed and blocked task metadata can be reloaded, but in-flight
provider/tool activity is never automatically replayed after process loss.

Startup recovery is intentionally bounded:

- `EFP_RUNTIME_TASKS_LOAD_MAX_RECORDS` limits how many persisted records are
  loaded at startup. The default is `256`.
- `EFP_RUNTIME_TASKS_SCAN_MAX_RECORDS` limits how many candidate task files are
  parsed while looking for records to load. The default is `512`.
- `EFP_RUNTIME_TASKS_LOAD_MAX_FILE_BYTES` skips individual persisted task files
  larger than the configured byte limit. The default is `2000000`.
- Active records from a previous process are marked stale with
  `runtime_restart_task_replay_disabled` and should be re-dispatched if the work
  is still required.
- `EFP_RUNTIME_TASKS_PERSIST_MAX_FILE_BYTES` caps each persisted record. Large
  result payloads are omitted from the persisted tracker file while the task
  status and identifiers remain available. The default is `2000000`.

## Skills And Commands

The runtime discovers skills from configured roots, `EFP_SKILLS_DIR`, or
`/app/skills`. Skill sidecars are read as data; Python sidecars are never
imported or executed. The `skill` built-in loads bounded skill context for the
model, and `/skill` activates provider-only skill context before a provider
request.

Slash commands are prompt-expansion records. They do not invoke the old skill
executor or old session stack. Command shell interpolation, when used, executes
through the built-in `bash` tool path with normal validation, permission,
output normalization, and lifecycle events.

## Context And Compaction

The runtime performs request-local context rendering and automatic session
compaction. Compaction is part-aware and protects pending tool calls, the latest
active user request, and recent tail turns. System prompts, instructions, and
skill context remain provider-only and are not persisted into session history.

Copilot model profiles provide context-window and reserve defaults for token
budget conversion. Unknown model ids use a conservative Copilot fallback.

## Permissions

Tool permissions are evaluated through the runtime permission broker. Built-ins
carry static permission metadata, and `RuntimeConfig.tool_permissions` can
override by exact tool id, category, subject, wildcard, or fallback. ASK
decisions pause the loop and resume against the same pending tool call after
approval or denial.

The default environment is workspace-full-access inside the configured runtime
workspace. Tools still reject paths that escape the workspace root.

## Current Gaps

The runtime intentionally does not implement:

- MCP or external protocol tool providers.
- Old Python local/external tool loaders.
- Embedded browser UI.
- Provider fallback to OpenAI, Anthropic, or Ollama.
- Shell job restoration.

The current tool and capability contract is tracked in
`docs/runtime-tool-surface.md` and guarded by runtime tests.
