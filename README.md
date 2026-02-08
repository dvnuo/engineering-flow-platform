# Engineering Flow Platform

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![pytest](https://img.shields.io/badge/pytest-76%20tests-green.svg)](tests/)
[![MIT License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

## ⚠️ Research Use Only

**This project is currently under active development and is intended for research purposes only.**

---

## About Engineering Flow Platform

Engineering Flow Platform is an agentic platform designed to improve software delivery flow by reducing waiting time, orchestrating asynchronous workflows, and enabling intent-driven product engineering across the SDLC.

It is not a coding assistant or a standalone AI tool, but a platform capability that reshapes how engineering work progresses when humans are offline.

### Why Engineering Flow Platform?

Traditional developer productivity efforts focus on helping individuals work faster — writing code quicker, fixing bugs sooner, or generating documentation automatically.

However, in modern software delivery, the primary bottleneck is not how fast developers work, but how long work items spend waiting across the SDLC.

Engineering Flow Platform is built on a different premise:

> **Productivity is a flow problem, not a speed problem.**

---

## Core Principles

### 1. Flow First
The platform optimizes for flow efficiency rather than individual efficiency.

- Focus on lead time and cycle time
- Reduce waiting, handoffs, and coordination delays
- Treat SDLC as a value stream, not a task list

### 2. Intent Over Instructions
Humans declare intent; the platform determines execution.

Instead of specifying step-by-step instructions, users describe:
- The desired outcome
- Constraints and risk tolerance
- Required approval or oversight

The platform translates intent into executable workflows.

### 3. Asynchronous by Default
Engineering work should continue even when no one is online.

- Agents operate asynchronously
- Workflows are event-driven and long-running
- Humans review outcomes instead of driving execution

### 4. Platform, Not Bots
Engineering Flow Platform is not a collection of independent bots.

It provides:
- Central orchestration
- Shared context and memory
- Unified governance and auditability
- Consistent interaction surfaces

### 5. Governance-Embedded Autonomy
Autonomy is introduced gradually and safely.

- Role-based access control
- Auditable actions and decisions
- Human-in-the-loop checkpoints
- Explicit escalation and rollback paths

---

## What Is an Engineering Flow?

An engineering flow represents a unit of work moving through the SDLC — from intent to outcome.

Examples include:
- Release failure analysis
- Dependency or framework migration
- Backlog refinement and impact analysis
- CI/CD quality triage
- Incident root cause investigation

Each flow:
- Is goal-oriented
- May span multiple tools and systems
- Advances state asynchronously
- Produces verifiable outcomes

---

## High-Level Architecture

Engineering Flow Platform is composed of five logical layers:

### 1. Intent Layer
The single entry point for human interaction.

Users express what they want to achieve, not how to achieve it.

### 2. Flow Orchestration Layer
The core engine of the platform.

- Decomposes intent into flow steps
- Coordinates task-specific agents
- Tracks state, dependencies, and progress
- Manages retries, failures, and rollbacks

### 3. Asynchronous Execution Layer
Enables long-running, event-driven workflows.

- Agents operate independently of human presence
- Execution continues across time boundaries
- Supports delegation followed by review

### 4. Context and Control Layer
Provides the foundation for safe and effective agent execution.

Includes:
- Tool integrations (e.g. Git, CI/CD, issue tracking, cloud)
- Knowledge sources (documentation, runbooks, repositories)
- Standards and policies
- Historical memory and decision traces
- Governance and guardrails

### 5. Interaction Surfaces
Agents operate where work already happens.

Supported surfaces may include:
- CLI and API
- Issue and documentation systems
- Chat and collaboration tools
- Developer portals

---

## Project Structure

A modular, agentic platform with clear separation between skill declarations and implementation:

```
engineering-flow/
├── skills/                    # 🎯 Skill Declarations (SKILL.md only)
│   ├── coding_agent/
│   │   └── SKILL.md
│   ├── git/
│   │   └── SKILL.md
│   ├── github/
│   │   └── SKILL.md
│   ├── test_case_generator/
│   │   └── SKILL.md
│   └── skill_creator/
│       ├── SKILL.md
│       └── references/
│
├── main.py                    # Entry point
├── __init__.py               # Package exports
│
└── src/                       # 🤖 All Implementation Code 
    ├── agents/                # Agent core + skill execution
    │   ├── executor.py        # SkillsExecutor, execute_skill()
    │   ├── subagent.py        # SubAgent spawning & management
    │   ├── subagent_schemas.py
    │   ├── core.py           # Agent with ReAct pattern
    │   ├── llm.py           # LLM client
    │   ├── heartbeat.py      # Periodic background checks
    │   ├── memory.py        # Memory system
    │   ├── model_fallback.py # Model fallback logic
    │   ├── queue.py         # Message queue
    │   ├── thinking.py      # Thinking levels
    │   └── compaction.py     # Context compaction
    │
    ├── channels/             # Channel adapters
    │   ├── discord.py
 github.py
       │   ├── │   ├── jira.py
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
    ├── sessions/             # Session management
    │   └── manager.py
    │
    ├── git/                 # Git tool
    ├── github/              # GitHub tool
    ├── jira/                # Jira tool
    ├── confluence/           # Confluence tool
    ├── skill_creator/        # Skill creation tool
    │   └── scripts/
    ├── config.py            # Configuration
    └── utils/               # Utilities
        └── logger.py
```

### Architecture Principles

1. **skills/** - Declarative skill definitions only (SKILL.md)
2. **src/** - All implementation code 
3. **main.py** and **__init__.py** at root for easy execution
4. All modules (agents, channels, cron, gateway, memory, sessions) in `src/`


1. **skills/** - Declarative skill definitions only (SKILL.md)
2. **src/** - All implementation code 
3. **config.py**, **main.py**, and **utils/** are also in src/
3. No separate `tools/` or `integrations/` directories
4. All modules (agents, channels, cron, gateway, memory, sessions) in `src/`

1. **skills/** - Declarative skill definitions only (SKILL.md)
2. **src/** - All implementation code (flat, modular structure)
3. No separate `tools/` or `integrations/` directories
4. Each tool module has its own directory with implementation

---

## Features

- **Modular Architecture** - Clean separation of concerns
- **Declarative Skills** - Skills defined as SKILL.md, implementation in src/
- **Tool Integration** - Git, GitHub, Jira, Confluence support
- **SubAgent System** - Spawn and manage sub-agent sessions
- **Session Management** - Persistent conversation context
- **Memory System** - Load context from workspace files
- **Heartbeat** - Periodic background checks
- **Extensible** - Easy to add new channels or tools

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Git

### Running with Docker

```bash
# Clone and setup
git clone https://github.com/dvnuo/engineering-flow-platform.git
cd engineering-flow-platform

# Start with Docker Compose
docker-compose up -d
```

### Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v
```

---

## Adding New Skills

### 1. Create Skill Declaration

Create `skills/my_skill/SKILL.md`:

```yaml
---
name: my-skill
description: "Description of what my skill does"
---

# My Skill

## Usage

Describe how to use this skill...
```

### 2. Create Tool Implementation

Add tool to `src/my_skill/__init__.py`:

```python
from src import ToolResult

async def my_tool(param: str) -> ToolResult:
    """Tool implementation."""
    return ToolResult(success=True, content="Result")
```

### 3. Register Tool

Export from `src/__init__.py` if needed.

---

## Module Documentation

| Module | Path | Description |
|--------|------|-------------|
| **Agent** | [`agent/`](agent/) | Agent core logic, heartbeat |
| **Channel** | [`channel/`](channel/) | Multi-channel adapters |
| **Skills** | [`skills/`](skills/) | Skill framework, SKILL.md files |
| **Src** | [`src/`](src/) | Tool implementations, executor |
| **Tests** | [`tests/`](tests/) | Test suite |
| **Cron** | [`cron/`](cron/) | Scheduled task scheduler |
| **Gateway** | [`gateway/`](gateway/) | Web API server |
| **Memory** | [`memory/`](memory/) | Persistent memory storage |
| **Session** | [`session/`](session/) | Session lifecycle management |

---

## Configuration

Edit `config.yaml`:

```yaml
discord:
  bot_token: "${DISCORD_BOT_TOKEN}"
  channel_id: "${DISCORD_CHANNEL_ID}"

llm:
  provider: "openai"
  api_key: "${LLM_API_KEY}"
  model: "gpt-3.5-turbo"

server:
  host: "0.0.0.0"
  port: 8000
```

---

## Memory System

Workspace files loaded from `~/.efp/workspace/`:

```
~/.efp/workspace/
├── SOUL.md        # Agent persona
├── USER.md        # User preferences
├── AGENTS.md      # Workspace conventions
├── TOOLS.md       # Tool configurations
├── MEMORY.md      # Long-term memory
└── memory/
    └── YYYY-MM-DD.md
```

---

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Adding Tests

Create test files in `tests/` directory following the pattern `test_*.py`.

---

## Status

This project is under active development.

The initial focus is on high-value, low-risk internal engineering workflows where asynchronous execution and governance-aware autonomy provide immediate benefits.

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Vision

Engineering Flow Platform represents a step toward a zero-friction SDLC, where:

- Humans specify intent
- Agents execute and observe
- Systems govern and learn
- Engineering flow never stops

---

**⚠️ This project is for research purposes only. Not for production use.**
