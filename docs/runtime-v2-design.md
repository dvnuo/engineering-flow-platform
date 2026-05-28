# EFP Runtime v2 Baseline

Runtime v2 is a standalone package under `src/efp_runtime`. It is importable as
`efp_runtime` when `PYTHONPATH=src` and is not wired into Portal or the legacy
agent runtime.

## Goals

- Keep session history as structured messages and parts.
- Normalize provider output into provider-neutral LLM events.
- Execute tools through one validation, permission, execution, normalization path.
- Model skills as context loading, not as a separate execution mode.
- Compact structured history without splitting tool call/result pairs.

## Non-goals

- Do not wrap `Agent.process`.
- Do not call `SkillSession` or `SkillsExecutor`.
- Do not import `src.agents.core`.
- Do not import `src.agents.tool_result_policy`.
- Do not replace legacy Portal or runtime entrypoints yet.

## Import Boundary

Runtime v2 modules use package-relative imports inside `src/efp_runtime`. The
package must import cleanly with:

```bash
PYTHONPATH=src python -c "import efp_runtime"
```

Importing runtime v2 must not import `src.agents.core` or require optional
legacy tool dependencies. It also must not import legacy skill/runtime modules
such as `src.agents.skill_runtime`, `src.agents.skill_mode`, or `src.skills`.
The legacy root package `src/__init__.py` is left unchanged.

## Data Model

The canonical history model is:

- `Session`: an ordered collection of messages.
- `Message`: a role, stable message id, status, usage, metadata, and typed parts.
- `MessagePart`: one structured unit such as text, reasoning, tool call, tool
  result, attachment, task, compaction, or error.
- `ToolCall` and `ToolResult`: normalized tool call/result records with legacy
  friendly aliases for `id`, `tool_id`, `args`, and `name`.

Tool calls and results are always structured message parts. They are not
flattened into assistant text.

## LLM Loop

The adapter emits `LLMEvent` values for text deltas, tool input, completed tool
calls, tool results, step start/finish, and errors. The session processor
consumes those events into structured messages.

The loop also bridges normalized provider stream events into transient
`RuntimeEvent` records for observability. When
`RuntimeConfig.emit_llm_stream_events=True` (the default), the runner appends
`llm.step_start`, `llm.text_delta`, `llm.reasoning_delta`,
`llm.tool_call_delta`, `llm.tool_call_done`, `llm.step_finish`, and `llm.error`
events between `iteration_start` and `iteration_finish`. These events include
the run id, iteration, original LLM event type, available event ids, text or
reasoning deltas, and tool call ids, names, and argument deltas or final
arguments. The bridge is observation-only: persisted session history still uses
the same final `Message` and `MessagePart` structures produced by the session
processor, and provider-only context is not written to history.

`RuntimeEventBus` publishes the same bridged `llm.*` events as they are appended
to the loop event log, so callers can subscribe to a session and drive UI
streaming without reading or mutating the session store. Set
`emit_llm_stream_events=False` to keep loop events at the older run, iteration,
provider retry, and tool lifecycle level.

Each loop iteration builds a `RuntimeRequest` for the provider. It keeps the raw
`Message` history for compatibility, and also carries a rendered
`ProviderRequest`, the `PreparedProviderRequest` with compaction metadata, and
the sorted `ToolDef` list used to render provider-neutral tool schemas.
`AgentRuntime` prepends provider-only context before persisted history in this
order: system prompt and runtime reminders, workspace instructions, active
skills, then session history.

`STEP_FINISH` updates the active assistant message. Tool result events append a
separate tool message and do not make that tool message the active assistant
message, so later usage and completion state are not attached to the wrong
message.

## Usage Telemetry

Runtime v2 aggregates provider-neutral usage from `LLMEvent.usage`. The
provider adapter may keep the provider's original usage dictionary on the
assistant message, but the loop also normalizes common token fields into a run
summary: input, output, reasoning, cached input, total tokens, and estimated
cost. The accumulator understands common aliases such as `prompt_tokens` for
input and `completion_tokens` for output, plus nested cached/reasoning detail
fields when providers expose them.

When `RuntimeConfig.track_usage=True` (the default), `RuntimeLoopResult.usage`
contains the cumulative run summary. The loop also publishes `usage.updated`
runtime events after step-finish usage is observed, adds per-iteration and
cumulative usage to `iteration_finish`, and includes the final usage summary in
`run_finish`. Provider request metadata includes `track_usage` and whether
usage pricing is enabled, so providers and workflows can observe the requested
telemetry mode without changing provider output shapes.

`RuntimeConfig.usage_pricing` is caller-provided estimation data, not provider
truth and not a built-in price table. Values are per-1M token prices such as
`input_per_1m`, `output_per_1m`, `reasoning_per_1m`, and
`cached_input_per_1m`; empty pricing keeps `cost_usd=None`. Runtime v2 does not
fetch pricing data or bind usage accounting to any specific provider.

## Runtime Facade

`efp_runtime.runtime.AgentRuntime` is the high-level Runtime v2 facade. It wires
an injected provider to an in-memory session store, `ToolRuntime`, context
rendering, compaction, and the loop runner. When configured with a workspace root
and no explicit tool registry/runtime, it creates the Runtime v2 built-in tool
registry for that workspace. The facade does not call Portal or the legacy agent
runtime.

## Workspace Config Loader

Runtime v2 exposes an explicit local config-loading entry point:

```python
from efp_runtime.config_loader import load_runtime_config
from efp_runtime.workspace import (
    create_agent_runtime_from_workspace,
    load_runtime_workspace,
)

loaded = load_runtime_config(workspace_root)
runtime_config = loaded.config
agent_registry = loaded.agent_registry

workspace = load_runtime_workspace(workspace_root)
runtime = create_agent_runtime_from_workspace(
    provider=provider,
    workspace_root=workspace_root,
)
```

`load_runtime_config(...)` returns a `RuntimeConfigLoadResult` containing the
constructed `RuntimeConfig`, an optional `AgentRegistry`, an optional
`CommandRegistry`, config command definitions, the successfully loaded file
paths, the merged raw config object, and loader metadata.

