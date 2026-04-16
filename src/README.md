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
│   ├── discord.py       # Discord bot
│   ├── github.py        # GitHub webhook/comments
│   ├── jira.py         # Jira
│   └── confluence.py    # Confluence
│
├── cron/                 # Scheduled tasks
│   └── automation_watchers.py
│
├── gateway/              # Web API server
│   ├── server.py        # Main gateway
│   └── webchat.py       # WebChat UI
│
├── memory/               # Memory storage
│   ├── __init__.py
│   └── sqlite_store.py
│
├── sessions/            # Session management
│   ├── manager.py
│   ├── persistence.py
│   ├── pruning.py
│   └── usage.py
│
├── git/                 # Git tool
├── github/              # GitHub tool
├── jira/                # Jira tool
├── confluence/          # Confluence tool
├── skill_creator/       # Skill creation tool
├── bash_tools/          # Shell/bash tools
├── config.py           # Configuration
└── utils/              # Utilities
    └── logger.py
```

## Architecture Principles

- Flat structure within each module
- Single responsibility per file
- Clean imports via `from src.<module> import ...`
- Skills in `skills/` use YAML frontmatter (.md files)
