# EFP Runtime Tool Surface Contract

The initial rebuild used opencode as an engineering reference, but this
document records the EFP-owned runtime capability and tool contract. It is not a
continuing upstream compatibility promise. Items are marked implemented only
when covered by EFP runtime implementation and tests.

| Area | State | Notes |
| --- | --- | --- |
| Core loop/history/provider request | Implemented | `AgentRuntime` and `RuntimeLoopRunner` build typed provider requests, persist typed history, normalize provider events, and emit runtime events. |
| Tools: bash/read/write/edit/apply_patch/grep/glob/webfetch/todowrite | Implemented | These are model-facing built-ins in the default registry. Removed aliases are not registered. |
| Tools: task | Implemented with injected runner | `task` is present by default; production behavior depends on injected runtime collaborators. Background task persistence is intentionally process-local. |
| Tools: question | Conditional | Available only when enabled through runtime config or registry construction. |
| Tools: browser | Conditional | Registered when `enable_browser_tool` is set, which the gateway does for interactive chats whose trusted metadata carries an enabled `connectors.local_browser` block (see the Portal `CONNECTORS_CONTRACT.md`). The tool publishes `tool.connector_requested`, waits on the process-wide `ConnectorBridgeBroker`, and returns what the Portal page posts to `/api/sessions/{id}/connectors/respond`. |
| Tools: session_search | Conditional | Registered when `enable_session_search` is set, which the gateway does for every interactive chat (a profile can set it to `false`); background tasks and sub-agents never see it. The tool searches and reads this assistant's other stored sessions in the same session root, indexing member and assistant text only (no tool output or reasoning). `scope=mine` (default) keeps sessions whose member turns carry the Portal member's `author_id`; `scope=agent` covers every session, matching what Portal already shows for the agent. When the tool is offered, the system prompt gains an "Earlier sessions" block listing the member's recent sessions. |
| Tools: memory | Conditional | Registered when `enable_member_memory` is set, which the gateway does for every interactive chat (a profile can set it to `false`). `remember`/`forget`/`list` keep one-sentence standing notes per Portal member (identity from trusted metadata; without one the tool refuses) in `<session root>/memory/<member>.json`, at most 50 notes of 300 characters, written only when the model is asked to. Whenever the tool is offered and a member is known, the system prompt gains a "Member notes" block with the rules and the member's notes. |
| Tools: websearch | Conditional | Available only when a provider-neutral runner is injected. No concrete search provider is bundled. |
| Tools: lsp | Conditional | Available only with an injected LSP client or explicit enable flag. The runtime does not start language servers. |
| Skills discovery/activation/commands | Implemented | Skill discovery reads `skill.md`/`SKILL.md`, `/skill` activates provider-only context, the `skill` tool loads bounded context (documentation listed first, omitted files summarized by directory, `file=` returns one file by relative path), and eligible skills can appear as slash commands. Skill directories are read-only roots for `read`, `glob` and `grep`, so an external skill keeps its own layout; write-type tools never touch them. Python sidecars are never executed. |
| Session list/delete/fork/revert/summary/query/todos | Implemented | Backed by runtime stores and gateway facade. Active gateway code imports the runtime session facade directly. |
| Context and automatic compaction | Implemented | Provider-only context, workspace instructions, skill context, request-local rendering, deterministic compaction, and optional summarizer control are covered. |
| Permissions and workspace-full-access defaults | Implemented | Built-ins run in the workspace with brokered allow/ask/deny decisions and path escape checks. |
| GitHub Copilot provider/model path | Implemented | Production chat supports Copilot only, with token/base URL environment overrides and no OpenAI/Anthropic/Ollama fallback. |
| Embedded runtime frontend | Removed | Gateway has no root HTML route and no static/template frontend assets. Portal owns UI. |
| Old Python tool loaders | Removed | `src.context_tools`, `src.bash_tools`, `efp_runtime.tools.local`, and `efp_runtime.tools.external` are absent. |
| MCP | Excluded | MCP servers and external protocol tool surfaces are out of scope for this runtime branch. |

## Intentional Remaining Gaps

- Background task state is process-local. Persisting task records across process
  restarts would require a dedicated task store and recovery contract.
- The LSP tool is an adapter boundary only. The runtime does not own language
  server installation, startup, or lifecycle management.
- `websearch` has no bundled provider. Callers must inject a runner so network
  search policy remains explicit.
- Internal runtime tests live under `tests/runtime` and form the current
  EFP runtime test boundary.