`load_runtime_workspace(...)` wraps that result in a small `RuntimeWorkspace`
object with convenience accessors for `config`, `agent_registry`, and
`command_registry`. `create_agent_runtime_from_workspace(...)` loads the
workspace once and constructs `AgentRuntime` with the loaded config, command
registry, agent registry, and registry default agent. Runtime v2 still never
constructs a provider in this path: provider and model selection remain
caller-owned and must be injected with `provider=...`.

The default lookup order is:

1. `opencode.json`
2. `opencode.jsonc`
3. `.opencode.json`
4. `.opencode/config.json`
5. `.opencode/config.jsonc`

Only files that exist are loaded. Multiple files are merged in that order:
mappings are deep-merged, lists are appended with stable de-duplication, and
scalar values from later files override earlier files. Explicit `paths=` can be
passed to load additional local files; relative paths are resolved under the
workspace root. JSONC files support line comments, block comments, and trailing
commas without treating `//` inside JSON strings as comments. Invalid JSON raises
`ValueError` with the file path in the message.

The loader maps the following opencode-style and snake_case keys into
`RuntimeConfig`:

- `permission` / `permissions` to `tool_permissions`.
- `enabledTools` / `enabled_tools`.
- `disabledTools` / `disabled_tools`.
- `instructions`, as string paths or `{"path": ...}` / `{"text": ...}` entries,
  to `instruction_paths` and `instruction_texts`.
- `systemPrompt` / `system_prompt`, as a string or list, to
  `system_prompt_texts`.
- `skillDirectories` / `skill_directories`, plus local `skills.paths`.
- `activeSkills` / `active_skills`.
- `commandDirectories` / `command_directories`.
- `runtime.mode` or `runtime_mode`.

Configured path fields are resolved as workspace-local `Path` objects without
requiring those files or directories to already exist. With
`include_defaults=True`, the loader adds existing default command directories
first: `~/.config/opencode/commands`, then workspace-local `.opencode/command`,
then workspace-local `.opencode/commands`. Missing default command directories
are ignored. Passing `include_defaults=False` disables these default command
directories. Configured `commandDirectories` / `command_directories` entries
are appended after defaults.
With `include_defaults=True`, existing user-level skill directories are added
first: `~/.claude/skills`, then `~/.agents/skills`. Existing project-local
skill directories are added next: `.opencode/skill`, `.opencode/skills`,
`.claude/skills`, then `.agents/skills`. Missing default skill directories are
ignored. Passing `include_defaults=False` disables all default skill
directories. Configured `skillDirectories` / `skill_directories` are appended
after defaults, and local `skills.paths` entries are appended after those.
Relative paths resolve under the workspace root and are de-duplicated with
`skillDirectories`.

`skills.urls` and remote skill pulling are not implemented in this phase.
Unsupported keys under `skills` are preserved in
`RuntimeConfigLoadResult.metadata["unconsumed_config"]["skills"]`.

The loader consumes `command` and `commands` to build config-defined
`CommandDefinition` records and a `CommandRegistry` that callers can pass to
`AgentRuntime(command_registry=loaded.command_registry)`.

Agent profiles can come from markdown files and config entries. With
`include_defaults=True`, the loader discovers project-local
`.opencode/agents/*.md` and `*.markdown` files when that directory exists.
Config can add more workspace-relative roots with `agentDirectories` /
`agent_directories`; Runtime v2 does not scan global home directories in this
phase.

Markdown agent files use optional `---` frontmatter plus a markdown body. The
frontmatter parser is deliberately small and standard-library only: it supports
scalar strings, numbers, booleans, shorthand lists such as `skills: [review,
docs]`, and one-level nested mappings such as:

```yaml
tools:
  write: false
permission:
  edit: ask
```

The body becomes `AgentProfile.prompt`. The filename is the default agent name,
for example `review.md` becomes `review`; frontmatter `name` can override it.
Hidden subdirectories under an agent directory are skipped, but configured hidden
roots such as `.opencode/agents` are valid.

`agent` is accepted as a singular alias for `agents`. `agent` / `agents` may be
a mapping or a list. Mapping entries use the mapping key as the profile name
unless a `name` field is supplied; list entries require `name`. Agent fields map
to `AgentProfile` as `name`, `description`, `prompt`, `tools`, `maxIterations` /
`max_iterations` / `steps` / `maxSteps`, and `skills` / `active_skills`.
`tools` can be a bool mapping or a list, with list entries treated as enabled.
The metadata fields `mode` (default `all`), `model`, `temperature`, `top_p` /
`topP`, `permission`, `task`, `hidden`, `color`, and `disable` / `disabled` are
kept in `AgentProfile.metadata`; other unknown agent fields are preserved under
`metadata["raw_config"]`. Disabled agents are not added to the registry.
When `include_defaults=True`, config loading seeds the registry with built-in
profiles before reading workspace profiles: `general`, `build`, `plan`,
`explore`, and `scout`. These profiles use normal `AgentProfile` fields:
instructions live in `prompt`, `metadata["mode"]` identifies the profile mode,
and `metadata["built_in"] = True` marks their source. `plan` and `scout` carry
read-only `metadata["permission"]` overlays, while `explore` asks before
mutating tool categories. Those permissions use the Phase18 profile overlay
mechanism described below; they do not add provider/model routing or protocol
integration. Built-in plan/explore/scout overlays cover both legacy EFP tool ids
and opencode-style aliases for mutating tools, so switching between names such
as `write_file` / `write` or `shell_exec` / `bash` does not bypass the profile
policy.

The registry merge order is predictable: built-in profiles are loaded first,
discovered markdown agents are loaded next in stable directory/file order and
override same-name built-ins, and config `agent` / `agents` entries override
both markdown and built-ins. A disabled config entry removes an earlier profile
with the same name, including a built-in. `include_defaults=False` skips
built-ins and default agent directories. `defaultAgent` / `default_agent`
selects the registry fallback when configured; otherwise the fallback is
`general` only when built-ins are included.

Keys that Runtime v2 does not consume in this phase, including root-level
`model` and `plugins`, are preserved in
`RuntimeConfigLoadResult.metadata["unconsumed_config"]`; the full merged object
is preserved in `metadata["raw_config"]` and `RuntimeConfigLoadResult.raw`.
Loading config is side-effect free: it only reads local files, does not start
subprocess tool providers, does not load Portal, and does not instantiate LLM or
tool providers. Agent metadata such as `mode`, `model`, and `hidden` is
informational here; it does not switch providers, change `RuntimeConfig`, or
start subagents. Agent `permission` metadata is preserved and is applied only
when that profile is selected for a primary or child run.

