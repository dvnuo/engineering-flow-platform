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
`limit` unless the full content is intentionally needed. Tools that explicitly
set their own truncation
metadata are treated as already normalized and are not truncated a second time.

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
`shell_exec`, and `shell_kill`. These tools remain registered in the underlying
registry so the policy is enforced through tool selection rather than by
changing registry shape. Caller-supplied `disabled_tools` still apply, and
caller-supplied `enabled_tools` cannot expose those mutating tools unless
`plan_mode_read_only=False`.

`plan_exit` lets the model submit a final structured plan. Its `ToolResult`
contains the plan, status, summary, next steps, and risks in `output`, and marks
`ToolResult.metadata["terminal"] = True` with
`terminal_reason="plan_exit"`. The loop appends that tool result to session
history like any other successful tool result, emits a `tool_terminal` runtime
event, finishes the run as `completed`, and does not make another provider
request. The final assistant message remains the assistant message that made the
tool call; the runtime does not synthesize assistant text after a terminal tool.

The core built-in registry is workspace-contained and intentionally independent
from the legacy runtime. It includes read/list/write, grep/glob, shell execution,
single-file edit, unified-diff apply_patch, session-local todo_write planning,
invalid-argument feedback, and HTTP(S) fetch tools. Mutating filesystem tools
default to ask permission; read/search, todo planning, invalid feedback, and
fetch tools default to allow. The fetch tool is categorized as medium-risk
network access so callers can override it to ask permission when needed.

Foreground `shell_exec` keeps the existing timeout behavior: the runtime waits
for `communicate()`, kills the process on timeout, and returns the collected
stdout, stderr, exit code, timeout flag, and saved full output path. Long-running
shell commands can instead be started with `shell_exec(background=true)`. That
call still uses the normal shell permission boundary, starts the process, and
immediately returns a `job_id`. Callers read retained stdout/stderr and exit
state with `shell_status(job_id, offset?, limit?)`, and stop a running job with
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

The `task` tool is an injectable foreground subagent boundary. It is not enabled
by the core registry unless a caller provides a task runner; when enabled, the
loop treats it like any other tool and appends its structured task output as a
tool result for the next provider iteration. Runtime v2 does not implement
background task synthetic-message injection yet; `background=true` is rejected
with an explicit unsupported error by default.

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

Runtime v2 exposes `skill_list` and `skill` tools when skill discovery is
configured for the default core tool registry. `skill_list` is the lightweight
registry view: it lists available skill names, descriptions, active skills, and
sidecar path/size/content-type inventory without loading full skill context.
The `skill` tool is the full context loader. It returns a model-visible
`<skill_content name="...">` block, and the structured output keeps the skill
name, description, skill file, raw skill markdown, sidecar inventory, and
metadata for programmatic consumers.

These tools complement, rather than replace, `/skill`: `/skill` explicitly
activates provider-only system context before the provider call, while
`skill_list` lets the model discover candidate skills during the loop and
`skill` lets it load one discovered skill by name on demand. Active skills are
reported in run metadata and `skill_list` output, but active skill context
remains transient provider-only system context and is not persisted.

Skill tools are read-only context loading. They never import or execute sidecar
files, including Python files. `skill_list` reports whether sidecars are text or
binary but never returns sidecar bodies; `skill` lists sidecar files in
`<skill_files>` by default, and callers may request bounded text content for
sidecars, subject to the configured maximum character limit.

## System Prompt Stack

Runtime v2 has a small configurable system prompt stack. By default,
`AgentRuntime` adds a stable base code-agent prompt, then optional explicit
`RuntimeConfig.system_prompt_texts` and UTF-8 workspace-local
`RuntimeConfig.system_prompt_paths`. It can also add runtime reminders for the
current iteration limit, the optional `question` tool, plan mode, and saved
truncated tool output referenced by `output_path`.

System prompt and reminder messages are transient provider-only system context.
They are not appended to the session store, are not copied into user messages,
and are rebuilt for each `run()` or `resume()` request.
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
workspace `read_file` tool. Request-time instruction context is provider-only
system context that is rebuilt at the start of a run or resume. Read-time
attachment is tool output context: when `read_file` reads a workspace file, the
tool walks from that file's parent directory up to `workspace_root` and attaches
the nearest default instruction file in each directory, using `AGENTS.md`,
`CLAUDE.md`, then `CONTEXT.md` priority. It skips the file being read and does
not scan global home directories or fetch remote instruction sources.

When called without `offset` or `limit`, `read_file` keeps the original
structured output shape and returns the full decoded text. When either range
argument is supplied, `offset` is a 1-based starting line and `limit` is the
maximum number of lines to return; the output content contains only that text
fragment and adds range metadata such as `start_line`, `end_line`,
`total_lines`, `line_count`, `has_more`, `next_offset`, `range_truncated`, and
`returned_bytes`. When nearby instructions are found, the output additionally
contains `instructions` and `loaded_instruction_paths`; each instruction entry
contains the workspace-relative path, content, truncation flag, and original
character count. `RuntimeConfig(attach_read_instructions=False)` disables this
read-time attachment independently of `include_default_instructions`, which only
controls request-time system instruction injection.


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

If provider invocation raises `ProviderContextOverflowError` and
`RuntimeConfig.enable_context_overflow_retry` is enabled, the same loop
iteration is rendered once more with a stricter budget and retried. Existing
part-aware compaction rules still protect pending tool calls and the latest
non-system block. The overflow retry is single-shot to avoid infinite loops, and
the retried request records `overflow_retry` metadata on the request and
compaction metadata while the loop publishes a
`provider.context_overflow_retry` event.
