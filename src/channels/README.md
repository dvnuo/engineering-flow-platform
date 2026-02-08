# Channel Directory

## Directory Structure

```
channel/
├── __init__.py
├── base.py                  # Base Channel抽象基类
├── discord/                 # Discord implementation
│   ├── __init__.py
│   ├── discord_channel.py
│   ├── handlers.py          # Event handlers
│   └── reactions.py        # Reaction handling
├── whatsapp/               # WhatsApp implementation
│   ├── __init__.py
│   ├── whatsapp_channel.py
│   └── webhook_handler.py
├── telegram/               # Telegram implementation
│   ├── __init__.py
│   ├── telegram_channel.py
│   └── bot_handlers.py
├── slack/                  # Slack implementation
│   ├── __init__.py
│   ├── slack_channel.py
│   └── events.py
├── googlechat/             # Google Chat implementation
│   ├── __init__.py
│   └── googlechat_channel.py
└── (other channel implementations)
```

## How It Works

### 1. Channel Abstraction
```python
# channel/base.py

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class Message:
    """Unified message format."""
    content: str
    author_id: str
    channel_id: str
    guild_id: Optional[str]
    timestamp: str
    message_id: str
    attachments: List[str] = None
    mentions: List[str] = None
    reply_to: Optional[str] = None

@dataclass
class Response:
    """Response format."""
    content: str
    channel_id: str
    attachments: List[str] = None
    reply_to: Optional[str] = None
    embed: Dict[str, Any] = None

class Channel(ABC):
    """Abstract base class for all channels."""
    
    @abstractmethod
    def receive(self) -> Message:
        """Receive a message from the channel."""
        pass
    
    @abstractmethod
    def send(self, response: Response) -> str:
        """Send a response to the channel."""
        pass
    
    @abstractmethod
    def delete(self, message_id: str) -> bool:
        """Delete a message."""
        pass
    
    @abstractmethod
    def react(self, message_id: str, emoji: str) -> bool:
        """Add reaction to a message."""
        pass
    
    @abstractmethod
    def get_info(self, channel_id: str) -> Dict[str, Any]:
        """Get channel information."""
        pass
```

### 2. Message Processing Flow
```
Platform Webhook → Channel Adapter → Unified Message → Agent Core → Unified Response → Channel Adapter → Platform
```

### 3. Event Handling
```python
# channel/discord/handlers.py

class DiscordHandler:
    """Handle Discord events."""
    
    def __init__(self, channel: "DiscordChannel"):
        self.channel = channel
    
    async def handle_message(self, event: Dict) -> Message:
        """Convert Discord message event to unified Message."""
        return Message(
            content=event.get("content", ""),
            author_id=event["author"]["id"],
            channel_id=event["channel_id"],
            guild_id=event.get("guild_id"),
            timestamp=event["timestamp"],
            message_id=event["id"],
            attachments=[a["url"] for a in event.get("attachments", [])],
            mentions=[m["id"] for m in event.get("mentions", [])],
            reply_to=event.get("referenced_message", {}).get("id")
        )
    
    async def handle_reaction(self, event: Dict):
        """Handle reaction add/remove events."""
        ...
    
    async def handle_slash_command(self, event: Dict):
        """Handle slash command interactions."""
        ...
```

## What Problems It Solves

- **Multi-Platform Support**: Unified interface for Discord, WhatsApp, Telegram, Slack, Google Chat
- **Message Format Conversion**: Platform-specific formats ↔ Unified format
- **Event Handling**: Normalized event processing across platforms
- **Rich Media Support**: Attachments, embeds, reactions
- **Rate Limiting**: Per-platform rate limit management

## Configuration Options

### Core Channel Configuration (config.yaml)