## Session Checkpoints

Runtime v2 session stores support explicit checkpoint and restore operations for
callers that need to bracket a group of tool executions or review steps. A
checkpoint records checkpoint metadata plus a snapshot of the structured session
history. It is a Runtime v2 session-history snapshot only: it does not capture
git state, workspace files, tool output archives, or any other filesystem state.

Checkpoint metadata is stored with the checkpoint record, not in provider
requests. It does not change compaction behavior or alter the serialized
message/part shape. A checkpoint can snapshot the current full session or the
history truncated through a specific message id. Restoring a checkpoint replaces
the current session history while preserving the same session id and message/part
bindings, so callers can continue with `run(...)` or `resume(session_id)` from
the restored history.

Workspace file snapshots are a separate Runtime v2 facility. They are exposed as
opt-in `AgentRuntime` APIs that capture regular files under a configured
`workspace_root` into an in-memory `WorkspaceSnapshotStore`, with diff, restore,
list, and delete operations. They do not alter session-history checkpoints, and
session-history checkpoints do not include workspace file bytes. In this phase,
workspace snapshots are never created or restored automatically around each tool
call; callers choose the bracket points explicitly.

## Provider Projection

Runtime v2 keeps provider boundaries explicit. `efp_runtime.llm.openai`
projects a provider-neutral `ProviderRequest` into plain OpenAI-compatible
dictionaries for Chat Completions or Responses transports. The projection layer
does not import an OpenAI SDK, does not call the network, and keeps tool calls,
tool results, context, attachments, and compaction summaries traceable in the
payload and metadata.

`efp_runtime.llm.provider.OpenAICompatibleProvider` is the transport facade for
that projection. It implements the Runtime v2 provider boundary by building the
OpenAI-compatible payload, calling an injected async `ProviderTransport`, and
returning either the raw non-stream response for loop normalization or a stream
of normalized `LLMEvent` values through `DefaultLLMEventAdapter`. Transport
failures are mapped to provider error responses so the loop can finish with an
error status without depending on SDK exception types.

When `RuntimeRequest.metadata["requested_model"]` is a non-empty string,
`OpenAICompatibleProvider` uses it as the outgoing payload `model` for Chat
Completions or Responses projection. This does not mutate the provider's
constructor `model` and does not switch provider instances, endpoint, transport,
tools, schemas, mode, or sampling settings. Generic providers can continue to
treat `requested_model` as run metadata.

Provider exceptions are provider-neutral. Runtime v2 exposes
`ProviderError`, `ProviderTransientError`, `ProviderContextOverflowError`, and
`ProviderFatalError` with `retryable`, `code`, and `metadata` fields, but does
not bind them to any SDK error classes. `RuntimeLoopRunner` retries transient
provider invocation failures up to `RuntimeConfig.provider_max_retries`, with
optional exponential backoff from
`provider_retry_backoff_seconds` and `provider_retry_backoff_multiplier`.
Each retry publishes a `provider.retry` runtime event and annotates retry
requests with `provider_retry` metadata. Fatal or non-retryable provider errors
go directly through the normal provider error path.

## Tools And Permissions

`ToolRuntime` provides the single tool execution path:

1. Look up the tool definition.
2. Validate arguments.
3. Evaluate permissions.
4. Execute only on allow.
5. Normalize output into `ToolResult`.

Actual execution attempts emit deterministic lifecycle events through
`ToolResult.events`. After lookup, validation, permission allow, and the
pre-execution cancellation check pass, the runtime emits `tool.started`. The
runtime measures the attempt with `time.monotonic()` and adds `duration_ms` to
the normalized `ToolResult.metadata` and terminal lifecycle event. Returned
results end with the existing `tool.completed` event, whose payload includes
`tool_id`, `tool_call_id`, available `session_id`, `run_id`, and `iteration`,
plus `status`, `success`, and `duration_ms`. If the tool callable raises, the
runtime returns the existing error result shape with `tool.started` followed by
`tool.error`; that error payload includes `error`, `error_type`, and
`duration_ms`. Tool-supplied events are preserved in their original order
between `tool.started` and `tool.completed`. Lifecycle payloads avoid raw
argument values; `tool.started` may include only sorted `arg_keys`.

Runtime v2 also supports a transport-independent external tool provider bridge
under `efp_runtime.tools.external`. A provider declares `ExternalToolSpec`
records and implements `execute(tool_name, args, context)`. The bridge converts
those specs into ordinary `ToolDef` entries and registers them in the same
`ToolRegistry` used by built-in tools. Once registered, external tools are
rendered in the same provider request schema and use the same argument
validation, permission broker, enabled/disabled selection, output policy, and
`ToolResult` normalization path as built-ins.

This bridge is the Runtime v2 entry point for project custom tools and
enterprise internal tools. It intentionally does not load JavaScript or
TypeScript plugins or start subprocess tool hosts in this phase.
`ExternalToolContext` carries the session id, message/tool call ids, workspace
root, copied runtime metadata, provider name, and provider-local tool name so
providers can receive session and worktree context without mutating the original
`ToolContext`.

Runtime v2 applies a unified model-visible output policy during normalization.
Large tool outputs are truncated by line and UTF-8 byte limits before they are
added to context. When a workspace-backed `AgentRuntime` creates the
`ToolRuntime`, the complete output is archived under
`.efp_runtime/tool-output` inside the workspace, and the visible tool result is
replaced with a preview plus a note that points at the saved output. If
`ToolResult.metadata` contains `output_path`, the model or caller can inspect
specific sections later with `read_file` line ranges or `grep` instead of
reading the full large file back into context. Large files and files referenced
by `output_path` should be read incrementally with `read_file` `offset` and
`limit` unless the full content is intentionally needed. If the resolved tool
registry includes an enabled `task` tool in the base config, the archived-output
hint instead recommends using that tool to have an explore or research subagent
inspect the saved file with `grep` and ranged `read` calls. Tools that
explicitly set their own truncation metadata are treated as already normalized
and are not truncated a second time.

