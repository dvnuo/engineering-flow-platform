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

Each loop iteration builds a `RuntimeRequest` for the provider. It keeps the raw
`Message` history for compatibility, and also carries a rendered
`ProviderRequest`, the `PreparedProviderRequest` with compaction metadata, and
the sorted `ToolDef` list used to render provider-neutral tool schemas.

`STEP_FINISH` updates the active assistant message. Tool result events append a
separate tool message and do not make that tool message the active assistant
message, so later usage and completion state are not attached to the wrong
message.

## Runtime Facade

`efp_runtime.runtime.AgentRuntime` is the high-level Runtime v2 facade. It wires
an injected provider to an in-memory session store, `ToolRuntime`, context
rendering, compaction, and the loop runner. When configured with a workspace root
and no explicit tool registry/runtime, it creates the Runtime v2 built-in tool
registry for that workspace. The facade does not call Portal or the legacy agent
runtime.

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

## Tools And Permissions

`ToolRuntime` provides the single tool execution path:

1. Look up the tool definition.
2. Validate arguments.
3. Evaluate permissions.
4. Execute only on allow.
5. Normalize output into `ToolResult`.

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

The core built-in registry is workspace-contained and intentionally independent
from the legacy runtime. It includes read/list/write, grep/glob, shell execution,
single-file edit, unified-diff apply_patch, and a session-local todo_write
planning tool. Mutating filesystem tools default to ask permission; read/search
and todo planning tools default to allow.

The `task` tool is an injectable foreground subagent boundary. It is not enabled
by the core registry unless a caller provides a task runner; when enabled, the
loop treats it like any other tool and appends its structured task output as a
tool result for the next provider iteration. Runtime v2 does not implement
background task synthetic-message injection yet; `background=true` is rejected
with an explicit unsupported error by default.

## Agent Profiles And Subagents

Runtime v2 has a small standalone agent profile layer under
`efp_runtime.agents`. `AgentProfile` names a profile and can provide additional
agent instructions, per-run tool overrides, active skills, a child iteration
limit, and metadata. `AgentRegistry` resolves `task.subagent_type` to a profile
and can fall back to a configured default profile, usually `general`.

`create_subagent_task_runner(...)` builds the foreground task runner used by the
injectable `task` tool. The runner does not start a legacy agent, background
worker, or separate process. It creates a child `AgentRuntime`, constructs a
traceable child session id from the parent session id and task id, prepends the
selected profile prompt to the task prompt, and runs the child loop. Completed
child runs return the final assistant text as the task result. Non-completed
child runs and provider/runtime failures are normalized into `TaskToolResult`
with `state="error"` so the parent loop receives an ordinary tool result rather
than an exception.

Child config is derived from the supplied `base_config` without mutating it.
`workspace_root` passed to the runner wins over `base_config.workspace_root`;
profile `max_iterations` wins over the base limit; profile `active_skills`
replace base active skills to avoid implicit skill leakage; and base
enabled/disabled tool settings remain in the child config. Profile `tools` are
passed to `AgentRuntime.run(..., tools=...)` as the per-run override. The core
registry still does not register `task` by default; callers must explicitly wire
the runner, for example by passing
`task_runner=create_subagent_task_runner(...)` to `create_core_tool_registry`.

Only foreground subagent tasks are supported in this phase. Background tasks,
real multiprocess workers, and legacy runtime/session integration remain
unsupported.

## Skills

Skills are discovered from `SKILL.md` or `skill.md` files. Loading a skill reads
markdown plus optional sidecar context. Python sidecar files are treated as text
or binary files; runtime v2 never imports or executes them.

`AgentRuntime` can keep an instance-level active skill list from
`RuntimeConfig.active_skills` and `/skill` command lines. Active skill context is
rendered as transient system context in the provider request before session
history. It is not appended to the persisted session store, so repeated runs do
not duplicate skill messages in history.

## Compaction

Compaction operates on message parts. Tool calls and their matching results are
grouped before deciding what to keep or summarize, so compaction never leaves
one side of a tool call/result pair in the retained history.

Runtime v2 can compact by either part count or an approximate character budget.
The character budget is deterministic: text, reasoning, errors, tool arguments,
tool results, and structured context metadata are counted without invoking an
LLM summarizer. System and skill context messages are retained, pending tool
calls are protected, and provider request metadata records the configured
budget plus compacted/kept part, message, pair, and character counts.
