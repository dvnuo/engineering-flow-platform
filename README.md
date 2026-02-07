# Engineering Flow Platform

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![pytest](https://img.shields.io/badge/pytest-76%20tests-green.svg)](tests/)
[![MIT License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

## ⚠️ Research Use Only

**This project is currently under active development and is intended for research purposes only.**

### 🚀 Running Requirements

**Docker is required to run this project.**

All services and dependencies must be run within Docker containers. Local Python installation is not supported for production use.

```bash
# Clone and setup
git clone https://github.com/dvnuo/engineering-flow-platform.git
cd engineering-flow-platform

# Start with Docker Compose
docker-compose up -d
```

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

## What Engineering Flow Platform Is Not

- Not a chat-based assistant
- Not an IDE plugin
- Not a prompt library
- Not a replacement for engineers

It augments engineering organizations by removing friction from workflows, not by replacing human judgment.

---

## Expected Outcomes

By introducing agentic, asynchronous workflows, Engineering Flow Platform aims to:

- Reduce lead time across the SDLC
- Minimize waiting caused by handoffs and coordination
- Improve consistency and reliability of complex engineering tasks
- Enable faster iteration from idea to outcome
- Support a shift from project delivery to product engineering

---

## Status

This project is under active development.

The initial focus is on high-value, low-risk internal engineering workflows where asynchronous execution and governance-aware autonomy provide immediate benefits.

---

## Features

- Simple Architecture - Core components: Gateway, Agent, Channel, Session
- Discord Support - Receive and respond to messages via Discord Bot
- LLM Integration - Supports OpenAI and GitHub Copilot APIs
- Session Management - Maintain conversation history per user/channel
- Memory System - Load context from workspace MD files (SOUL.md, USER.md, etc.)
- Extensible - Easy to add new channels or tools

## Table of Contents

- [⚠️ Research Use Only](#️-research-use-only)
- [🚀 Running Requirements](#-running-requirements)
- [About Engineering Flow Platform](#about-engineering-flow-platform)
- [Core Principles](#core-principles)
- [What Is an Engineering Flow?](#what-is-an-engineering-flow)
- [High-Level Architecture](#high-level-architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running](#running)
- [API Reference](#api-reference)
- [Architecture](#architecture)
- [Submodule Documentation](#submodule-documentation)
- [Memory System](#memory-system)
- [Development](#development)
- [Heartbeat](#heartbeat-periodic-background-checks)
- [Model Fallback](#model-fallback-automatic-model-degradation)
- [Troubleshooting](#troubleshooting)

---

## Installation

### Docker Required

**This project must be run using Docker.**

```bash
# Clone the repository
git clone https://github.com/dvnuo/engineering-flow-platform.git
cd engineering-flow-platform

# Create workspace directory
mkdir -p workspace/memory

# Copy example configuration
cp workspace/*.example workspace/

# Start with Docker Compose
docker-compose up -d
```

### Docker Compose Configuration

```yaml
version: '3.8'

services:
  engineering-flow-platform:
    build: .
    container_name: efp-bot
    ports:
      - "8000:8000"
    volumes:
      # Workspace directory for memory files - persists across restarts
      - ./workspace:/root/.efp/workspace
      # Optional: logs directory
      - ./logs:/app/logs
    environment:
      - EFP_DISCORD_BOT_TOKEN=${EFP_DISCORD_BOT_TOKEN}
      - EFP_DISCORD_CHANNEL_ID=${EFP_DISCORD_CHANNEL_ID}
      - EFP_LLM_API_KEY=${EFP_LLM_API_KEY}
    restart: unless-stopped
```

### Environment Variables

Create a `.env` file:

```bash
EFP_DISCORD_BOT_TOKEN=your_discord_bot_token
EFP_DISCORD_CHANNEL_ID=your_discord_channel_id
EFP_LLM_API_KEY=your_openai_api_key
```

---

## Quick Start Guide

### Step 1: Prepare Discord Bot

1. Create Discord Application at https://discord.com/developers/applications
2. Create Bot and get Token
3. Enable "Message Content Intent"
4. Invite Bot to server
5. Copy Channel ID (enable Developer Mode in Discord)

### Step 2: Get OpenAI API Key

1. Visit https://platform.openai.com/api-keys
2. Create new secret key
3. Copy API Key (format: `sk-...`)

### Step 3: Configure Project

```bash
# Create .env file
cat > .env << EOF
EFP_DISCORD_BOT_TOKEN=your_bot_token
EFP_DISCORD_CHANNEL_ID=your_channel_id
EFP_LLM_API_KEY=your_api_key
EOF

# Start with Docker Compose
docker-compose up -d
```

### Step 4: Verify

```bash
# Check logs
docker-compose logs -f
```

---

## Configuration

### Basic Configuration

Edit `config.yaml`:

```yaml
discord:
  bot_token: "${EFP_DISCORD_BOT_TOKEN}"
  channel_id: "${EFP_DISCORD_CHANNEL_ID}"

llm:
  provider: "openai"
  api_key: "${EFP_LLM_API_KEY}"
  model: "gpt-3.5-turbo"
  max_tokens: 1000
  temperature: 0.7

server:
  host: "0.0.0.0"
  port: 8000
```

### Configuration Options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `discord.bot_token` | string | - | Discord Bot Token |
| `discord.channel_id` | string | - | Target channel ID |
| `llm.provider` | string | `openai` | LLM provider |
| `llm.api_key` | string | - | API key |
| `llm.model` | string | `gpt-3.5-turbo` | Model name |
| `server.host` | string | `0.0.0.0` | Listen address |
| `server.port` | int | `8000` | Listen port |

---

## Running

### Start Services

```bash
# Build and start
docker-compose build
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Health Check

```bash
curl http://localhost:8000/health

# Response: {"status": "ok", "service": "engineering-flow-platform"}
```

---

## API Reference

### HTTP Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/webhook/discord` | Discord webhook receiver |
| GET | `/api/sessions` | List all active sessions |

---

## Architecture

```
+-------------+     +----------+     +-------------+     +---------+
| Discord     |---->| Gateway  |---->| Agent Core  |---->| LLM API |
| (Webhook)   |     | (HTTP)   |     |             |     |          |
+-------------+     +----------+     +-------------+     +---------+
                         │                  ^
                         │                  │
                    +----------+     +-------------+
                    | Session  |     |   LLM       |
                    | Manager  |     |   Client    |
                    +----------+     +-------------+
```

---

## Submodule Documentation

Each core module has detailed documentation in its `README.md`:

| Module | Path | Description |
|--------|------|-------------|
| **Agent** | [`agent/README.md`](agent/README.md) | Agent core logic, LLM providers, model fallback, heartbeat |
| **Channel** | [`channel/README.md`](channel/README.md) | Multi-channel adapters |
| **Skills** | [`skills/README.md`](skills/README.md) | Skill framework, @skill decorator |
| **Tools** | [`tools/README.md`](tools/README.md) | Sub-agent management, shell execution |
| **Tests** | [`tests/README.md`](tests/README.md) | Test framework, pytest configuration |
| **Cron** | [`cron/README.md`](cron/README.md) | Scheduled task scheduler |
| **Gateway** | [`gateway/README.md`](gateway/README.md) | Web API server |
| **Memory** | [`memory/README.md`](memory/README.md) | Persistent memory storage |
| **Session** | [`session/README.md`](session/README.md) | Session lifecycle management |
| **Docs** | [`docs/README.md`](docs/README.md) | Documentation standards |

---

## Memory System

Workspace files loaded from `~/.efp/workspace/`:

```bash
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

### Prerequisites

- Docker and Docker Compose
- Git
- Discord Developer Account (for testing)
- OpenAI API Key (for testing)

### Local Development

```bash
# Clone repository
git clone https://github.com/dvnuo/engineering-flow-platform.git
cd engineering-flow-platform

# Create environment file
cp .env.example .env

# Edit .env with your credentials
nano .env

# Start development environment
docker-compose up -d
```

### Testing

```bash
# Run tests in Docker
docker-compose exec engineering-flow-platform pytest tests/ -v
```

---

## Heartbeat (Periodic Background Checks)

The heartbeat feature provides periodic background checks.

### Configuration

```yaml
heartbeat:
  enabled: true
  check_interval: 300  # seconds
```

---

## Model Fallback (Automatic Model Degradation)

Automatically switches to alternative models when the primary model fails.

### Predefined Fallback Orders

| Order | Sequence | Use Case |
|-------|----------|----------|
| `FALLBACK_ORDER` | gpt-4o → gpt-4o-mini | Balanced reliability |
| `FAST_FALLBACK` | gpt-4o → gpt-4o-mini | Speed prioritized |
| `BUDGET_FALLBACK` | gpt-4o-mini → local | Cost minimized |

---

## Troubleshooting

### Bot Not Responding

1. Check bot is online in Discord
2. Verify Message Content Intent is enabled
3. Check configuration file
4. Review logs: `docker-compose logs`

### Error "401 Unauthorized"

Wrong API Key. Get new key from https://platform.openai.com/api-keys

### Error "429 Too Many Requests"

API rate limit exceeded. Wait and retry.

### Docker Issues

```bash
# Check container status
docker-compose ps

# View container logs
docker-compose logs -f

# Restart services
docker-compose restart
```

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