Mutating file tools return model-readable diagnostics in addition to structured
metadata. `edit` returns a successful `ToolResult` with the path, replacement
count, old and new byte counts, changed/no-op state, and a bounded unified diff
preview generated with the Python standard library. `apply_patch` returns the
changed workspace-relative paths, process stdout/stderr/exit code, changed file
count, and a bounded preview of the submitted patch. Patch failure results keep
the same structured shape and include the error message, paths, exit code, and
stderr preview in model-visible content. These previews default to 200 lines and
12000 characters and remain subject to the Runtime v2 tool output truncation
policy for very large final tool results.

Successful text-file mutations also attach structured file diff records for
downstream review workflows. `write`, `write_file`, and `edit` place a
`filediff` record in both `ToolResult.output` and `ToolResult.metadata`;
`apply_patch` places a `filediffs` list in both locations and adds `filediff`
only when exactly one file changed. Each record carries the workspace-relative
path, previous path for moves, added and removed line counts from the actual
before/after text, and the bounded unified diff text already exposed by the
tool.

Validation errors and permission denies return structured tool results and do
not execute the tool callable. A low-level ASK decision is represented as a
`permission_requested` `ToolResult`, but the Runtime v2 loop treats that result
as an interactive pause rather than appending it to session history.

The default permission evaluator is a `PermissionBroker`. ASK decisions create a
deterministic `PermissionRequest`, store it in the broker's pending set, and
return `permission_requested` with the full request payload in
`ToolResult.metadata["permission_request"]`. When the loop sees that status it
publishes `tool.permission_requested`, finishes the run as
`waiting_for_permission`, and leaves the assistant tool call unpaired. After the
caller approves or denies the request, `resume(session_id)` executes that same
pending assistant tool call before making the next provider request and does not
append an empty user message. Approving with `always=False` creates a one-use
allow rule for the matching retry, while `always=True` creates a persistent
allow rule for the same tool/category. Deny follows the same rule scope model
and is appended as a final tool result on resume.

`RuntimeConfig.tool_permissions` adds an opencode-style execution permission
layer on top of the static permission metadata carried by each tool definition.
Keys can be exact tool ids such as `bash`, `shell_exec`, `read`, `read_file`,
`write`, `write_file`, `apply_patch`, `skill`, or `task_status`; category
aliases such as `bash`, `edit`, `read`, `list`, `grep`, `glob`, `task`,
`todowrite`, `webfetch`, `lsp`, `skill`, `question`, and `doom_loop`; wildcard
patterns such as `external_*`; or `*` as a fallback. Values can be `"allow"`, `"ask"`, or
`"deny"`, or a mapping like
`{"action": "ask", "reason": "...", "risk": "medium", "patterns": ["..."]}`.
For tools whose permission applies to an argument value, config also accepts
nested subject-pattern maps. For example, `{"skill": {"*": "allow",
"internal-*": "deny"}}` matches the requested skill name; `task` matches
`subagent_type`; and `webfetch` matches the requested URL for both `fetch` and
`webfetch`.
Runtime config matching is ordered by exact tool id, wildcard specificity,
category/metadata category, `*`, then the tool definition's original
`PermissionMetadata`; each stage checks direct rules before subject-pattern
rules. When an agent profile selected for a run has
`metadata["permission"]`, that normalized profile permission map is carried in
run metadata as `agent_permission_overlay` and is evaluated after
`RuntimeConfig.tool_permissions`. The profile overlay wins for any matching
exact, category, wildcard, or fallback rule in that run; runs without a selected
profile overlay keep the runtime-level behavior unchanged.

This config controls execution permission for tools. It does not remove tools
from provider schemas. `enabled_tools`, `disabled_tools`, per-run `tools={...}`,
and plan-mode read-only selection still control what tools are visible to the
provider. The skill surface is subject-filtered by the same
`tool_permissions["skill"]` rules used for execution: only a selected `"deny"`
action hides a skill from the `skill` description, `skill_list`, and active
skill context injection. `"allow"`, `"ask"`, and no match keep the skill
visible. Profile `tools` metadata participates only in schema selection, while
profile `permission` metadata participates only in execution permission
decisions. For example, `tool_permissions={"edit": "deny"}` keeps edit tools in
the schema but denies execution; `disabled_tools=["edit"]` or profile
`tools={"edit": false}` hides the `edit` tool from the provider request.
Configured `"ask"` decisions still create normal `PermissionBroker` pending
requests, so `pending_permissions()`,
`approve_permission(...)`, `deny_permission(...)`, and `resume(...)` keep the
same flow as static ASK permissions.

Runtime v2 applies model-aware selection for mutating file tools when callers
provide a model hint in run metadata. The lookup checks `model`, `model_id`,
`requested_model`, and `provider_model`; command expansion may set
`requested_model` when a command declares a model. With
`RuntimeConfig.model_aware_tool_selection=True` (the default), GPT-style ids
containing `gpt-` but not `gpt-4` or `oss` expose `apply_patch` and hide
`edit`, `write`, and `write_file`. Any other non-empty hint exposes the direct
file tools and hides `apply_patch`. Without a hint, selection is unchanged.
The provider request metadata records the model hint, selected mode
(`patch`, `direct`, or `none`), and tool ids forced disabled by this policy.

Runtime v2 also pauses likely tool-call doom loops before executing the next
tool. By default, if the same assistant tool call is requested three times in a
row with the same stable JSON arguments, the loop asks for permission with
category `doom_loop` and finishes as `waiting_for_permission` without appending
a tool result for that pending call. If the caller approves and then calls
`resume(session_id)`, the pending tool call is executed and the loop continues
to the next provider iteration. Set
`RuntimeConfig(doom_loop_threshold=None)` to disable this guard.

Runtime v2 supports a minimal `plan` runtime mode alongside the default `build`
mode. In plan mode, `AgentRuntime` registers the `plan_exit` built-in tool by
default and, while `RuntimeConfig.plan_mode_read_only=True`, hides mutating
tools from the provider request schema: `apply_patch`, `edit`, `write_file`,
`write`, `shell_exec`, `bash`, `shell_kill`, `task`, and `task_cancel`. The set
intentionally includes legacy EFP ids and opencode-style aliases, and may name
tools that are not present in a particular registry. Registered mutating tools
remain in the underlying registry so the policy is enforced through tool
selection rather than by changing registry shape. Caller-supplied
`disabled_tools` still apply, and caller-supplied `enabled_tools` cannot expose
those mutating tools unless `plan_mode_read_only=False`.

