# src/ - Implementation Code

## Structure

```
src/
├── agents/                 # Compatibility support modules and skill/subagent helpers
│   ├── subagent.py        # SubAgent spawning & management
│   ├── subagent_schemas.py
│   ├── heartbeat.py      # Periodic background checks
│   ├── memory.py         # Memory system
│   ├── model_fallback.py # Model fallback logic
│   ├── queue.py          # Message queue
│   ├── thinking.py       # Thinking levels
│   └── compaction.py     # Context compaction
│
├── channels/             # Channel adapters
│   ├── github.py        # GitHub webhook/comments
│   ├── jira.py         # Jira
│   └── confluence.py    # Confluence
│
├── cron/                 # Scheduled tasks
│   ├── automation_watchers.py  # Deprecated compatibility shim; Portal owns automation monitoring rules
│   └── jira_reconciliation.py  # Legacy/separate reconciliation workflow (not GitHub PR automation monitoring)
│
├── gateway/              # API-only runtime HTTP server
│   ├── server.py        # Main gateway
│   ├── runtime_chat.py  # EFP runtime chat adapter
│   ├── runtime_api.py   # Portal/runtime API routes
│   └── runtime_request_contracts.py
│
├── efp_runtime/          # AgentRuntime, loop, provider, sessions, built-in tools
│
├── memory/               # Memory storage
│   ├── __init__.py
│   └── sqlite_store.py
│
├── sessions/            # Session support modules
│   ├── persistence.py
│   ├── pruning.py
│   └── usage.py
│
├── git/                 # Git tool
├── github/              # GitHub tool
├── jira/                # Jira tool
├── confluence/          # Confluence tool
├── skill_creator/       # Skill creation tool
├── config.py           # Configuration
└── utils/              # Utilities
    └── logger.py
```

## Architecture Principles

- Flat structure within each module
- Single responsibility per file
- Clean imports via `from src.<module> import ...`
- Runtime skill infrastructure lives under `src/skills`.
- Business skill assets are loaded from `EFP_SKILLS_DIR`, `/app/skills`, or local repo-root `skills/` fallback for development.
- Canonical skill metadata files are lowercase `skill.md` with YAML frontmatter.
- Business skills should be added to `engineering-flow-platform-skills`, not this EFP runtime repo.
- Portal provisions skills repository/branch.
- EFP native runtime no longer supports the External tools subsystem.
- Runtime tool surface is built-in/native only.
- The native runtime is API-only. Portal owns the UI; this repo no longer serves an embedded browser page or static/template assets.

## Runtime / Portal boundary (important)

- Portal (control plane) owns automation monitoring rules and GitHub PR review-request polling.
- EFP runtime (execution plane) receives dispatched tasks via `/api/tasks/execute`.
- `github_review_task` remains a runtime execution path.
- Do **not** add new runtime-side automation polling in EFP.
- For native runtime HTTP surface, design, parity, capability snapshot shape, and observability fields, see `../docs/runtime_contract.md`, `../docs/runtime-design.md`, `../docs/opencode-parity.md`, and `../docs/observability_contract.md`.
