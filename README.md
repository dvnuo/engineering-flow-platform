# OpenClaw Mini

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![pytest](https://img.shields.io/badge/pytest-76%20tests-green.svg)](tests/)
[![MIT License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

A simple version of [OpenClaw](https://github.com/openclaw/openclaw) written in Python.

## Features

- 🎯 **Simple Architecture** - Core components: Gateway, Agent, Channel, Session
- 💬 **Discord Support** - Receive and respond to messages via Discord Bot
- 🧠 **LLM Integration** - Supports OpenAI and GitHub Copilot APIs
- 💾 **Session Management** - Maintain conversation history per user/channel
- 📝 **Memory System** - Load context from workspace MD files (SOUL.md, USER.md, etc.)
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

## 新手完全指南 (5分钟上手)

### 第一步：准备 Discord 机器人 🤖

#### 1.1 创建 Discord 应用

1. 打开浏览器，访问 https://discord.com/developers/applications
2. 点击右上角 **"New Application"**（新应用）
3. 输入应用名称（例如：`OpenClaw-Bot`）
4. 点击 **"Create"**

#### 1.2 创建机器人

1. 点击左侧菜单 **"Bot"**（机器人）
2. 点击 **"Add Bot"**（添加机器人）
3. 点击 **"Yes, do it!"** 确认

#### 1.3 获取 Bot Token（非常重要！）

1. 在 Bot 页面，找到 **"Token"** 部分
2. 点击 **"Reset Token"**（重置令牌）
3. 点击 **"Copy"**（复制）
4. **保存到安全的地方**，不要分享给他人！

#### 1.4 启用必要权限

1. 在 Bot 页面，找到 **"Privileged Gateway Intents"**
2. 启用 **"Message Content Intent"**（必须开启，否则收不到消息）
3. 点击 **"Save Changes"**

#### 1.5 邀请机器人到服务器

1. 点击左侧菜单 **"OAuth2"** → **"URL Generator"**
2. 在 **"Scopes"**（范围）中，勾选 `bot`
3. 在 **"Bot Permissions"**（机器人权限）中，勾选：
   - ✅ `Send Messages`（发送消息）
   - ✅ `Read Message History`（读取消息历史）
   - ✅ `View Channel`（查看频道）
4. 滚动到页面底部，复制 **"Generated URL"**
5. 在浏览器中打开链接，选择你的 Discord 服务器，点击 **"授权"**

### 第二步：获取频道 ID 📺

1. 打开 Discord
2. 点击左下角 **齿轮图标**（用户设置）
3. 选择 **"Advanced"**（高级）
4. 开启 **"Developer Mode"**（开发者模式）
5. 右键点击你想要机器人发言的频道
6. 选择 **"Copy ID"**（复制 ID）

### 第三步：获取 OpenAI API Key 🔑

1. 打开 https://platform.openai.com/api-keys
2. 登录/注册 OpenAI 账号
3. 点击 **"Create new secret key"**
4. 复制 API Key（格式：`sk-...`）

**注意**：
- 使用 ChatGPT Plus 账号可以直接使用
- 需要先充值或绑定支付方式
- 有免费额度（$5）

### 第四步：配置项目 📝

#### 4.1 编辑配置文件

```bash
# Run from project root
nano config.yaml
```

#### 4.2 填入配置

```yaml
discord:
  bot_token: "刚才复制的 Bot Token"
  channel_id: "刚才复制的频道 ID"

llm:
  provider: "openai"
  api_key: "刚才复制的 OpenAI API Key"
  model: "gpt-3.5-turbo"  # 推荐用这个，便宜好用
```

#### 4.3 验证配置

```bash
# 测试配置文件是否正确
python main.py --help
```

### 第五步：运行机器人 🚀

```bash
# 方式一：前台运行（测试用）
python main.py

# 看到 "Gateway started on http://0.0.0.0:8000" 就是成功了！
```

### 第六步：测试 🤝

1. 在 Discord 频道中发送：`你好`
2. 机器人应该会回复你！

---

## 常见问题 FAQ

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

### 重要：确保在正确目录 ⚠️

**必须进入 `openclaw_mini` 目录运行**：

```bash
cd /root/codew/openclaw_mini
```

如果出现以下错误：
```
ModuleNotFoundError: No module named 'openclaw_mini'
```
说明当前目录不对，请先执行：
```bash
# Run from project root
```

### Basic Run

```bash
# 1. 进入目录
# Run from project root

# 2. 运行程序
python main.py

# 看到以下输出表示成功：
# Gateway started on http://0.0.0.0:8000
```

### Run with Custom Config

```bash
python main.py --config /path/to/config.yaml
```

### Run in Background (Linux/macOS)

```bash
# 创建日志目录
mkdir -p logs

# 使用 nohup 后台运行
nohup python main.py > logs/app.log 2>&1 &

# 查看日志
tail -f logs/app.log

# 停止服务
pkill -f "python main.py"
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

[368 more lines in file. Use offset=468 continues]
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
│   ├── llm.py           # LLM client (OpenAI + GitHub Copilot)
│   └── memory.py        # Memory system for MD files
├── channel/
│   ├── __init__.py
│   └── discord.py       # Discord adapter
├── session/
│   ├── __init__.py
│   └── manager.py       # Session management
├── skills/
│   └── ...              # Skill executors
└── tests/
    ├── test_config.py
    ├── test_gateway.py
    ├── test_llm_client.py
    ├── test_session_manager.py
    └── test_memory.py   # Memory system tests
```

---

## Memory System

CodeW loads context from workspace MD files, similar to OpenClaw's memory system.

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

By default, memory files are loaded from `~/.openclaw/workspace/`:

```bash
~/.openclaw/workspace/
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
git clone https://github.com/itwake/codew.git
cd codew
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

## 故障排除 Troubleshooting

### ❌ 机器人不回复消息

**按以下顺序检查**：

1. ✅ **Bot 是否在线？**
   - 查看 Discord 服务器成员列表，机器人头像应该在线且显示绿色

2. ✅ **Message Content Intent 是否开启？**
   - 访问 Discord Developer Portal → Bot
   - 确认 **"Message Content Intent"** 已启用
   - 点击 **"Save Changes"**

3. ✅ **config.yaml 配置是否正确？**
   ```bash
   cat config.yaml
   ```
   确认 `bot_token` 和 `channel_id` 正确且没有多余空格

4. ✅ **Bot Token 是否正确？**
   - Token 格式：一串字符如 `MTIzNDU2Nzg5MC5xyz...`
   - 不要包含引号或额外字符

5. ✅ **频道 ID 是否正确？**
   - 必须是纯数字，如 `123456789012345678`
   - 不要包含 `<` `>` 等符号

6. ✅ **检查控制台日志**
   ```bash
   python main.py
   ```
   查看是否有错误信息

### ❌ 报错 "401 Unauthorized"

**原因**：API Key 错误

**解决方法**：
```bash
# 1. 重新获取 API Key
# 访问 https://platform.openai.com/api-keys

# 2. 确认格式正确（以 sk- 开头）
# 3. 更新 config.yaml
```

### ❌ 报错 "429 Too Many Requests"

**原因**：API 调用频率超限

**解决方法**：
```bash
# 1. 等待 1 分钟后再试
# 2. 或降低 config.yaml 中的 max_tokens
# 3. 或提高 temperature 值
```

### ❌ 报错 "Connection Error" 或 "Failed to connect"

**原因**：网络问题

**解决方法**：
```bash
# 1. 检查网络连接
ping api.openai.com

# 2. 确认能访问 OpenAI
curl https://api.openai.com/v1/models

# 3. 尝试使用代理
```

### ❌ 机器人回复很慢

**可能原因**：
1. 网络延迟
2. API 服务器繁忙
3. 使用了 `gpt-4`（比 gpt-3.5-turbo 慢）

**解决方法**：
```yaml
# config.yaml 中使用更快的模型
llm:
  model: "gpt-3.5-turbo"  # 比 gpt-4 快且便宜
  max_tokens: 500         # 减少响应长度
```

### ❌ 端口 8000 被占用

```bash
# 1. 查看占用端口的进程
lsof -i :8000

# 2. 杀掉进程
kill <PID>

# 3. 或修改 config.yaml 中的端口
server:
  port: 8080  # 改用其他端口
```

### ❌ Docker 权限被拒绝

```bash
# 方法一：使用 sudo（不推荐）
sudo docker-compose up -d

# 方法二：将用户添加到 docker 组（推荐）
sudo usermod -aG docker $USER
# 重新登录后生效
```

### ❌ 看不到日志输出

```bash
# 1. 确保在前台运行
python main.py

# 2. 查看详细日志（修改 main.py）
# 找到 logging.basicConfig，修改为：
logging.basicConfig(level=logging.DEBUG)

# 3. 重启程序
```

### ❌ Bot 回复 "我遇到了错误"

**可能原因**：
1. OpenAI 账号余额不足
2. API Key 过期
3. 网络超时

**解决方法**：
```bash
# 1. 检查 OpenAI 账号余额
# 访问 https://platform.openai.com/account/usage

# 2. 检查控制台具体错误信息
python main.py
```

---

## 快速检查清单 ✅

运行机器人前，确认以下所有项：

- [ ] Discord Bot 创建完成
- [ ] Message Content Intent 已启用
- [ ] Bot Token 已复制并保存
- [ ] Bot 已邀请到服务器
- [ ] 频道 ID 已获取（纯数字格式）
- [ ] OpenAI API Key 已获取（以 sk- 开头）
- [ ] config.yaml 配置正确
- [ ] 依赖已安装：`pip install -r requirements.txt`
- [ ] 机器人在线且能发送消息

---

## 获得帮助 🤝

1. **查看日志**：控制台的错误信息通常能说明问题
2. **搜索错误**：把错误信息粘贴到搜索引擎
3. **提 Issue**：在 GitHub 仓库提交问题

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