`plan_exit` lets the model submit a final structured plan. Its `ToolResult`
contains the plan, status, summary, next steps, and risks in `output`, and marks
`ToolResult.metadata["terminal"] = True` with
`terminal_reason="plan_exit"`. The loop appends that tool result to session
history like any other successful tool result, emits a `tool_terminal` runtime
event, finishes the run as `completed`, and does not make another provider
request. The final assistant message remains the assistant message that made the
tool call; the runtime does not synthesize assistant text after a terminal tool.

Structured output uses the same terminal-tool loop path. A caller can pass an
object schema to `AgentRuntime.run(..., output_schema={...})`, or set
`RuntimeConfig.structured_output_schema`, to expose a temporary
`StructuredOutput` tool for that run. The tool is copied into a per-run
`ToolRuntime`, so it is not part of the default core registry and does not
persist as a global built-in. When the model calls `StructuredOutput`, Runtime
v2 validates the arguments with the normal `ToolDef.validate_args(...)` schema
validator, appends a successful terminal tool result with
`terminal_reason="structured_output"`, copies the validated object into
`RuntimeLoopResult.structured_output`, and stops without another provider
request. While structured output is active, the provider request also receives a
transient system reminder instructing the model to call `StructuredOutput`
instead of replying with plain text; that reminder is never written to session
history. If the run completes with assistant text and no structured-output tool
call, the loop preserves the normal assistant history but returns
`status="error"` and emits `structured_output.missing`. The same strict
conversion applies when a required structured-output run reaches
`max_iterations` without a valid terminal `StructuredOutput` result, including
after a validation error from that tool. Runs that stop for permission,
question, or cancellation keep their waiting or cancelled status. The missing
event records the run id, structured-output tool id, iteration count, and prior
loop status before conversion.

The core built-in registry is workspace-contained and intentionally independent
from the legacy runtime. It preserves EFP ids while exposing opencode-style
aliases: `read_file` / `read`, `write_file` / `write`, `fetch` / `webfetch`,
and `todo_write` / `todowrite`. The legacy ids and their behavior remain
available. The registry also includes grep/glob, shell execution, single-file
edit, unified-diff apply_patch, session-local todo planning, invalid-argument
feedback, and HTTP(S) fetch tools. Mutating filesystem tools default to ask
permission; read/search, todo planning, invalid feedback, and fetch tools
default to allow. Fetch tools are categorized as medium-risk network access so
callers can override them to ask permission when needed. `fetch` and `webfetch`
support opencode-style `format` values of `markdown` (default), `text`, and
`html`: HTML responses are rendered to readable text or lightweight Markdown
unless raw HTML is requested, while non-HTML text is returned as decoded text.
Responses are rejected when the declared or actual body exceeds 5 MiB, and
`max_chars` still controls model-visible truncation.

The `todo_write` and `todowrite` ids share one session-local in-memory todo
store. Todo items normalize to `content`, `status`, and `priority`; `status`
accepts `pending`, `in_progress`, `completed`, and `cancelled`, while
`priority` accepts `high`, `medium`, and `low` and defaults to `medium` when
callers omit it. Successful writes return the normalized todos in output and
metadata, include active/completed/cancelled count metadata, and attach a
`todo.updated` runtime event with the current session id, tool id, tool call id,
normalized todos, and the same count fields.

The legacy `read_file` id keeps its original raw text output. The `read` alias
keeps raw selected text in `ToolResult.output["content"]` for callers, while its
model-visible content wraps files in `<path>`, `<type>`, and `<content>` tags
with numbered lines plus explicit end, continuation, or byte-cap markers.
Directory reads keep structured `entries` output and default to 2000 visible
entries when no limit is supplied, with the visible `<entries>` block showing
either the total entry count or the offset needed to continue.

Search tools prefer the local `rg` executable when available, with user
configuration disabled, hidden files included, and `.git` paths excluded.
When `rg` is unavailable they use the standard-library fallback with the same
workspace-relative output shape. `glob` defaults to 100 recent-first paths, and
`grep` preserves the structured `matches`, counts, include filter, truncation
metadata, and grouped readable content expected by Runtime v2 callers.

Foreground `shell_exec` keeps the existing timeout behavior: the runtime waits
for `communicate()`, kills the process on timeout, and returns the collected
stdout, stderr, exit code, timeout flag, and saved full output path. Runtime v2
also exposes the opencode-style `bash` tool id as an alias over the same shell
execution path; `shell_exec` remains registered and unchanged. Long-running
foreground shell commands also receive the run cancellation callback through
their tool context. When cancellation is requested, the shell tool kills the
subprocess, preserves collected stdout/stderr, marks the result as cancelled,
and adds shell metadata noting the user abort. Background
shell commands can be started with either `shell_exec(background=true)` or
`bash(background=true)`. That call still uses the normal shell permission
boundary, starts the process, and immediately returns a `job_id`. Callers read
retained stdout/stderr and exit state with
`shell_status(job_id, offset?, limit?)`, and stop a running job with
`shell_kill(job_id)`. Background shell jobs are intentionally process-local to
one `AgentRuntime` / `ToolRuntime` lifecycle; Runtime v2 does not run a
cross-process daemon and does not restore jobs after VM or process restart.

The `lsp` tool is an optional code-navigation boundary modeled after
opencode-style LSP operations: definitions, references, hover, document and
workspace symbols, implementations, and call hierarchy queries. Runtime v2 does
not start or manage a language server process in this phase. Callers must inject
an `LSPClient` adapter or explicitly enable the tool with
`RuntimeConfig(enable_lsp_tool=True)` / `include_lsp_tool=True`; without an
available client, tool calls return `No LSP client available for this file type.`
The tool remains workspace-contained and validates file paths before calling the
injected client.

The `task` tool is an injectable subagent boundary. It is not enabled by the
core registry unless a caller provides a task runner; when enabled, foreground
`task(background=false)` calls are treated like any other tool and the loop
appends their structured task output as a tool result for the next provider
iteration. If the caller opts in to background tasks and supplies, or lets the
tool create, a `BackgroundTaskManager`, `task(background=true)` starts the same
injected runner with `asyncio.create_task`, immediately returns a process-local
`task_id`, and lets the primary agent continue its loop.

