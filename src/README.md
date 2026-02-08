# src/ - Implementation Code

## Structure

```
src/
├── agents/                 # Agent core + skill execution
│   ├── executor.py         # SkillsExecutor, execute_skill()
│   ├── subagent.py        # SubAgent spawning & management
│   ├── subagent_schemas.py
│   ├── core.py            # Agent with ReAct pattern
│   ├── llm.py            # LLM client
│   ├── heartbeat.py      # Periodic background checks
│   ├── memory.py         # Memory system
│   ├── model_fallback.py # Model fallback logic
│   ├── queue.py          # Message queue
│   ├── thinking.py       # Thinking levels
│   └── compaction.py     # Context compaction
│
├── channels/             # Channel adapters
│   ├── discord.py
│   ├── github.py
│   ├── jira.py
│   └── confluence.py
│
├── cron/                 # Scheduled tasks
│   └── mention_poller.py
│
├── gateway/              # Web API server
│   ├── server.py
│   └── webchat.py
│
├── memory/               # Memory storage
│   ├── __init__.py
│   └── sqlite_store.py
│
├── sessions/            # Session management
│   └── manager.py
│
├── git/                 # Git tool
├── github/              # GitHub tool
├── jira/                # Jira tool
├── confluence/           # Confluence tool
├── skill_creator/        # Skill creation tool
│   └── scripts/
├── config.py           # Configuration
└── utils/              # Utilities
    └── logger.py
```

## Architecture Principles

- Flat structure within each module
- Single responsibility per file
- Clean imports via `from src.<module> import ...`
