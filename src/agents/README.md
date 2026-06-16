# agents/ - Agent Core + Skill Execution

## Structure

```
agents/
鈹溾攢鈹€ executor.py            # SkillsExecutor, execute_skill()
鈹溾攢鈹€ subagent.py           # SubAgent spawning & management
鈹溾攢鈹€ subagent_schemas.py   # SubAgent tool schemas
鈹溾攢鈹€ core.py               # Agent with ReAct pattern
鈹溾攢鈹€ llm.py               # LLM client
鈹溾攢鈹€ heartbeat.py         # Periodic background checks
鈹溾攢鈹€ model_fallback.py    # Model fallback logic
鈹溾攢鈹€ queue.py             # Message queue
鈹溾攢鈹€ thinking.py          # Thinking levels
鈹斺攢鈹€ compaction.py        # Context compaction
```

## Components

### Agent Core (`core.py`)
Main Agent class implementing ReAct pattern (Reasoning + Acting).

### Skill Execution (`executor.py`)
Handles skill execution and tool calls.

### SubAgent System (`subagent.py`)
Spawn and manage sub-agent sessions.

### LLM Integration (`llm.py`)
LLM client with model fallback support.