Background task state is observed explicitly with `task_status(task_id?)`.
Without a `task_id`, `task_status` lists known tasks for a session or for the
current runtime process; with `drain=true`, it returns completed/error/cancelled
records that have not already been drained and marks them drained. Running
tasks can be cancelled with `task_cancel(task_id)`, which defaults to ask
permission because it mutates a running background operation. `AgentRuntime`
also auto-injects completed/error/cancelled background task results as
synthetic user messages on the next `run(..., session_id=...)` or
`resume(session_id)` for the parent session, before preparing the next provider
request. This lets the primary agent continue with finished subagent results
without polling `task_status`. The automatic injection path has its own
once-only tracking; `task_status` and `drain_background_tasks(session_id?)`
remain explicit inspection APIs with independent drain semantics.

The `question` tool is an optional first-class interactive pause. It is disabled
by default and can be enabled with
`RuntimeConfig(enable_question_tool=True)` or by passing
`include_question_tool=True` to `create_core_tool_registry`. When the model calls
`question`, the tool creates a `QuestionRequest` in the `QuestionBroker` and
returns `question_requested`. The loop publishes `tool.question_requested`,
finishes the run as `waiting_for_question`, and leaves the assistant tool call
unpaired. The caller answers with `AgentRuntime.answer_question(request_id,
answers)` and then calls `resume(session_id)`. On resume, the same pending
question tool call consumes the answer, appends a successful tool result, and the
loop continues to the next provider iteration without adding an empty user
message. Question requests are independent from permission requests and do not
create pending permissions.

## Agent Profiles And Subagents

Runtime v2 has a small standalone agent profile layer under
`efp_runtime.agents`. `AgentProfile` names a profile and can provide additional
agent instructions, per-run tool overrides, active skills, a child iteration
limit, and metadata. `AgentRegistry` resolves `task.subagent_type` to a profile
and can fall back to a configured default profile, usually `general`.
`efp_runtime.agents.discovery` loads opencode-style markdown agent files into
the same `AgentProfile` shape used by config and subagent task runners, so
primary-agent, subagent, and `@agent` mention selection can share one registry.

`AgentRuntime(agent_registry=..., default_agent=...)` also supports primary run
profile selection. Callers choose a profile with
`AgentRuntime.run(..., agent="review")`, command metadata can request a profile,
the first effective user prompt line can start with `@review`, callers can pass
an `AgentProfile` directly with `agent=profile`, or `default_agent` can resolve
through the supplied registry when nothing else selects a profile. Selection
precedence is caller `agent=...`, command `agent` metadata, resolved `@agent`
mention, then `default_agent`. Runtime v2 does not load profile files from disk
inside the facade; config and agent discovery are separate caller concerns.

Primary-run `@agent` mention parsing happens after `/skill` command lines are
removed and after slash command expansion. The parser inspects only the first
effective non-empty line and treats a leading token shaped like `@name` as a
candidate only when it is followed by whitespace or the end of that line. If the
candidate resolves to a profile in the supplied `agent_registry`, and no caller
or command already selected a profile, Runtime v2 strips that mention token
from the prompt before provider rendering and before prompt reference
resolution. The remaining text on the line and following prompt text are
preserved. Metadata records `agent_mention` and
`selected_agent_source="mention"` only when a mention selects a profile.
Unknown `@name` tokens remain ordinary prompt text.

For primary runs, a selected profile prompt is transient provider-only system
context. It is inserted after the base system prompt stack, including runtime
reminders, and before workspace instruction context, active skill context, and
persisted session history. The profile prompt is not appended to the session
store. Profile `tools`, `max_iterations`, and `active_skills` are per-run
overrides: command `tools` metadata, when a command expands, is merged over
profile tool entries, and caller-supplied `tools={...}` wins over both. Profile
`max_iterations` changes only the current loop limit, and profile
`active_skills` are the base active skills for that run without leaking into
the runtime instance's long-lived active skill list. `/skill` commands still
add to or clear that run's profile skill base. Profile
`metadata["permission"]` is normalized into `agent_permission_overlay` run
metadata and overlays `RuntimeConfig.tool_permissions` for tool execution in
that run. Profile metadata also records future routing hints such as `model`,
`mode`, or `temperature`, but those hints do not switch provider, model, mode,
or sampling settings.

`create_subagent_task_runner(...)` builds the task runner used by the injectable
`task` tool. The runner does not start a legacy agent or separate process. It
creates a child `AgentRuntime`, constructs a traceable child session id from the
parent session id and task id, prepends the selected profile prompt to the task
prompt, and runs the child loop. Completed child runs return the final assistant
text as the task result. Non-completed child runs and provider/runtime failures
are normalized into `TaskToolResult` with `state="error"` so the parent loop
receives an ordinary tool result rather than an exception.

Child config is derived from the supplied `base_config` without mutating it.
`workspace_root` passed to the runner wins over `base_config.workspace_root`;
profile `max_iterations` wins over the base limit; profile `active_skills`
replace base active skills to avoid implicit skill leakage; and base
enabled/disabled tool settings remain in the child config. Profile
`metadata["permission"]` is normalized, merged into the child
`RuntimeConfig.tool_permissions` with profile keys winning, and carried in child
run metadata as `agent_permission_overlay` so it still takes precedence over
matching base permission rules during execution. Profile `tools` are passed to
`AgentRuntime.run(..., tools=...)` as the per-run schema-selection override.
Child runs also hide recursive `task` and todo-write tools (`todo_write` and
`todowrite`) by default. A profile can expose them only when its own
`metadata["permission"]` overlay contains a non-deny `task`, `todo_write`, or
`todowrite` rule; inherited base permissions and wildcard fallbacks do not opt
in, and explicit `profile.tools={...: false}` entries still hide matching
tools. The core registry still does not register `task` by default; callers must
explicitly wire the runner, for example by passing
`task_runner=create_subagent_task_runner(...)` to `create_core_tool_registry`.

Agent-backed task tool definitions include a deterministic list of visible
subagent profiles in the provider schema description. Hidden profiles and
primary-only `build` / `plan` profiles are omitted; visible task-capable
profiles are listed by name with their description so the model can choose an
appropriate `subagent_type`. Subject-aware `permission.task` rules also filter
this list by profile name: only `deny` hides a subagent entry, while `ask`,
`allow`, and no matching rule keep the current visibility behavior.

