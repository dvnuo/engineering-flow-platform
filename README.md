# OpsClaw

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![pytest](https://img.shields.io/badge/pytest-76%20tests-green.svg)](tests/)
[![MIT License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

A simple version of [OpsClaw](https://github.com/openclaw/openclaw) written in Python.

## Features

- Simple Architecture - Core components: Gateway, Agent, Channel, Session
- Discord Support - Receive and respond to messages via Discord Bot
- LLM Integration - Supports OpenAI and GitHub Copilot APIs
- Session Management - Maintain conversation history per user/channel
- Memory System - Load context from workspace MD files (SOUL.md, USER.md, etc.)
- Extensible - Easy to add new channels or tools

## Table of Contents

- [Quick Start](#quick-start-guide-5-minutes)
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

## Submodule Documentation

Each core module has detailed documentation in its `README.md`:

| Module | Path | Description |
|--------|------|-------------|
| **Agent** | [`agent/README.md`](agent/README.md) | Agent core logic, LLM providers, model fallback, heartbeat |
| **Channel** | [`channel/README.md`](channel/README.md) | Multi-channel adapters (Discord, WhatsApp, Telegram, Slack, Google Chat) |
| **Skills** | [`skills/README.md`](skills/README.md) | Skill framework, @skill decorator, executor |
| **Tools** | [`tools/README.md`](tools/README.md) | Sub-agent management, shell execution, process management |
| **Tests** | [`tests/README.md`](tests/README.md) | Test framework, pytest configuration, CI/CD integration |
| **Cron** | [`cron/README.md`](cron/README.md) | Scheduled task scheduler, mention poller, cleanup jobs |
| **Gateway** | [`gateway/README.md`](gateway/README.md) | Web API server, authentication, rate limiting, WebSocket |
| **Memory** | [`memory/README.md`](memory/README.md) | Persistent memory storage, semantic search, context management |
| **Session** | [`session/README.md`](session/README.md) | Session lifecycle, state persistence, context isolation |
| **Docs** | [`docs/README.md`](docs/README.md) | Documentation standards, guides, API reference templates |

### When to Read Each Documentation

| Scenario | Read This |
|----------|-----------|
| Adding new LLM provider | [`agent/README.md`](agent/README.md) |
| Adding new channel | [`channel/README.md`](channel/README.md) |
| Creating new skill | [`skills/README.md`](skills/README.md) |
| Running background tasks | [`cron/README.md`](cron/README.md) |
| Managing user sessions | [`session/README.md`](session/README.md) |
| Understanding memory system | [`memory/README.md`](memory/README.md) |
| Adding tools/sub-agents | [`tools/README.md`](tools/README.md) |
| Writing tests | [`tests/README.md`](tests/README.md) |
| Configuring web server | [`gateway/README.md`](gateway/README.md) |
| Writing documentation | [`docs/README.md`](docs/README.md) |

---

## Installation

### 1. Virtual Environment (Recommended)

Create an isolated Python environment:

```bash
# Create virtual environment
# Run from project root
python -m venv venv

# Activate (Linux/macOS)
source venv/bin/activate

# Activate (Windows)
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

To deactivate the environment when done:
```bash
deactivate
```

### 2. Docker

```bash
# Build the image
# Run from project root
docker build -t opsclaw .
```

**Docker Compose (recommended)**:

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  opsclaw:
    build: .
    container_name: opsclaw-bot
    ports:
      - "8000:8000"
    volumes:
      # Config file - required
      - ./config.yaml:/app/config.yaml:ro
      
      # Workspace directory for memory files - persists across restarts
      # Contains: SOUL.md, USER.md, AGENTS.md, TOOLS.md, MEMORY.md, memory/
      - ./workspace:/root/.opsclaw/workspace
      
      # Optional: logs directory
      - ./logs:/app/logs
    environment:
      - OPENCLAW_DISCORD_BOT_TOKEN=${OPENCLAW_DISCORD_BOT_TOKEN}
      - OPENCLAW_DISCORD_CHANNEL_ID=${OPENCLAW_DISCORD_CHANNEL_ID}
      - OPENCLAW_LLM_API_KEY=${OPENCLAW_LLM_API_KEY}
    restart: unless-stopped
```

```bash
# Create workspace directory with template files
mkdir -p workspace/memory
cp workspace/*.example workspace/

# Start the container
docker-compose up -d
```

**Important: Workspace Volume**

Without the `./workspace:/root/.opsclaw/workspace` volume mount:
- Memory files (SOUL.md, USER.md, MEMORY.md, etc.) will be lost on restart
- Conversation context and learned preferences won't persist

**Directory Structure After Setup**:

```
./workspace/
├── SOUL.md        # Agent persona (copy from SOUL.md.example)
├── USER.md        # User preferences (copy from USER.md.example)
├── AGENTS.md      # Workspace conventions (copy from AGENTS.md.example)
├── TOOLS.md       # Tool configurations (optional)
├── MEMORY.md      # Long-term memory (copy from MEMORY.md.example)
└── memory/
    └── 2026-02-02.md  # Daily notes (auto-created)
```

### 3. System-wide

```bash
# Run from project root
pip install -r requirements.txt
```

---

## Configuration

### Basic Configuration

Edit `config.yaml` with your settings:

```yaml
# config.yaml
discord:
  bot_token: "YOUR_DISCORD_BOT_TOKEN"
  channel_id: "YOUR_CHANNEL_ID"

llm:
  provider: "openai"  # or "github_copilot"
  api_base: "https://api.openai.com/v1"
  api_key: "YOUR_API_KEY"
  model: "gpt-3.5-turbo"
  max_tokens: 1000
  temperature: 0.7
  max_retries: 3
  retry_delay: 1

server:
  host: "0.0.0.0"
  port: 8000
```

### Environment Variables (Recommended)

For sensitive data, use environment variables:

```bash
# Linux/macOS
export OPENCLAW_DISCORD_BOT_TOKEN="your_bot_token"
export OPENCLAW_DISCORD_CHANNEL_ID="your_channel_id"
export OPENCLAW_LLM_API_KEY="your_api_key"

# Windows (PowerShell)
$env:OPENCLAW_DISCORD_BOT_TOKEN="your_bot_token"
$env:OPENCLAW_DISCORD_CHANNEL_ID="your_channel_id"
$env:OPENCLAW_LLM_API_KEY="your_api_key"
```

Then in `config.yaml`:

```yaml
discord:
  bot_token: "${OPENCLAW_DISCORD_BOT_TOKEN}"
  channel_id: "${OPENCLAW_DISCORD_CHANNEL_ID}"

llm:
  api_key: "${OPENCLAW_LLM_API_KEY}"
```

### GitHub Copilot Configuration

```yaml
llm:
  provider: "github_copilot"
  api_key: "ghp_YOUR_GITHUB_TOKEN"
  model: "gpt-4"
  # GitHub Copilot uses: https://api.github.com/copilot/chat/completions
```

### Configuration Options

#### Discord Configuration

| Key | Type | Required | Default | Description | Example |
|-----|------|----------|---------|-------------|---------|
| `discord.bot_token` | string | Yes* | - | Discord Bot Token from Developer Portal | `MTIzNDU2Nzg5MC5xyz...` |
| `discord.channel_id` | int/string | Yes* | - | Target channel ID (right-click channel to copy) | `123456789012345678` |
| `discord.webhook_url` | string | No | - | Discord Webhook URL for receiving messages (optional) | `https://discord.com/api/webhooks/...` |

**Note**: At least one of `bot_token` or `webhook_url` is required

**Getting bot_token**:
1. Visit [Discord Developer Portal](https://discord.com/developers/applications)
2. Create Application → Bot → Reset Token → Copy

**Getting channel_id**:
1. Discord Settings → Advanced → Developer Mode
2. Right-click channel → Copy ID

#### LLM Configuration

| Key | Type | Required | Default | Description | Example |
|-----|------|----------|---------|-------------|---------|
| `llm.provider` | string | No | `openai` | LLM provider | `openai` / `github_copilot` |
| `llm.api_base` | string | No | OpenAI URL | API base URL | `https://api.openai.com/v1` |
| `llm.api_key` | string | Yes | - | API key | `sk-...` |
| `llm.model` | string | No | `gpt-3.5-turbo` | Model name | `gpt-3.5-turbo` / `gpt-4` |
| `llm.max_tokens` | int | No | 1000 | Max response tokens | 500 / 2000 |
| `llm.temperature` | float | No | 0.7 | Response randomness (0.0-1.0) | 0.5 / 0.9 |
| `llm.max_retries` | int | No | 3 | API retry attempts | 3 / 5 |
| `llm.retry_delay` | float | No | 1 | Retry delay in seconds (exponential backoff) | 1 / 2 |

**Provider Notes**:
- `openai`: OpenAI official API (ChatGPT)
- `github_copilot`: GitHub Copilot API

**Model Recommendations**:
- `gpt-3.5-turbo`: Cheap and fast, suitable for daily conversations
- `gpt-4`: Stronger reasoning, suitable for complex tasks

**Temperature Notes**:
- `0.0`: Most deterministic output
- `0.7`: Balanced creativity
- `1.0`: Highest randomness

#### Session Configuration

| Key | Type | Required | Default | Description | Example |
|-----|------|----------|---------|-------------|---------|
| `session.max_history` | int | No | 5 | Conversation turns to retain | 5 / 10 |

**Note**: Session history is stored in memory and cleared on server restart

#### Server Configuration

| Key | Type | Required | Default | Description | Example |
|-----|------|----------|---------|-------------|---------|
| `server.host` | string | No | `0.0.0.0` | Listen address | `0.0.0.0` / `127.0.0.1` |
| `server.port` | int | No | 8000 | Listen port | 8000 / 8080 |

**Notes**:
- `0.0.0.0`: Listen on all network interfaces
- `127.0.0.1`: Local only (more secure)

#### Complete Configuration Example

```yaml
# Basic configuration
discord:
  bot_token: "YOUR_BOT_TOKEN"
  channel_id: "1234567890"

# OpenAI configuration
llm:
  provider: "openai"
  api_base: "https://api.openai.com/v1"
  api_key: "sk-..."
  model: "gpt-3.5-turbo"
  max_tokens: 1000
  temperature: 0.7
  max_retries: 3
  retry_delay: 1

# Session configuration
session:
  max_history: 5

# Server configuration
server:
  host: "0.0.0.0"
  port: 8000
```

#### Docker Environment Variables Configuration

```yaml
# docker-compose.yml
services:
  opsclaw:
    environment:
      - OPENCLAW_DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN}
      - OPENCLAW_DISCORD_CHANNEL_ID=${DISCORD_CHANNEL_ID}
      - OPENCLAW_LLM_API_KEY=${OPENAI_API_KEY}
```

```bash
# .env file
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_CHANNEL_ID=your_channel_id
OPENAI_API_KEY=sk-your-api-key
```

---

## Quick Start Guide (5 Minutes)

### Step 1: Prepare Discord Bot

#### 1.1 Create Discord Application

1. Open browser, visit https://discord.com/developers/applications
2. Click "New Application" in top-right
3. Enter application name (e.g., `OpsClaw-Bot`)
4. Click "Create"

#### 1.2 Create Bot

1. Click "Bot" in left menu
2. Click "Add Bot"
3. Click "Yes, do it!" to confirm

#### 1.3 Get Bot Token (Important!)

1. On Bot page, find "Token" section
2. Click "Reset Token"
3. Click "Copy"
4. **Save securely**, don't share!

#### 1.4 Enable Required Permissions

1. On Bot page, find "Privileged Gateway Intents"
2. Enable "Message Content Intent" (Required to receive messages)
3. Click "Save Changes"

#### 1.5 Invite Bot to Server

1. Click "OAuth2" → "URL Generator" in left menu
2. In "Scopes", check `bot`
3. In "Bot Permissions", check:
   - Send Messages
   - Read Message History
   - View Channel
4. Scroll down, copy "Generated URL"
5. Open in browser, select your Discord server, click "Authorize"

### Step 2: Get Channel ID

1. Open Discord
2. Click gear icon (User Settings) bottom-left
3. Select "Advanced"
4. Enable "Developer Mode"
5. Right-click the channel where bot should respond
6. Select "Copy ID"

### Step 3: Get OpenAI API Key

1. Open https://platform.openai.com/api-keys
2. Login/Register OpenAI account
3. Click "Create new secret key"
4. Copy API Key (format: `sk-...`)

**Notes**:
- ChatGPT Plus users can use directly
- Payment method required
- Free tier available ($5)

### Step 4: Configure Project

```bash
# Run from project root
nano config.yaml
```

Fill in configuration:

```yaml
discord:
  bot_token: "Bot Token you copied"
  channel_id: "Channel ID you copied"

llm:
  provider: "openai"
  api_key: "OpenAI API Key you copied"
  model: "gpt-3.5-turbo"  # Recommended: cheap and effective
```

Verify configuration:

```bash
python main.py --help
```

### Step 5: Run Bot

```bash
# Method 1: Run in foreground (for testing)
python main.py

# When you see "Gateway started on http://0.0.0.0:8000", it's successful!
```

### Step 6: Test

1. Send `Hello` in Discord channel
2. Bot should reply!

---

## Running

### Important: Run from Correct Directory

Must run from `opsclaw` directory:

```bash
cd /root/opsclaw
```

If you see this error:
```
ModuleNotFoundError: No module named 'opsclaw'
```
Your current directory is wrong.

### Basic Run

```bash
# Run from project root
python main.py

# Success output:
# Gateway started on http://0.0.0.0:8000
```

### Run with Custom Config

```bash
python main.py --config /path/to/config.yaml
```

### Run in Background (Linux/macOS)

```bash
# Create logs directory
mkdir -p logs

# Run in background with nohup
nohup python main.py > logs/app.log 2>&1 &

# View logs
tail -f logs/app.log

# Stop service
pkill -f "python main.py"
```

### Run as a Service (systemd)

Create `/etc/systemd/system/opsclaw.service`:

```ini
[Unit]
Description=OpsClaw - Discord Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/opsclaw
ExecStart=/path/to/venv/bin/python main.py
Restart=on-failure
RestartSec=5
Environment=OPENCLAW_LLM_API_KEY=your_api_key

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable opsclaw
sudo systemctl start opsclaw
sudo systemctl status opsclaw
```

---

## API Reference

### HTTP Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/webhook/discord` | Discord webhook receiver |
| GET | `/api/sessions` | List all active sessions |
| GET | `/api/sessions/{id}` | Get session info |
| POST | `/api/sessions/{id}/clear` | Clear session history |

### Health Check

```bash
curl http://localhost:8000/health

# Response
{"status": "ok", "service": "opsclaw"}
```

### List Sessions

```bash
curl http://localhost:8000/api/sessions

# Response
{"sessions": ["discord:123:456"], "count": 1}
```

### Clear Session

```bash
curl -X POST http://localhost:8000/api/sessions/discord:123:456/clear

# Response
{"status": "cleared", "session_id": "discord:123:456"}
```

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

## Project Structure

```
opsclaw/
├── main.py              # Entry point
├── config.yaml          # Configuration
├── config.py            # Config loader
├── requirements.txt     # Python dependencies
├── pytest.ini           # pytest configuration
├── README.md           # This file
├── agent/              # Agent core (see agent/README.md)
│   ├── README.md       # Detailed agent documentation
│   ├── core.py
│   ├── llm.py
│   ├── model_fallback.py
│   └── heartbeat/
├── channel/            # Channel adapters (see channel/README.md)
│   ├── README.md       # Detailed channel documentation
│   ├── discord.py
│   └── (other channels)
├── skills/            # Skills framework (see skills/README.md)
│   ├── README.md       # Detailed skills documentation
│   ├── decorator.py
│   ├── executor/
│   ├── coding_agent/
│   └── (other skills)
├── tools/              # Tools (see tools/README.md)
│   ├── README.md       # Detailed tools documentation
│   ├── subagent.py
│   └── exec.py
├── tests/              # Tests (see tests/README.md)
│   ├── README.md       # Detailed testing documentation
│   ├── test_*.py
│   └── fixtures/
├── cron/              # Cron jobs (see cron/README.md)
│   ├── README.md       # Detailed cron documentation
│   ├── scheduler.py
│   └── jobs/
├── gateway/            # Web server (see gateway/README.md)
│   ├── README.md       # Detailed gateway documentation
│   ├── main.py
│   ├── routes/
│   └── middleware/
├── memory/             # Memory system (see memory/README.md)
│   ├── README.md       # Detailed memory documentation
│   ├── base.py
│   └── sqlite_store.py
├── session/            # Session management (see session/README.md)
│   ├── README.md       # Detailed session documentation
│   ├── base.py
│   └── session_manager.py
└── docs/               # Documentation (see docs/README.md)
    └── README.md        # Documentation standards
```

---

## Memory System

CodeW loads context from workspace MD files, similar to OpsClaw's memory system.

### Workspace Files

| File | Description | Required |
|------|-------------|----------|
| `SOUL.md` | Agent persona and behavior guidelines | No |
| `USER.md` | User preferences and context | No |
| `AGENTS.md` | Workspace conventions and rules | No |
| `TOOLS.md` | Tool configurations and aliases | No |
| `MEMORY.md` | Long-term curated memory | No |
| `memory/YYYY-MM-DD.md` | Daily notes and logs | No |

### Memory Files Location

By default, memory files are loaded from `~/.opsclaw/workspace/`:

```bash
~/.opsclaw/workspace/
├── SOUL.md        # Agent identity
├── USER.md        # User info
├── AGENTS.md      # Workspace rules
├── TOOLS.md       # Tool configs
├── MEMORY.md      # Long-term memory
└── memory/
    ├── 2026-01-31.md
    └── 2026-02-01.md
```

### Security

- **MEMORY.md** is only loaded for main sessions (main, webchat, discord)
- Other sessions exclude sensitive memory content for privacy
- Set `cache_ttl_seconds=0` in MemorySystem to disable caching

### API Usage

```python
from agent.memory import memory_system

# Build complete system prompt
prompt = memory_system.build_system_prompt(include_memory=True)

# Load individual files
soul = memory_system.load_soul()
user = memory_system.load_user()
memory = memory_system.load_memory()

# Configure cache (default: 60 seconds)
memory_system = MemorySystem(cache_ttl_seconds=120)
```

---

## Development

### Setup Development Environment

```bash
# Clone and setup
git clone https://github.com/itwake/opsclaw.git
cd opsclaw
git checkout -b feature/your-feature

# Create virtual environment
# Run from project root
python -m venv venv
source venv/bin/activate

# Install dev dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx

# Run tests
pytest tests/ -v

# Run with hot reload (requires entr or similar)
echo "*.py" | entr -r python main.py
```

### Add New Channel

See [`channel/README.md`](channel/README.md) for detailed guide.

### Add New LLM Provider

See [`agent/README.md`](agent/README.md) for detailed guide.

### Add New Skill

See [`skills/README.md`](skills/README.md) for detailed guide.

### Code Style

- Follow PEP 8
- Use type hints
- Add docstrings
- Write tests for new features
- See [`tests/README.md`](tests/README.md) for testing standards

---

## Heartbeat (Periodic Background Checks)

The heartbeat feature provides periodic background checks for emails, calendar, and weather. The behavior is influenced by the **thinking level**.

### Configuration

Enable heartbeat in `config.yaml`:

```yaml
heartbeat:
  enabled: true
  check_interval: 300  # Base interval in seconds (default: 5 minutes)
```

### Thinking Level Effects

| Thinking Level | Check Interval | Detail Level |
|---------------|----------------|---------------|
| `high` | 2x more frequent (150s) | Detailed analysis with importance, conflicts, alerts |
| `medium` | Normal (300s) | Standard detail |
| `minimal` | Normal (300s) | Simplified checks |
| `off` | 2x less frequent (600s) | Just unread count / summary |

### Behavior Examples

**thinking=high**:
- Emails: Returns `important_count`, `action_required`, detailed analysis
- Calendar: Returns `conflicts`, `upcoming_important`
- Weather: Returns `forecast`, `alerts`, `recommendations`

**thinking=off**:
- Emails: Returns only `unread_count`
- Calendar: Returns only `today_count`
- Weather: Returns only `current_condition`

### Log Output

```
=== [HEARTBEAT] STARTED ===
  think_level=high
  interval=150s

=== [HEARTBEAT] CHECK COMPLETED ===
  Detail: detailed

=== [HEARTBEAT] LEVEL CHANGED ===
  off -> high
  interval: 150s
```

---

## Model Fallback (Automatic Model Degradation)

The model fallback feature automatically switches to alternative models when the primary model fails. This improves reliability by gracefully handling transient errors.

### Overview

When making LLM calls, sometimes the primary model may fail due to:
- Network timeouts
- Server errors
- Model overloaded

Instead of completely failing, the system can automatically try the next model in the fallback list.

### Usage

```python
from agent.model_fallback import (
    with_model_fallback,
    FALLBACK_ORDER,
    FAST_FALLBACK,
    BUDGET_FALLBACK,
    LOCAL_FALLBACK,
)

# Use with async tasks
result = await with_model_fallback(
    task=lambda: agent.process(message="Analyze this code"),
    candidates=FALLBACK_ORDER
)
```

### Predefined Fallback Orders

| Order | Sequence | Use Case |
|-------|----------|----------|
| `FALLBACK_ORDER` | gpt-4o → gpt-4o-mini → claude-sonnet-4 | Balanced reliability and cost |
| `FAST_FALLBACK` | gpt-4o → gpt-4o-mini | Speed prioritized |
| `BUDGET_FALLBACK` | gpt-4o-mini → claude-haiku-3-5 → ollama/llama3 | Cost minimized |
| `LOCAL_FALLBACK` | ollama/llama3 → ollama/mistral → gpt-4o-mini | Local models first |

### Custom Fallback Order

```python
from agent.model_fallback import ModelCandidate, with_model_fallback

# Create custom fallback order
my_fallback = [
    ModelCandidate(provider="openai", model="gpt-4o", priority=0),
    ModelCandidate(provider="anthropic", model="claude-sonnet-4", priority=1),
    ModelCandidate(provider="ollama", model="llama3", priority=2),
]

result = await with_model_fallback(
    task=lambda: agent.process(message="..."),
    candidates=my_fallback
)
```

### Error Classification

**Skip Fallback** (changing models won't help):
- Authentication errors (invalid API key)
- Rate limit exceeded
- Quota exceeded
- Context length exceeded
- Permission denied

**Trigger Fallback** (different model may succeed):
- Connection refused
- Request timeout
- Service unavailable
- Server error
- Model overloaded

### Configuration via YAML

```yaml
model_fallback:
  enabled: true
  default_order: "FALLBACK_ORDER"  # FALLBACK_ORDER, FAST_FALLBACK, BUDGET_FALLBACK, LOCAL_FALLBACK
  max_retries: 3
```

### Programmatic Configuration

```python
from agent.model_fallback import get_fallback_order

# Get predefined order
order = get_fallback_order("fast")

# Or get default
order = get_fallback_order()  # Returns FALLBACK_ORDER
```

### ModelCandidate Properties

```python
candidate = ModelCandidate(
    provider="openai",    # Provider name
    model="gpt-4o",       # Model name
    priority=0,           # Priority (lower = higher priority)
    weight=1.0,           # Weight for load balancing
)
```

### Error Handling

```python
from agent.model_fallback import FallbackError

try:
    result = await with_model_fallback(task, candidates)
except FallbackError as e:
    # All models failed
    print(f"Attempts: {len(e.attempts)}")
    for attempt in e.attempts:
        print(f"{attempt['provider']}/{attempt['model']}: {attempt['error']}")
```

### Integration with Agent

```python
from agent.model_fallback import FALLBACK_ORDER

# Simple integration
async def robust_process(agent, message):
    return await with_model_fallback(
        task=lambda: agent.process(message=message),
        candidates=FALLBACK_ORDER[1:]  # Skip first (already tried)
    )
```

### Testing

```bash
pytest tests/test_model_fallback.py -v
```

All 30 tests pass covering:
- Error classification
- Fallback logic
- Predefined orders
- Edge cases

---

## Troubleshooting

### Bot Not Responding

**Check in this order**:

1. Is Bot Online?
   - Check Discord server member list, bot avatar should be online with green indicator

2. Is Message Content Intent Enabled?
   - Visit Discord Developer Portal → Bot
   - Confirm "Message Content Intent" is enabled
   - Click "Save Changes"

3. Is config.yaml Correct?
   ```bash
   cat config.yaml
   ```
   Confirm `bot_token` and `channel_id` are correct without extra spaces

4. Is Bot Token Correct?
   - Token format: characters like `MTIzNDU2Nzg5MC5xyz...`
   - Don't include quotes or extra characters

5. Is Channel ID Correct?
   - Must be pure numbers, like `123456789012345678`
   - Don't include `<` `>` symbols

6. Check Console Logs
   ```bash
   python main.py
   ```
   Look for error messages

### Error "401 Unauthorized"

**Cause**: Wrong API Key

**Solution**:
```bash
# 1. Get new API key
# Visit https://platform.openai.com/api-keys

# 2. Confirm format (starts with sk-)
# 3. Update config.yaml
```

### Error "429 Too Many Requests"

**Cause**: API rate limit exceeded

**Solution**:
```bash
# 1. Wait 1 minute and retry
# 2. Or reduce max_tokens in config.yaml
# 3. Or increase temperature value
```

### Error "Connection Error" or "Failed to connect"

**Cause**: Network issue

**Solution**:
```bash
# 1. Check network connection
ping api.openai.com

# 2. Confirm OpenAI access
curl https://api.openai.com/v1/models

# 3. Try using proxy
```

### Bot Response is Slow

**Possible causes**:
1. Network latency
2. API server busy
3. Using `gpt-4` (slower than gpt-3.5-turbo)

**Solution**:
```yaml
# Use faster model in config.yaml
llm:
  model: "gpt-3.5-turbo"  # Faster and cheaper than gpt-4
  max_tokens: 500         # Reduce response length
```

### Port 8000 Already in Use

```bash
# 1. Check process using port
lsof -i :8000

# 2. Kill process
kill <PID>

# 3. Or change port in config.yaml
server:
  port: 8080  # Use different port
```

### Docker Permission Denied

```bash
# Method 1: Use sudo (not recommended)
sudo docker-compose up -d

# Method 2: Add user to docker group (recommended)
sudo usermod -aG docker $USER
# Log out and back in to take effect
```

### No Log Output

```bash
# 1. Run in foreground
python main.py

# 2. Enable detailed logging (modify main.py)
# Find logging.basicConfig, change to:
logging.basicConfig(level=logging.DEBUG)

# 3. Restart program
```

### Bot Replies "I encountered an error"

**Possible causes**:
1. OpenAI account has insufficient balance
2. API Key expired
3. Network timeout

**Solution**:
```bash
# 1. Check OpenAI account balance
# Visit https://platform.openai.com/account/usage

# 2. Check console for specific error
python main.py
```

---

## Quick Checklist

Before running bot, confirm all items:

- [ ] Discord Bot created
- [ ] Message Content Intent enabled
- [ ] Bot Token copied and saved
- [ ] Bot invited to server
- [ ] Channel ID obtained (pure numbers)
- [ ] OpenAI API Key obtained (starts with sk-)
- [ ] config.yaml configured correctly
- [ ] Dependencies installed: `pip install -r requirements.txt`
- [ ] Bot online and can send messages

---

## Getting Help

1. Check logs: Console error messages usually indicate the problem
2. Search errors: Paste error message into search engine
3. Create Issue: Submit problem in GitHub repository

---

## Contributing

1. Create a feature branch: `git checkout -b feature/xxx`
2. Make changes and add tests
3. Run tests: `pytest tests/ -v`
4. Commit: `git commit -m "feat: description"`
5. Push: `git push origin feature/xxx`
6. Create PR and request review

### Pull Request Template

```markdown
## Summary
Brief description of changes

## Testing
- [ ] Tests pass
- [ ] Manual testing completed

## Checklist
- [ ] Code follows style guide
- [ ] Self-review completed
- [ ] Documentation updated (if needed)
```

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Support

- Create an issue for bugs
- Join our Discord community
- Read the full documentation
