# Opencode Parity Checklist

This checklist records the current EFP runtime state against the audited
opencode baseline. It is intentionally conservative: items are marked complete
only when covered by implementation and tests.

| Area | State | Notes |
| --- | --- | --- |
| Core loop/history/provider request | Implemented | `AgentRuntime` and `RuntimeLoopRunner` build typed provider requests, persist typed history, normalize provider events, and emit runtime events. |
| Tools: bash/read/write/edit/apply_patch/grep/glob/webfetch/todowrite | Implemented | These are model-facing built-ins in the default registry. Removed aliases are not registered. |
| Tools: task | Implemented with injected runner | `task` is present by default; production behavior depends on injected runtime collaborators. Background task persistence is intentionally process-local. |
| Tools: question | Conditional | Available only when enabled through runtime config or registry construction. |
| Tools: websearch | Conditional | Available only when a provider-neutral runner is injected. No concrete search provider is bundled. |
| Tools: lsp | Conditional | Available only with an injected LSP client or explicit enable flag. The runtime does not start language servers. |
| Skills discovery/activation/commands | Implemented | Skill discovery reads `skill.md`/`SKILL.md`, `/skill` activates provider-only context, the `skill` tool loads bounded context, and eligible skills can appear as slash commands. Python sidecars are never executed. |
| Session list/delete/fork/revert/summary/query/todos | Implemented | Backed by runtime stores and gateway facade. Active gateway code imports the runtime session facade directly. |
| Context and automatic compaction | Implemented | Provider-only context, workspace instructions, skill context, request-local rendering, deterministic compaction, and optional summarizer control are covered. |
| Permissions and workspace-full-access defaults | Implemented | Built-ins run in the workspace with brokered allow/ask/deny decisions and path escape checks. |
| GitHub Copilot provider/model path | Implemented | Production chat supports Copilot only, with token/base URL environment overrides and no OpenAI/Anthropic/Ollama fallback. |
| Embedded runtime frontend | Removed | Gateway has no root HTML route and no static/template frontend assets. Portal owns UI. |
| Old Python tool loaders | Removed | `src.context_tools`, `src.bash_tools`, `efp_runtime.tools.local`, and `efp_runtime.tools.external` are absent. |
| MCP | Excluded | MCP servers and external protocol tool surfaces are out of scope for this runtime replacement branch. |

## Intentional Remaining Gaps

- Background task state is process-local. Persisting task records across process
  restarts would require a dedicated task store and recovery contract.
- The LSP tool is an adapter boundary only. The runtime does not own language
  server installation, startup, or lifecycle management.
- `websearch` has no bundled provider. Callers must inject a runner so network
  search policy remains explicit.
- Internal runtime tests live under `tests/runtime` and form the current
  runtime test boundary.
