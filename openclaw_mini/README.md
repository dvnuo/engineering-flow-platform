# OpenClaw Mini

A simple version of [OpenClaw](https://github.com/openclaw/openclaw) written in Python.

## Features

- 🎯 **Simple Architecture** - Core components: Gateway, Agent, Channel, Session
- 💬 **Discord Support** - Receive and respond to messages via Discord Bot
- 🧠 **LLM Integration** - Supports OpenAI-compatible APIs
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
  provider: "openai"
  api_base: "https://api.openai.com/v1"
  api_key: "YOUR_OPENAI_API_KEY"
  model: "gpt-3.5-turbo"
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
│   └── llm.py           # LLM client
├── channel/
│   ├── __init__.py
│   └── discord.py       # Discord adapter
└── session/
    ├── __init__.py
    └── manager.py       # Session management
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/webhook/discord` | Discord webhook receiver |
| GET | `/api/sessions` | List active sessions |
| POST | `/api/sessions/{id}/clear` | Clear a session |

## Development

### Run Tests

```bash
# TBD
```

### Add New Channel

1. Create a new file in `channel/`
2. Implement `send_message()` and `handle_payload()` methods
3. Register in `gateway/server.py`

### Add New Tool

1. Create a new file in `tools/`
2. Implement the tool logic
3. Register in `agent/core.py`

## License

MIT License
