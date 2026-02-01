# OpenClaw Mini

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![pytest](https://img.shields.io/badge/pytest-62%20tests-green.svg)](tests/)
[![MIT License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

A simple version of [OpenClaw](https://github.com/openclaw/openclaw) written in Python.

## Features

- 🎯 **Simple Architecture** - Core components: Gateway, Agent, Channel, Session
- 💬 **Discord Support** - Receive and respond to messages via Discord Bot
- 🧠 **LLM Integration** - Supports OpenAI and GitHub Copilot APIs
- 💾 **Session Management** - Maintain conversation history per user/channel
- 🔌 **Extensible** - Easy to add new channels or tools

## Table of Contents

- [Installation](#installation)
  - [Virtual Environment (Recommended)](#1-virtual-environment-recommended)
  - [Docker](#2-docker)
  - [System-wide](#3-system-wide)
- [Configuration](#configuration)
- [Discord Setup](#discord-setup)
- [Running](#running)
- [API Reference](#api-reference)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## Installation

### 1. Virtual Environment (Recommended)

Create an isolated Python environment:

```bash
# Create virtual environment
cd openclaw_mini
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
cd openclaw_mini
docker build -t openclaw-mini .

# Run the container
docker run -d \
  --name openclaw-mini \
  -p 8000:8000 \
  -v $(pwd)/config.yaml:/app/config.yaml \
  openclaw-mini
```

**Docker Compose (recommended)**:

```yaml
# docker-compose.yml
version: '3.8'
services:
  openclaw-mini:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./config.yaml:/app/config.yaml
    environment:
      - OPENCLAW_LLM_API_KEY=${OPENCLAW_LLM_API_KEY}
    restart: unless-stopped
```

```bash
docker-compose up -d
```

### 3. System-wide

```bash
cd openclaw_mini
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

#### Discord 配置

| Key | Type | Required | Default | Description | Example |
|-----|------|----------|---------|-------------|---------|
| `discord.bot_token` | string | Yes* | - | Discord Bot Token，从 Discord Developer Portal 获取 | `MTIzNDU2Nzg5MC5xyz...` |
| `discord.channel_id` | int/string | Yes* | - | 目标频道 ID，启用开发者模式后右键频道复制 | `123456789012345678` |
| `discord.webhook_url` | string | No | - | Discord Webhook URL，用于接收消息（可选，与 bot_token 二选一） | `https://discord.com/api/webhooks/...` |

**注意**: `bot_token` 和 `Webhook_url` 至少需要一个

**获取 bot_token**:
1. 访问 [Discord Developer Portal](https://discord.com/developers/applications)
2. 创建应用 → Bot → Reset Token → 复制

**获取 channel_id**:
1. Discord 设置 → 高级 → 启用开发者模式
2. 右键频道 → 复制 ID

#### LLM 配置

| Key | Type | Required | Default | Description | Example |
|-----|------|----------|---------|-------------|---------|
| `llm.provider` | string | No | `openai` | LLM 提供商 | `openai` / `github_copilot` |
| `llm.api_base` | string | No | OpenAI URL | API 基础 URL | `https://api.openai.com/v1` |
| `llm.api_key` | string | Yes | - | API 密钥 | `sk-...` |
| `llm.model` | string | No | `gpt-3.5-turbo` | 模型名称 | `gpt-3.5-turbo` / `gpt-4` |
| `llm.max_tokens` | int | No | 1000 | 响应最大 token 数 | 500 / 2000 |
| `llm.temperature` | float | No | 0.7 | 响应随机性 (0.0-1.0) | 0.5 / 0.9 |
| `llm.max_retries` | int | No | 3 | API 失败重试次数 | 3 / 5 |
| `llm.retry_delay` | float | No | 1 | 重试间隔秒数（指数退避） | 1 / 2 |

**Provider 说明**:
- `openai`: OpenAI 官方 API (ChatGPT)
- `github_copilot`: GitHub Copilot API

**模型推荐**:
- `gpt-3.5-turbo`: 便宜快速，适合日常对话
- `gpt-4`: 更强推理能力，适合复杂任务

**Temperature 说明**:
- `0.0`: 最确定性输出
- `0.7`: 平衡创造力
- `1.0`: 最高随机性

#### Session 配置

| Key | Type | Required | Default | Description | Example |
|-----|------|----------|---------|-------------|---------|
| `session.max_history` | int | No | 5 | 保留的对话轮数（每轮包含用户和助手消息） | 5 / 10 |

**说明**: 会话历史存储在内存中，服务器重启后清零

#### Server 配置

| Key | Type | Required | Default | Description | Example |
|-----|------|----------|---------|-------------|---------|
| `server.host` | string | No | `0.0.0.0` | 监听地址 | `0.0.0.0` / `127.0.0.1` |
| `server.port` | int | No | 8000 | 监听端口 | 8000 / 8080 |

**说明**:
- `0.0.0.0`: 监听所有网络接口
- `127.0.0.1`: 仅本地访问（更安全）

#### 完整配置示例

```yaml
# 基础配置
discord:
  bot_token: "YOUR_BOT_TOKEN"
  channel_id: "1234567890"

# OpenAI 配置
llm:
  provider: "openai"
  api_base: "https://api.openai.com/v1"
  api_key: "sk-..."
  model: "gpt-3.5-turbo"
  max_tokens: 1000
  temperature: 0.7
  max_retries: 3
  retry_delay: 1

# 会话配置
session:
  max_history: 5

# 服务器配置
server:
  host: "0.0.0.0"
  port: 8000
```

#### Docker 环境变量配置

```yaml
# docker-compose.yml
services:
  openclaw-mini:
    environment:
      - OPENCLAW_DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN}
      - OPENCLAW_DISCORD_CHANNEL_ID=${DISCORD_CHANNEL_ID}
      - OPENCLAW_LLM_API_KEY=${OPENAI_API_KEY}
```

```bash
# .env 文件
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_CHANNEL_ID=your_channel_id
OPENAI_API_KEY=sk-your-api-key
```

---

## Discord Setup

### Step 1: Create Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and name it
3. Go to "Bot" section and click "Add Bot"
4. Copy the Bot Token (keep it secret!)

### Step 2: Enable Privileged Intents

In the Bot settings, enable:
- **Message Content Intent** (Required for reading messages)

### Step 3: Invite Bot to Server

1. Go to OAuth2 → URL Generator
2. Select scopes: `bot`
3. Select permissions:
   - `Send Messages`
   - `Read Message History`
   - `View Channel`
4. Copy the generated URL and open it

### Step 4: Get Channel ID

1. Enable Developer Mode in Discord (Settings → Advanced → Developer Mode)
2. Right-click the channel → Copy ID

### Step 5: Create Webhook (Optional)

```bash
curl -X POST \
  -H "Authorization: Bot YOUR_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  https://discord.com/api/v10/channels/YOUR_CHANNEL_ID/webhooks \
  -d '{"name": "openclaw-mini"}'
```

---

## Running

### Basic Run

```bash
cd openclaw_mini
python main.py
```

### Run with Custom Config

```bash
python main.py --config /path/to/config.yaml
```

### Run in Background (Linux/macOS)

```bash
# Using nohup
nohup python main.py > logs/app.log 2>&1 &

# Using systemd (see below)
```

### Run as a Service (systemd)

Create `/etc/systemd/system/openclaw-mini.service`:

```ini
[Unit]
Description=OpenClaw Mini - Discord Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/openclaw_mini
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
sudo systemctl enable openclaw-mini
sudo systemctl start openclaw-mini
sudo systemctl status openclaw-mini
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
{"status": "ok", "service": "openclaw-mini"}
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
┌─────────────┐     ┌──────────┐     ┌─────────────┐     ┌─────────┐
│ Discord     │────▶│ Gateway  │────▶│ Agent Core  │────▶│ LLM API │
│ (Webhook)   │     │ (HTTP)   │     │             │     │          │
└─────────────┘     └──────────┘     └─────────────┘     └─────────┘
                          │                 ▲
                          │                 │
                     ┌──────────┐     ┌─────────────┐
                     │ Session  │     │   LLM       │
                     │ Manager  │     │   Client    │
                     └──────────┘     └─────────────┘
```

## Project Structure

```
openclaw_mini/
├── main.py              # Entry point
├── config.yaml          # Configuration
├── config.py            # Config loader
├── requirements.txt     # Python dependencies
├── pytest.ini           # pytest configuration
├── gateway/
│   ├── __init__.py
│   └── server.py        # HTTP/WebSocket server
├── agent/
│   ├── __init__.py
│   ├── core.py          # Agent logic
│   └── llm.py           # LLM client (OpenAI + GitHub Copilot)
├── channel/
│   ├── __init__.py
│   └── discord.py       # Discord adapter
├── session/
│   ├── __init__.py
│   └── manager.py       # Session management
└── tests/
    ├── test_config.py
    ├── test_gateway.py
    ├── test_llm_client.py
    └── test_session_manager.py
```

---

## Development

### Setup Development Environment

```bash
# Clone and setup
git clone https://github.com/itwake/codew.git
cd codew
git checkout -b feature/your-feature

# Create virtual environment
cd openclaw_mini
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

1. Create a new file in `channel/`
2. Implement the channel adapter class
3. Register routes in `gateway/server.py`

Example:
```python
# channel/telegram.py
from typing import Dict, Any

class TelegramChannel:
    async def send_message(self, content: str, channel_id: str) -> Dict[str, Any]:
        # Implementation
        pass
```

### Add New LLM Provider

1. Add provider detection in `agent/llm.py`
2. Implement provider-specific request handling
3. Add configuration examples in `config.yaml`

### Code Style

- Follow PEP 8
- Use type hints
- Add docstrings
- Write tests for new features

---

## Troubleshooting

### Bot not responding?

1. ✅ Check bot has correct permissions (Message Content Intent)
2. ✅ Verify `config.yaml` has correct `bot_token` and `channel_id`
3. ✅ Check bot is online in Discord server
4. ✅ Review logs for error messages
5. ✅ Ensure port 8000 is accessible

```bash
# Check bot permissions
# Go to Discord Developer Portal → Bot → Server Members Intent, Message Content Intent enabled
```

### LLM API errors?

1. ✅ Verify API key is valid
2. ✅ Check `api_base` URL is correct
3. ✅ Ensure model name is supported
4. ✅ Check rate limits or quota
5. ✅ Check network connectivity

```bash
# Test API key
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.openai.com/v1/models
```

### Session issues?

1. ✅ Sessions are in-memory (restart clears history)
2. ✅ Check `/api/sessions` endpoint for active sessions
3. ✅ Use `/api/sessions/{id}/clear` to reset a session
4. ✅ Configure `session.max_history` in config.yaml

### Port already in use?

```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill <PID>

# Or use a different port
python main.py --config config.yaml  # Edit config.yaml port
```

### Permission denied (Docker)?

```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Or run with sudo (not recommended)
sudo docker-compose up -d
```

### Logs not showing?

```bash
# Enable verbose logging
# Edit main.py and change logging level
logging.basicConfig(level=logging.DEBUG)

# Check systemd logs
journalctl -u openclaw-mini -f
```

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

- 📧 Create an issue for bugs
- 💬 Join our Discord community
- 📖 Read the full documentation
