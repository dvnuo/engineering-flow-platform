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

## Quick Start

### 1. Install Dependencies

```bash
cd openclaw_mini
pip install -r requirements.txt
```

### 2. Configure

Edit `config.yaml` with your settings:

```yaml
discord:
  bot_token: "YOUR_DISCORD_BOT_TOKEN"
  channel_id: "YOUR_CHANNEL_ID"

llm:
  provider: "openai"  # or "github_copilot"
  api_base: "https://api.openai.com/v1"
  api_key: "YOUR_API_KEY"
  model: "gpt-3.5-turbo"
```

#### Environment Variables (Recommended)

For sensitive data, use environment variables instead:

```bash
export OPENCLAW_DISCORD_BOT_TOKEN="your_bot_token"
export OPENCLAW_DISCORD_CHANNEL_ID="your_channel_id"
export OPENCLAW_LLM_API_KEY="your_api_key"
```

Then in `config.yaml`:

```yaml
discord:
  bot_token: "${OPENCLAW_DISCORD_BOT_TOKEN}"
  channel_id: "${OPENCLAW_DISCORD_CHANNEL_ID}"

llm:
  api_key: "${OPENCLAW_LLM_API_KEY}"
```

#### GitHub Copilot Configuration

```yaml
llm:
  provider: "github_copilot"
  api_key: "ghp_YOUR_GITHUB_TOKEN"
  model: "gpt-4"
  # GitHub Copilot uses: https://api.github.com/copilot/chat/completions
```

### 3. Set Up Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and bot
3. Get the Bot Token
4. Enable Message Content Intent
5. Invite the bot to your server
6. Create a channel and get the Channel ID

### 4. Set Up Discord Webhook

Create a webhook in your channel:

```bash
# Using Discord API (replace with your token and channel ID)
curl -X POST \
  -H "Authorization: Bot YOUR_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  https://discord.com/api/v10/channels/YOUR_CHANNEL_ID/webhooks \
  -d '{"name": "openclaw-mini"}'
```

Copy the webhook URL and update `config.yaml`:

```yaml
discord:
  bot_token: "YOUR_BOT_TOKEN"
  channel_id: "YOUR_CHANNEL_ID"
  webhook_url: "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN"
```

### 5. Run

```bash
python main.py
```

### 6. Test

Send a message in your Discord channel. The bot should respond!

## Configuration Options

| Section | Key | Type | Description | Required |
|---------|-----|------|-------------|----------|
| discord | bot_token | string | Discord Bot Token | Yes* |
| discord | channel_id | int/string | Target Channel ID | Yes* |
| discord | webhook_url | string | Discord Webhook URL | No |
| llm | provider | string | LLM provider (openai, github_copilot) | No |
| llm | api_base | string | API base URL | No |
| llm | api_key | string | API Key | Yes |
| llm | model | string | Model name (gpt-3.5-turbo, gpt-4, etc.) | No |
| llm | max_tokens | int | Max tokens in response | No |
| llm | temperature | float | Response creativity (0.0-1.0) | No |
| llm | max_retries | int | Retry attempts on failure | No |
| llm | retry_delay | float | Base delay between retries (seconds) | No |

* Either `bot_token` or `webhook_url` required for Discord integration

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

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/webhook/discord` | Discord webhook receiver |
| GET | `/api/sessions` | List active sessions |
| POST | `/api/sessions/{id}/clear` | Clear a session |
| GET | `/api/sessions/{id}` | Get session info |

## Development

### Run Tests

```bash
cd openclaw_mini
pytest tests/ -v
```

### Add New Channel

1. Create a new file in `channel/`
2. Implement `send_message()` and `handle_payload()` methods
3. Register in `gateway/server.py`

### Add New Tool

1. Create a new file in `tools/`
2. Implement the tool logic
3. Register in `agent/core.py`

## Troubleshooting

### Bot not responding?

1. Check bot has correct permissions (Message Content Intent)
2. Verify `config.yaml` has correct bot_token and channel_id
3. Check bot is online in Discord server
4. Review logs for error messages

### LLM API errors?

1. Verify API key is valid
2. Check `api_base` URL is correct
3. Ensure model name is supported
4. Check rate limits or quota

### Session issues?

1. Sessions are in-memory (restart clears history)
2. Check `/api/sessions` endpoint for active sessions
3. Use `/api/sessions/{id}/clear` to reset a session

## Contributing

1. Create a feature branch: `git checkout -b feature/xxx`
2. Make changes and add tests
3. Run tests: `pytest tests/ -v`
4. Commit: `git commit -m "feat: description"`
5. Push: `git push origin feature/xxx`
6. Create PR and request review

## License

MIT License
