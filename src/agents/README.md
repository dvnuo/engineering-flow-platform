# agents/ - Agent Core + Skill Execution

## Structure

```
agents/
|-- executor.py          # SkillsExecutor, execute_skill()
|-- subagent.py          # SubAgent spawning and management
|-- subagent_schemas.py  # SubAgent tool schemas
|-- core.py              # Agent with ReAct pattern
|-- llm.py               # LLM client
|-- heartbeat.py         # Periodic background checks
|-- model_fallback.py    # Model fallback logic
|-- queue.py             # Message queue
|-- thinking.py          # Thinking levels
`-- compaction.py        # Context compaction
```

## Components

### Agent Core (`core.py`)

Main Agent class implementing ReAct pattern.

### Skill Execution (`executor.py`)

Handles skill execution and tool calls.

### SubAgent System (`subagent.py`)

Spawn and manage sub-agent sessions.

### LLM Integration (`llm.py`)

LLM client with model fallback support.