`create_agent_task_tool(...)` preserves the historical single-tool helper.
`create_agent_task_tools(..., allow_background=True)` returns `task`,
`task_status`, and `task_cancel` wired to the same `BackgroundTaskManager`, so
callers can register all three definitions together. Background subagent tasks
are intentionally limited to the current process lifecycle. Runtime v2 does not
provide UI child-session navigation, does not persist background task records to
disk, does not run multiprocess workers, and does not integrate with the legacy
runtime/session stack.

## Skills

Skills are discovered from `SKILL.md` or `skill.md` files. Loading a skill reads
markdown plus optional sidecar context. Python sidecar files are treated as text
or binary files; runtime v2 never imports or executes them.

Discovery walks configured skill roots in order. If two packages declare the
same case-insensitive skill name, the later root wins; within one root,
path-sorted discovery order is stable. The final model-facing skill list is
sorted by name after duplicate winners are selected, so configured project
skills can override same-name user-level defaults while provider prompts remain
stable.

Skill markdown supports either `---` frontmatter or the existing compact leading
`name: ...` / `description: ...` header style. Runtime v2 parses only simple
scalar metadata fields from that header, including fields such as `license`,
`compatibility`, `category`, `version`, and `author`; unknown simple scalar
fields are preserved in `SkillPackage.metadata`. It does not parse nested YAML,
lists, block scalars, or sidecar manifests into executable behavior.

`AgentRuntime` can keep an instance-level active skill list from
`RuntimeConfig.active_skills` and `/skill` command lines. Active skill context is
rendered as transient system context in the provider request before session
history. It is not appended to the persisted session store, so repeated runs do
not duplicate skill messages in history.

Runtime v2 exposes `skill_list` and `skill` tools when skill discovery is
configured for the default core tool registry. `skill_list` is the lightweight
registry view: it lists available skill names, descriptions, active skills, and
sidecar path/size/content-type inventory without loading full skill context.
Structured `skill_list` entries include the parsed skill metadata dictionary.
The `skill` tool is the full context loader. Its provider description exposes
available skills in an XML-like `<available_skills>` block with each skill name
and description. Calling `skill({name})` returns a model-visible
`<skill_content name="...">` block with the skill markdown, base-directory
guidance, and sampled sidecar inventory as `<file>` entries. The structured
output keeps the skill name, description, skill file, raw skill markdown,
sidecar inventory, and metadata for programmatic consumers.

These tools complement, rather than replace, `/skill`: `/skill` explicitly
activates provider-only system context before the provider call, while
`skill_list` lets the model discover candidate skills during the loop and
`skill` lets it load one discovered skill by name on demand. Active skills are
reported in run metadata and `skill_list` output, but active skill context
remains transient provider-only system context and is not persisted.

Skill tools are read-only context loading. They never import or execute sidecar
files, including Python files. `skill_list` reports whether sidecars are text or
binary but never returns sidecar bodies; `skill` lists sampled sidecar files in
`<skill_files>` by default, and callers may request bounded text content for
sidecars, subject to the configured maximum character limit.

## Custom Commands

Runtime v2 supports custom slash command directories through
`RuntimeConfig.command_directories` and explicit `CommandRegistry` injection on
`AgentRuntime`. Command files are markdown or text prompt templates discovered
from those directories, including hidden configured roots such as
`.opencode/command`; hidden subdirectories are skipped by default. With default
discovery enabled, the config loader discovers existing command directories in
this order: `~/.config/opencode/commands`, workspace-local `.opencode/command`,
workspace-local `.opencode/commands`, then configured `commandDirectories` /
`command_directories`. Later discovered markdown commands with the same
normalized name override earlier ones, so project commands override global user
commands, plural project commands override singular project commands, and
configured command directories override all defaults. The config loader also
supports opencode-style `command` and compatible `commands` mappings:

```json
{
  "command": {
    "test": {
      "template": "Run tests for $ARGUMENTS",
      "description": "Run tests",
      "agent": "build",
      "model": "provider/model",
      "subtask": false
    },
    "review": "Review $1"
  }
}
```

Config commands are registered before directory commands. Later discoveries
with the same normalized name replace earlier ones, so project markdown command
files can override config commands.

When command expansion is enabled, `AgentRuntime.run(...)` checks the first
effective non-empty user line after `/skill` lines have been parsed. A discovered
command such as `/fix bug-123` is expanded into the current user prompt with the
command content, command arguments, and any remaining body text. This is user
prompt expansion only: it is not a tool call, does not create persisted system
prompt state, does not mutate active skills, and does not call the legacy
runtime/session stack. Unknown slash commands remain ordinary user text.

Command expansion happens before final primary-run profile resolution. The
expanded prompt text is the user text sent through the normal
`AgentRuntime.run(...)` path. If the expanded command has `agent` metadata and
the caller did not pass `agent=...`, Runtime v2 resolves that profile for this
run. Caller-supplied `agent=...` wins over command metadata, command metadata
wins over resolved `@agent` mentions, and `default_agent` is used only when no
caller, command, or mention selected a profile. Unknown command agents fail
before any provider request, using the same unknown-agent error as ordinary
`AgentRuntime.run(..., agent="...")` selection. `selected_agent_source` is
recorded as `caller`, `command`, `mention`, or `default` whenever a profile is
selected.

Before the command content is placed inside the visible `<command>` block, the
template renderer replaces `$ARGUMENTS` with the full argument string and
positional variables such as `$1` and `$2` with shell-like positional arguments
parsed by `shlex.split`, falling back to whitespace splitting on malformed
quotes. Missing positional variables become empty strings, and unknown variables
such as `$HOME` are preserved. The expansion still includes
`<command_arguments>` and `<command_input>` blocks so the model can see the raw
arguments and remaining body text.

Command metadata flows into run metadata as `command_source`, optional
`command_agent`, optional `command_model`, optional `command_subtask`, and a
copied `command_metadata` object. When command `model` is present, Runtime v2
also records `requested_model`. For generic providers that field remains
metadata only. `OpenAICompatibleProvider` honors it when building the outgoing
payload `model`, but it does not switch provider instances, endpoint, transport,
tools, schemas, mode, or sampling settings. `subtask` is recorded only; it does
not start a subagent in this phase.