```yaml
# config.yaml
channels:
  # Global channel settings
  default_channel: "discord"
  max_message_length: 4000
  rate_limit:
    enabled: true
    strategy: "per_channel"   # per_channel, global
    reset_interval: 60        # seconds
  
  # Discord configuration
  discord:
    enabled: true
    token: ${DISCORD_TOKEN}
    intents:
      - "GUILDS"
      - "GUILD_MESSAGES"
      - "DIRECT_MESSAGES"
      - "MESSAGE_CONTENT"
      - "REACTIONS"
    prefix: "!"
    mention_prefix: "@"
    dm_enabled: true
    guilds: []
    ignored_channels: []
    reaction_emojis:
      success: "✅"
      error: "❌"
      processing: "⏳"
    slash_commands:
      enabled: true
      commands:
        - name: "ask"
          description: "Ask a question"
        - name: "help"
          description: "Get help"
  
  # WhatsApp configuration
  whatsapp:
    enabled: false
    account: ${WA_ACCOUNT}
    token: ${WA_TOKEN}
    webhook_url: ${WA_WEBHOOK_URL}
    api_url: "https://api.whatsapp.com/v1"
    media_path: "./media"
    max_media_size: 16777216  # 16MB
    reply_timeout: 300        # seconds
    format:
      phone: "+1234567890"
      country_code: "1"
  
  # Telegram configuration
  telegram:
    enabled: false
    bot_token: ${TG_BOT_TOKEN}
    webhook_url: ${TG_WEBHOOK_URL}
    api_url: "https://api.telegram.org/bot"
    parse_mode: "HTML"        # HTML, Markdown, MarkdownV2
    commands:
      - command: "/start"
        description: "Start the bot"
      - command: "/help"
        description: "Get help"
      - command: "/ask"
        description: "Ask a question"
    inline_keyboards: true
    location_support: false
  
  # Slack configuration
  slack:
    enabled: false
    bot_token: ${SLACK_BOT_TOKEN}
    app_token: ${SLACK_APP_TOKEN}
    webhook_url: ${SLACK_WEBHOOK_URL}
    signing_secret: ${SLACK_SIGNING_SECRET}
    prefix: "!"
    mention_prefix: "@"
    dm_enabled: true
    channels: []
    reactions:
      success: "white_check_mark"
      error: "x"
      processing: "clock3"
  
  # Google Chat configuration
  googlechat:
    enabled: false
    webhook_url: ${GC_WEBHOOK_URL}
    space_id: ${GC_SPACE_ID}
    bot_name: "Engineering Flow Platform"
    mentions: true
    format: "cardsV2"
```

### Per-Channel Advanced Configuration

```yaml
# Discord specific
discord:
  advanced:
    message_cache:
      max_size: 1000
      ttl: 3600      # seconds
    presence:
      status: "online"
      activity:
        type: "WATCHING"
        name: "for commands"
    shard_id: null   # For large bots
    shard_count: 1
    
# WhatsApp specific
whatsapp:
  advanced:
    typing_indicator: true
    read_receipts: true
    message_ttl: 86400   # 24 hours
    media:
      image: ["image/jpeg", "image/png"]
      document: ["application/pdf"]
      audio: ["audio/ogg"]
      video: ["video/mp4"]

# Telegram specific  
telegram:
  advanced:
    file_max_size: 52428800  # 50MB
    proxy:
      enabled: false
      url: "socks5://proxy:1080"
    poll_interval: 1        # seconds
    long_polling:
      enabled: true
      timeout: 60
      limit: 100
```

### Environment Variables

```bash
# Discord
DISCORD_TOKEN=ODY4Mzk1MjI2ODQ0ODkxODA2.G9.G9.discord_token

# WhatsApp
WA_ACCOUNT=myaccount
WA_TOKEN=wa_token_xxxxxxxx
WA_WEBHOOK_URL=https://...

# Telegram
TG_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TG_WEBHOOK_URL=https://...

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=xxx

# Google Chat
GC_WEBHOOK_URL=https://chat.googleapis.com/...
GC_SPACE_ID=spaces/xxx
```

## How to Run

### Start Channel Listener
```bash
# Start all enabled channels
python main.py --channels

# Start specific channel
python main.py --channel discord

# Start with webhook mode (recommended for production)
python main.py --channel webhook
```

### Test Channels
```bash
# Test Discord integration
pytest tests/test_discord.py -v

# Test WhatsApp integration
pytest tests/test_whatsapp.py -v

# Test all channels
pytest tests/test_channel*.py -v

# Channel-specific tests
pytest tests/ -k "discord" -v
```

### Webhook Setup

#### Discord
1. Go to Discord Developer Portal
2. Create application
3. Add bot to server
4. Configure intents
5. Set webhook URL

#### WhatsApp
1. Create Meta Developer account
2. Set up WhatsApp Business account
3. Configure webhook
4. Verify phone number

#### Telegram
1. Contact @BotFather
2. Create new bot
3. Set webhook URL
4. Configure commands

## Development Principles

### 1. Channel Implementation Pattern
```python
class MyChannel(Channel):
    """Example channel implementation."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.client = self._init_client()
    
    def _init_client(self) -> Any:
        """Initialize channel-specific client."""
        ...
    
    def receive(self) -> Message:
        """Implement message receiving."""
        ...
    
    def send(self, response: Response) -> str:
        """Implement sending."""
        ...
```

