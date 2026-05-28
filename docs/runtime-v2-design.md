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

## Tools And Permissions

`ToolRuntime` provides the single tool execution path:

1. Look up the tool definition.
2. Validate arguments.
3. Evaluate permissions.
4. Execute only on allow.
5. Normalize output into `ToolResult`.

Validation errors, permission asks, and permission denies return structured
tool results and do not execute the tool callable.

The default permission evaluator is a `PermissionBroker`. ASK decisions create a
deterministic `PermissionRequest`, store it in the broker's pending set, and
return `permission_requested` with the full request payload in
`ToolResult.metadata["permission_request"]`. The runtime does not block inside
the same tool execution call; approving a request with `always=False` creates a
one-use allow rule for the matching retry, while `always=True` creates a
persistent allow rule for the same tool/category. Deny follows the same rule
scope model.

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
