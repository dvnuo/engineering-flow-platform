# EFP Runtime Observability Contract

## Log Context Fields

The runtime log context includes:

- `trace_id`
- `span_id`
- `parent_span_id`
- `request_id`
- `session_id`
- `task_id`
- `portal_task_id`
- `portal_dispatch_id`
- `agent_id`
- `runtime_type`
- `execution_type`
- `source_type`
- `tool_name`
- `tool_source`
- `skill_name`
- `profile_version`
- `path`

## Where Context Is Bound

- Gateway chat endpoints bind path/request/session/agent/runtime/execution/source context before handing the LLM loop to Runtime v2.
- Runtime v2 chat events are returned in `runtime_events` and mirrored into WebChat payloads for request correlation.
- `/api/tasks/execute` continues binding task and portal trace context.
- `ExecutionBus` binds request-scoped context via `set_log_context()` and always resets via `reset_log_context()` in a `finally` block.

## Redaction

- All log messages still pass through `RedactingFilter`.
- third-party logger output does not include EFP trace block context.
- Never put token/password/raw request body values into log context.

## Reviewer Checklist

- One `/api/chat` flow log can correlate request/session/agent context.
- One tool execution flow log includes `tool` and `tool_source`.
- Failed execution paths do not leak context into subsequent requests.
- third-party logger output stays free of EFP trace block fields.