Command `tools` metadata is treated as a per-run tool override base. A list such
as `tools: [read_file, edit]` enables those tools, equivalent to
`{"read_file": true, "edit": true}`. A mapping such as
`tools: {"read_file": true, "write_file": false}` is used as explicit boolean
overrides. Non-list and non-mapping `tools` metadata raises `ValueError` before
the provider call. Per-run tool merge order is profile tools first, then command
tools, then caller `AgentRuntime.run(..., tools={...})`; later entries win.

`@file` references inside command templates are not read by the commands module.
They remain in the expanded user prompt and are resolved later by the Runtime v2
prompt reference resolver when `RuntimeConfig.resolve_prompt_references=True`.
The `@agent` mention parser is not a file-reference resolver: unresolved
mentions such as `@README.md` or unknown `@name` tokens are left in the prompt
so the prompt reference resolver and ordinary text handling keep their existing
behavior.
Shell interpolation is supported only inside command template content after
argument rendering and command-content truncation. A template line whose first
non-space character is `!` runs the rest of that line, and inline
``!`cmd` `` spans run the command between the backticks. Shell-looking text in
slash command arguments or the remaining user body stays ordinary text in the
`<command_arguments>` and `<command_input>` blocks. Interpolation executes
through the normal `shell_exec` tool path, so validation, permissions,
cancellation, output normalization, and tool lifecycle events use the same
runtime path as model-requested shell calls.

`/skill` remains the independent skill activation command. It is parsed before
custom command expansion and continues to control active skill context rendered
as provider-only system context.

## System Prompt Stack

Runtime v2 has a small configurable system prompt stack. By default,
`AgentRuntime` adds a stable base code-agent prompt, then optional explicit
`RuntimeConfig.system_prompt_texts` and UTF-8 workspace-local
`RuntimeConfig.system_prompt_paths`. It can also add runtime reminders for the
current iteration limit, the optional `question` tool, plan mode, and saved
truncated tool output referenced by `output_path`.

The default prompt is provider-only coding-agent context. Runtime reminders are
separate transient system messages driven by request metadata, so per-run loop
guidance can be added or omitted without changing configured prompts. System
prompt and reminder messages are not appended to the session store, are not
copied into user messages, and are rebuilt for each `run()` or `resume()`
request.
Plan mode does not persist extra system prompt text either; only ordinary
user, assistant, and tool history is stored.

The full provider request context order is:

1. System prompt and runtime reminder messages.
2. Workspace instruction messages.
3. Active skill messages.
4. Persisted session history.

## Instructions

Runtime v2 loads workspace instruction files from `AGENTS.md`, `CLAUDE.md`, and
`CONTEXT.md`, plus any explicit `RuntimeConfig.instruction_paths` and
`RuntimeConfig.instruction_texts`. These are rendered as transient system
context in the provider request after the system prompt stack, before active
skill context, and before persisted session history.

Instruction context is not appended to the session store and is not copied into
user messages. Each run rebuilds the provider-only context from the configured
sources, so persisted history remains limited to user, assistant, tool, task,
and compaction records created by the runtime loop.

Runtime v2 also supports read-time nearby instruction attachment for the
workspace `read_file` tool and the opencode-style `read` file alias.
Request-time instruction context is provider-only system context that is rebuilt
at the start of a run or resume. Read-time attachment is tool output context:
when either tool reads a workspace file, it walks from that file's parent
directory up to `workspace_root` and attaches the nearest default instruction
file in each directory, using `AGENTS.md`, `CLAUDE.md`, then `CONTEXT.md`
priority. It skips the file being read and does not scan global home
directories or fetch remote instruction sources.

When called without `offset` or `limit`, `read_file` keeps the original
structured output shape and returns the full decoded text. The opencode-style
`read` file alias is bounded by default: it starts at 1-based `offset`, returns
up to `limit` lines when provided or 2000 lines otherwise, caps visible content,
and exposes `next_offset` for continuation. For both tools, ranged reads add
metadata such as `start_line`, `end_line`, `total_lines`, `line_count`,
`has_more`, `next_offset`, `range_truncated`, and `returned_bytes`. When nearby
instructions are found, the output additionally contains `instructions` and
`loaded_instruction_paths`; each instruction entry contains the
workspace-relative path, content, truncation flag, and original character count.
`RuntimeConfig(attach_read_instructions=False)` disables this read-time
attachment independently of `include_default_instructions`, which only controls
request-time system instruction injection.

The workspace search tools also favor bounded model-readable output. `grep`
accepts an optional `include` file glob, returns recent-file-first grouped
matches with `Found ...` summaries, and keeps structured match metadata.
`glob` returns recent-first workspace-relative paths. Both default to 100
visible results and include truncation hints when more results remain.


## Compaction

Compaction operates on message parts. Tool calls and their matching results are
grouped before deciding what to keep or summarize, so compaction never leaves
one side of a tool call/result pair in the retained history.

Runtime v2 can compact by either part count or an approximate character budget.
The character budget is deterministic: text, reasoning, errors, tool arguments,
tool results, and structured context metadata are counted without invoking an
LLM summarizer. System prompt, instruction, and skill context messages are
retained, pending tool calls and the latest non-system block are protected, and
provider request metadata records the configured budget plus compacted/kept
part, message, pair, and character counts.

`AgentRuntime.compact_session(...)` provides manual persistent compaction for
stored session history. Unlike request-local budget compaction, which only
changes the provider request for a single turn, manual compaction replaces older
stored messages with a persisted compaction summary message. Future
`read_history` calls and later turns see the compacted form. Forced manual
compaction uses the same part-aware rules with a very small part budget when no
explicit budget is supplied, so pending tool calls and the latest ordinary block
remain in history while older compactable messages become summary context.

Compaction summaries use anchored Markdown headings in a stable order: goal,
constraints, progress, decisions, next steps, critical context, and relevant
files. Custom summarizers receive a bounded structured prompt with compacted
source, retained source, counts, and the latest previous summary when present,
so repeated compactions update the prior summary instead of starting from an
empty note.

If provider invocation raises `ProviderContextOverflowError` and
`RuntimeConfig.enable_context_overflow_retry` is enabled, the same loop
iteration is rendered once more with a stricter budget and retried. Existing
part-aware compaction rules still protect pending tool calls and the latest
non-system block. The overflow retry is single-shot to avoid infinite loops, and
the retried request records `overflow_retry` metadata on the request and
compaction metadata while the loop publishes a
`provider.context_overflow_retry` event.
