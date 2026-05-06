# Engineering Flow Platform

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![pytest](https://img.shields.io/badge/pytest-76%20tests-green.svg)](tests/)

## ⚠️ Research Use Only

**This project is currently under active development and is intended for research purposes only.**

---

## About

Engineering Flow Platform is an AI-powered engineering assistant that orchestrates workflows across the SDLC. It integrates with Jira, Confluence, GitHub, and more to automate and accelerate engineering tasks.

### Core Capabilities

- **AI Chat Interface** - Natural language interaction with the agent
- **Multi-Channel Integration** - Jira, Confluence, GitHub, Git, Bash
- **Session Persistence** - Conversations persist across restarts
- **File Attachments** - Support for images in chat (documents via file-parse)
- **Settings Panel** - Web-based configuration for LLM and integrations

---

## Quick Start

### Prerequisites

- Python 3.11+
- API keys for LLM provider (OpenAI, GitHub Copilot, or Anthropic)

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Create config directory
mkdir -p ~/.efp

# Copy config template
cp config.yaml.example ~/.efp/config.yaml

# Edit ~/.efp/config.yaml with your settings
# Minimum required: LLM api_key

# Start server
python main.py
```

### Server Options

```bash
# Default (port 8000)
python main.py

# Custom port (edit config.yaml)
# server:
#   port: 8001

# With encrypted config
EFP_CONFIG_KEY="your-secret-passphrase" python main.py
```

Access the web UI at `http://localhost:8000/`

---

## Configuration

### LLM Providers

```yaml
llm:
  provider: "openai"  # openai (default), github_copilot
  api_key: "sk-..."
  model: "gpt-5.4-mini"
```

### Control-Plane Runtime Settings

```yaml
server:
  jira_reconciliation_enabled: false     # Runtime scheduled Jira reconciliation loop
  jira_reconciliation_interval_seconds: 300
```

Reconciliation/session contract notes:
- Jira reconciliation fallback publishes to Portal via `/api/internal/external-events/ingest` using Portal `ExternalEventIngressRequest`-compatible fields (`workflow_review_requested`, `payload_json`, `project_key`, `issue_key`, etc.).
- Runtime session metadata publish keeps canonical keys first and supports legacy Portal aliases (`portal_group_id`, `portal_task_id`, `portal_delegation_id`, `portal_coordination_run_id`) for cross-version compatibility.

### Integrations

#### Jira (Multiple Instances)
```yaml
jira:
  enabled: true
  instances:
    - name: "Production"
      url: "https://company.atlassian.net"
      project: "PROJ"
      # Auth: Bearer token, Basic (username+password), or Basic (username+api_token)
      token: "your-jira-api-token"
```

#### Confluence (Multiple Instances)
```yaml
confluence:
  enabled: true
  instances:
    - name: "Wiki"
      url: "https://company.atlassian.net/wiki"
      username: "user@company.com"
      password: "your-password"
```

#### GitHub
```yaml
github:
  enabled: true
  api_token: "ghp_xxxx"  # GitHub personal access token
  base_url: ""           # Optional GitHub API base URL (blank => https://api.github.com)
  repos:
    - "owner/repo1"
    - "owner/repo2"
```

`github.api_token` is used by both GitHub REST API tools and Git clone/push/pull over HTTPS.

### Encryption

Sensitive values can be encrypted:

```bash
# Set encryption key via environment
export EFP_CONFIG_KEY="your-32-byte-key"

# Use encrypted values in config
llm:
  api_key: "ENC:base64encryptedvalue..."
```

---

## Project Structure

```
engineering-flow-platform/
├── main.py                 # Server entry point
├── config.yaml.example     # Configuration template
├── requirements.txt        # Python dependencies
├── src/
│   ├── agents/             # Agent core logic
│   │   ├── core.py          # Main agent loop
│   │   ├── llm.py          # LLM client
│   │   ├── executor.py     # Tool execution
│   │   └── memory.py       # Agent memory
│   ├── gateway/            # HTTP server & WebChat
│   │   ├── server.py        # aiohttp server
│   │   ├── webchat.py       # Chat API & UI
│   │   ├── static/          # Web assets
│   │   └── templates/       # HTML templates
│   ├── channels/           # Channel adapters
│   ├── jira/               # Jira integration
│   ├── confluence/         # Confluence integration
│   ├── github/             # GitHub integration
│   ├── git/                # Git tools
│   ├── memory/             # Memory system
│   ├── sessions/           # Session persistence
│   ├── tools/              # Built-in tools
│   ├── hooks/              # Lifecycle hooks
│   └── utils/              # Utilities
│       └── file_parser/     # File upload & storage
├── src/skills/             # Runtime skill registry/loading infrastructure
├── tests/                  # Test suite
└── workspace/               # Workspace files (for local dev)
    └── .efp/               # Runtime data
```

---

Business skill assets are maintained in **engineering-flow-platform-skills** and are typically checked out/mounted by Portal/K8s at `/app/skills` for runtime discovery.

## API Endpoints

### Chat

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat` | POST | Send message to agent |
| `/api/chat/stream` | POST | Streaming chat response |

**Chat Request:**
```json
{
  "message": "What are the open Jira tickets?",
  "session_id": "optional-session-id",
  "attachments": ["file_id1", "file_id2"]
}
```

### Control-Plane Runtime Endpoints (Internal)

| Endpoint | Method | Description | Required Header |
|----------|--------|-------------|-----------------|
| `/api/tasks/execute` | POST | Runtime task execution bridge | none (internal network / Portal proxy topology) |
| `/api/capabilities` | GET | Runtime capability snapshot/filter API | none (internal network / Portal proxy topology) |

### Phase 5 Trust Contract (Portal ↔ Runtime)

- `/api/chat` and `/api/chat/stream` remain usable for direct runtime chat.
- Governance/capability metadata is only applied for **trusted Portal requests**.
- Trusted chat request requires:
  - `X-Portal-Author-Source: portal`.
- `portal_user_id` / `portal_user_name` are trusted identity headers only (`X-Portal-User-Id`, `X-Portal-User-Name`).

For complete control-plane contract details, see `docs/control_plane_contract.md`.

Additional runtime contracts:
- `docs/runtime_contract.md`
- `docs/observability_contract.md`

### Portal Control-Plane Integration (Operator Minimum)

1. **Portal -> EFP trusted chat**  
   Header: `X-Portal-Author-Source: portal`.

2. **Portal -> EFP internal runtime endpoints** (`/api/tasks/execute`, `/api/capabilities`)  
   The current deployment mode relies on the trusted Portal source/header contract.

3. **EFP adapter -> Portal internal APIs** (`adapter:portal:*`)  
   Requires `PORTAL_INTERNAL_BASE_URL` (env) or `server.portal_internal_base_url`.  

### Sessions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sessions` | GET | List all sessions |
| `/api/sessions/{id}` | GET | Load session history |
| `/api/sessions/{id}/rename` | POST | Rename session |
| `/api/sessions/{id}` | DELETE | Delete session |
| `/api/sessions/{id}/clear` | POST | Clear session history |
| `/api/clear` | POST | Clear all sessions |

### Files

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/files/upload` | POST | Upload file (multipart) |
| `/api/files` | GET | List files |
| `/api/files/{id}` | GET | Download file |
| `/api/files/parse` | POST | Parse file content (body: {file_id}) |
| `/api/files/{id}/preview` | GET | Get file preview |

### Settings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Get current config |
| `/api/config/save` | POST | Save config |
| `/api/git-info` | GET | Git repository info |

---

## Chat Attachments

### Sending Attachments

The chat API supports file attachments in two ways:

1. **New format** (recommended): Send `attachments` array in JSON body
   ```json
   {
     "message": "Analyze this image",
     "attachments": ["file_id1", "file_id2"]
   }
   ```

2. **Legacy format**: Include `@file_<id>` in message text
   ```
   What is in @file_abc12345?
   ```

Only the first image attachment is processed to avoid large payloads.

### Uploading Files

```
POST /api/files/upload
Content-Type: multipart/form-data

file: <binary>
```

Returns:
```json
{
  "success": true,
  "file_id": "uuid...",
  "filename": "example.png",
  "content_type": "image/png",
  "size": 12345,
  "uploaded_at": "2024-01-01T00:00:00Z"
}
```

---

## Session Management

Sessions are automatically persisted to `~/.efp/workspace/sessions/`.

### Session Structure
```
~/.efp/workspace/sessions/
├── {session_id}_{hash}.jsonl  # Session conversation history
└── archive/                   # Archived sessions
```

### Configuration
```yaml
session:
  max_history: 5        # Turns to keep in context
  max_iterations: 30    # Max tool calls per turn
  persistence:
    enabled: true
    storage_dir: "~/.efp/workspace/sessions"
    ttl_seconds: 2592000  # 30 days
```

---

## Memory System

### Workspace Files

Located at `~/.efp/workspace/`:

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent persona and behavior |
| `USER.md` | User preferences |
| `AGENTS.md` | Workspace conventions |
| `TOOLS.md` | Tool configurations |
| `MEMORY.md` | Long-term memory |
| `memory/YYYY-MM-DD.md` | Daily memory |

### Memory Generation

- **Startup**: Generates daily memory from session events
- **Hourly**: Checks for changes, regenerates if needed
- **Long-term**: Consolidates last 3 days to MEMORY.md

---

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Adding Tests

Create test files in `tests/` following `test_*.py` pattern.

### Code Style

- Follow existing code style in the project
- Use type hints where helpful
- Add docstrings for public APIs

### Adding New Integrations

1. Create module in `src/{integration}/`
2. Implement API client
3. Add config schema to `config.py`
4. Add tools in `src/tools/`
5. Document in README

---

## Troubleshooting

### Server Won't Start

1. Check config.yaml exists and has valid YAML
2. Verify LLM api_key is set
3. Check port is not in use: `lsof -i :8000`

### Chat Not Working

1. Check LLM configuration is correct
2. Verify API key has sufficient credits
3. Check server logs for errors

### File Upload Fails

1. Ensure upload directory exists: `~/.efp/workspace/uploads/`
2. Check file size limits
3. Verify file type is allowed

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Support

- Issues: https://github.com/dvnuo/engineering-flow-platform/issues
- Discussions: https://github.com/dvnuo/engineering-flow-platform/discussions
