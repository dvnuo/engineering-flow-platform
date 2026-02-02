# Mention Polling Feature

Monitor @mentions across GitHub, Jira, and Confluence, and automatically process commands.

## Overview

This feature allows the bot to monitor when specific users are mentioned in comments across platforms, then parse and execute commands based on the mention text.

## Features

- **Multi-platform support**: GitHub, Jira, Confluence
- **Configurable monitoring**: Choose which repos/projects/spaces to monitor
- **Command parsing**: Natural language commands for common operations
- **Automatic replies**: Bot responds directly in the comment thread
- **Rate limit handling**: Exponential backoff for GitHub API rate limits
- **Production-ready**: Integrated into `main.py` for automatic startup

## Configuration

### Basic Setup

```yaml
# config.yaml
polling:
  enabled: true
  interval_seconds: 30
  monitored_usernames:
    - "your-github-username"
    - "your-jira-username"
    - "your-confluence-username"
```

### Platform-specific Configuration

```yaml
polling:
  enabled: true
  
  github:
    enabled: true
    repos:
      - "owner/repo1"
      - "owner/repo2"
  
  jira:
    enabled: true
    projects:
      - "PROJECT1"
      - "PROJECT2"
  
  confluence:
    enabled: true
    spaces:
      - "DEV"
      - "DOCS"
```

## Usage

### Supported Commands

#### Jira Commands

```
@lucaslai create issue "Issue Title" -d "Description"
@lucaslai status PROJ-123
@lucaslai help
```

#### Confluence Commands

```
@lucaslai search confluence "API Documentation"
@lucaslai help
```

#### GitHub Commands

```
@lucaslai status owner/repo/123
@lucaslai help
```

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                    Polling Loop                          │
│                    (every 30 seconds)                    │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ↓               ↓               ↓
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  GitHub     │  │   Jira      │  │ Confluence  │
│  Polling    │  │  Polling    │  │  Polling    │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       └────────────────┼────────────────┘
                        ↓
           ┌────────────────────────┐
           │  Extract @mentions     │
           │  Check against config  │
           └───────────┬────────────┘
                       ↓
           ┌────────────────────────┐
           │  Parse command         │
           └───────────┬────────────┘
                       ↓
           ┌────────────────────────┐
           │  Execute via tools     │
           └───────────┬────────────┘
                       ↓
           ┌────────────────────────┐
           │  Reply to comment      │
           └────────────────────────┘
```

## Architecture

### Files

```
codew/
├── cron/
│   └── mention_poller.py      # Main polling logic
├── channel/
│   ├── github.py              # GitHub API client
│   ├── jira.py                # Jira API client
│   └── confluence.py          # Confluence API client
├── config.py                  # Configuration management
└── tests/
    └── test_mention_poller.py # Unit tests
```

### Components

| Component | Description |
|-----------|-------------|
| `MentionPoller` | Main class that handles polling loop |
| `extract_mentions()` | Extract @mentions from text |
| `parse_command()` | Parse commands from mention text |
| `_reply_to()` | Reply to the original comment |

## Starting the Poller

```python
from cron.mention_poller import start_polling, stop_polling, is_enabled

# Check if enabled
if is_enabled():
    await start_polling()

# Later, to stop
await stop_polling()
```

## Example Flow

1. User comments on Jira issue PROJ-123:
   > "请 @lucaslai 帮忙看一下这个问题，帮我创建一个新 issue"

2. Bot detects @lucaslai mention

3. Bot parses command: `create issue "新 issue"`

4. Bot executes: `jira_create_issue(project="PROJ", summary="新 issue")`

5. Bot replies:
   > @author 处理结果:
   > 
   > Issue created: **PROJ-456**
   > Summary: 新 issue

## Testing

```bash
# Run mention poller tests
python3 -m pytest tests/test_mention_poller.py -v
```

## Rate Limit Handling

GitHub API has rate limits. The GitHub channel implements exponential backoff:

- **Initial backoff**: 1 second
- **Max backoff**: 60 seconds
- **Max retries**: 5 attempts

When rate limited (403 response), the bot will:
1. Check for `X-RateLimit-Reset` header for exact reset time
2. If not available, use exponential backoff
3. Log warnings for each retry attempt

## Integration

### Automatic Startup (via main.py)

The mention poller is automatically started when:
1. `polling.enabled` is set to `true` in `config.yaml`
2. The main application is started (`python main.py`)

```python
# In main.py
from cron.mention_poller import start_polling, stop_polling, is_enabled

# Start polling if enabled
if is_enabled():
    logger.info("Starting mention polling...")
    polling_task = asyncio.create_task(start_polling())

# Stop on shutdown
await stop_polling()
```

### Manual Startup

```python
from cron.mention_poller import start_polling, stop_polling, is_enabled

if is_enabled():
    await start_polling()
```

## Future Enhancements

- [ ] Natural language command parsing with LLM
- [ ] Conversation context across multiple comments
- [ ] Rate limiting per user/platform
- [ ] Command history and undo
- [ ] Multi-language support