### 2. Message Format Conversion
```python
def platform_to_unified(platform_msg: Dict) -> Message:
    """Convert platform-specific format to unified Message."""
    return Message(
        content=platform_msg.get("text", ""),
        author_id=platform_msg["from"]["id"],
        channel_id=platform_msg["chat"]["id"],
        guild_id=platform_msg.get("chat", {}).get("guild_id"),
        timestamp=platform_msg.get("date", ""),
        message_id=platform_msg["message_id"],
        attachments=platform_msg.get("attachments", []),
        mentions=platform_msg.get("entities", []),
    )

def unified_to_platform(response: Response) -> Dict:
    """Convert unified Response to platform-specific format."""
    return {
        "text": response.content,
        "chat_id": response.channel_id,
        "reply_to_message_id": response.reply_to,
        "attachments": response.attachments,
        "reply_markup": response.embed,
    }
```

### 3. Error Handling
```python
class ChannelError(Exception):
    """Base channel error."""
    pass

class RateLimitError(ChannelError):
    """Rate limit exceeded."""
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s")

class ChannelUnavailableError(ChannelError):
    """Channel is temporarily unavailable."""
    pass

# Handling
try:
    channel.send(response)
except RateLimitError as e:
    schedule_retry(e.retry_after)
except ChannelUnavailableError:
    enable_fallback_channel()
```

### 4. Testing Standards
```python
class TestDiscordChannel:
    def test_message_receive(self):
        """Test message receiving."""
        channel = DiscordChannel(config)
        msg = channel.receive()
        assert isinstance(msg, Message)
        assert msg.content is not None
    
    def test_message_send(self):
        """Test message sending."""
        channel = DiscordChannel(config)
        response = Response(content="Test", channel_id="123")
        result = channel.send(response)
        assert result is not None
```

## Supported Features by Channel

| Feature | Discord | WhatsApp | Telegram | Slack | Google Chat |
|---------|---------|----------|----------|-------|-------------|
| Text messages | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attachments | ✅ | ✅ | ✅ | ✅ | ✅ |
| Reactions | ✅ | ❌ | ✅ | ✅ | ❌ |
| Threads | ✅ | ❌ | ✅ | ✅ | ✅ |
| Slash commands | ✅ | ❌ | ✅ | ✅ | ❌ |
| Voice messages | ✅ | ✅ | ✅ | ❌ | ❌ |
| Video calls | ❌ | ✅ | ❌ | ❌ | ❌ |
| Polls | ✅ | ❌ | ✅ | ✅ | ❌ |
| Inline buttons | ✅ | ❌ | ✅ | ✅ | ✅ |
| Location sharing | ✅ | ✅ | ✅ | ❌ | ❌ |

## API Reference

### Base Channel (channel/base.py)

```python
class Channel(ABC):
    """Abstract base class for channels."""
    
    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to channel."""
        pass
    
    @abstractmethod
    def disconnect(self):
        """Clean up connection."""
        pass
    
    @abstractmethod
    def receive(self) -> Optional[Message]:
        """Receive next message."""
        pass
    
    @abstractmethod
    def send(self, response: Response) -> str:
        """Send response. Returns message ID."""
        pass
    
    @abstractmethod
    def edit(self, message_id: str, content: str) -> bool:
        """Edit existing message."""
        pass
    
    @abstractmethod
    def delete(self, message_id: str) -> bool:
        """Delete message."""
        pass
    
    @abstractmethod
    def react(self, message_id: str, emoji: str) -> bool:
        """Add reaction."""
        pass
    
    @abstractmethod
    def get_info(self) -> ChannelInfo:
        """Get channel info."""
        pass
```

### Unified Message (channel/base.py)

```python
@dataclass
class Message:
    """Unified message across all channels."""
    content: str
    author_id: str
    channel_id: str
    guild_id: Optional[str]
    timestamp: str
    message_id: str
    platform: str           # "discord", "whatsapp", etc.
    attachments: List[str] = None
    mentions: List[str] = None
    reply_to: Optional[str] = None
    metadata: Dict[str, Any] = None

@dataclass
class Response:
    """Response to be sent to channel."""
    content: str
    channel_id: str
    platform: str = None
    attachments: List[str] = None
    embed: Dict[str, Any] = None
    reply_to: Optional[str] = None
    reply_markup: Dict[str, Any] = None
    mentions: List[str] = None
    typing_indicator: bool = False
```

## Troubleshooting

### Connection Issues
```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Test connection
channel = DiscordChannel(config)
if channel.connect():
    print("Connected successfully")
else:
    print("Connection failed")
```

### Rate Limiting
```python
# Check rate limit status
channel = DiscordChannel(config)
status = channel.get_rate_limit_status()
print(f"Remaining: {status.remaining}/{status.limit}")
```

### Message Not Sending
```bash
# Check channel configuration
python -c "
from channel import ChannelManager
manager = ChannelManager()
status = manager.get_channel_status()
print(status)
"

# Test message format
python -c "
from channel.base import Message
msg = Message(...)
print(msg.content)
"
```
