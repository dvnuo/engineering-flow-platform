# Engineering Flow Platform

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![pytest](https://img.shields.io/badge/pytest-76%20tests-green.svg)](tests/)

## ⚠️ Research Use Only

**This project is currently under active development and is intended for research purposes only.**

---

## About

Engineering Flow Platform is an AI-powered engineering assistant that orchestrates workflows across the SDLC. It integrates with Jira, Confluence, GitHub, and more to automate and accelerate engineering tasks.

### Core Capabilities

- **Runtime API Chat** - Natural language interaction with the agent over the HTTP API
- **EFP Runtime Chat** - Portal and Jira chat use the EFP runtime with GitHub Copilot
- **Multi-Channel Integration** - Jira, Confluence, GitHub, Git, Bash outside the model-visible tool surface
- **Session Persistence** - Conversations persist across restarts
- **File Attachments** - Support for Portal-provided transient image and document attachment ids in chat
- **Runtime Operations APIs** - Configuration reload and runtime metadata endpoints for operations

---

## Quick Start

### Prerequisites

- Python 3.11+
- GitHub Copilot token for EFP runtime native chat

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

The native runtime is API-only. Check health and call chat endpoints directly:

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello","session_id":"local-dev"}'
```

---

## Configuration

### LLM Providers

```yaml
llm:
  provider: "github_copilot"
  api_key: "ghu_..."
  model: "gpt-5.4"
  reasoning_effort: "high"
```

EFP runtime native mode does not fall back to OpenAI or Anthropic providers.
`EFP_GITHUB_COPILOT_TOKEN` or `GITHUB_COPILOT_TOKEN` may be used instead of
`llm.api_key`; `llm.api_base` or `EFP_GITHUB_COPILOT_BASE_URL` can override the
Copilot transport base URL.

Supported GitHub Copilot models are `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.5`,
`gpt-5.3-codex`, `gpt-5-mini`, `gemini-2.5-pro`, and `gemini-3.5-flash`.

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
│   ├── efp_runtime/         # EFP runtime AgentRuntime, provider, session, and tools
│   ├── agents/              # Compatibility support modules only; legacy loop removed
│   ├── gateway/            # API-only HTTP server
│   │   ├── server.py        # aiohttp server
│   │   ├── runtime_api.py   # Portal/runtime API routes
│   │   └── runtime_request_contracts.py
│   ├── channels/           # Channel adapters
│   ├── jira/               # Jira integration
│   ├── confluence/         # Confluence integration
│   ├── github/             # GitHub integration
│   ├── git/                # Git integration helpers
│   ├── memory/             # Memory system
│   ├── sessions/           # Session persistence
│   ├── runtime/            # Runtime task/control-plane orchestration
│   ├── hooks/              # Lifecycle hooks
│   └── utils/              # Utilities
│       └── file_parser/     # Attachment parsing/storage helpers
├── src/skills/             # Runtime skill registry/loading infrastructure
├── tests/                  # Test suite
└── workspace/               # Workspace files (for local dev)
    └── .efp/               # Runtime data
```

---

Business skill assets are maintained in **engineering-flow-platform-skills**. Portal/K8s typically checks out/mounts that skills repository at `/app/skills` (or another path via `EFP_SKILLS_DIR`) for runtime discovery.

EFP native runtime exposes only EFP-owned built-in LLM tools
(`bash`, `read`, `write`, `edit`, `grep`, `glob`, `webfetch`, `todowrite`,
`apply_patch`, plus other EFP runtime built-ins). Legacy Python tool packages,
including `src/bash_tools`, are not part of the production LLM tool surface.
Runtime image builds can also place prebuilt `engineering-flow-platform-tools`
CLI binaries such as `jira`, `confluence`, and `browser` on `PATH` in
`/usr/local/bin`. Agents invoke those CLIs through the EFP `bash` built-in from
the workspace; they are not registered as separate model-facing function tools
and are not loaded through `EFP_TOOLS_DIR` or `EFP_EXTERNAL_TOOLS_*`.

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
- `docs/runtime-design.md`
- `docs/runtime-tool-surface.md`
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

### Settings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config/reload` | POST | Reload runtime config |
| `/api/git-info` | GET | Git repository info |

---

## Chat Attachments

### Sending Attachments

Portal can pass runtime-known transient attachment ids in the `attachments` array:

```json
{
  "message": "Analyze this image",
  "attachments": ["file_id1", "file_id2"]
}
```

Attachment bytes are provided by Portal and resolved by runtime storage helpers.

---

## Session Management

Sessions are automatically persisted to `/workspace/sessions/` by default.

### Session Structure
```
/workspace/sessions/
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
    storage_dir: "/workspace/sessions"
    ttl_seconds: 2592000  # 30 days
```

---

## Memory System

### Workspace Files

Located at `/workspace/` by default:

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
4. Add model-visible tools through `src/efp_runtime/tools/builtin/`
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

## License

MIT License - see [LICENSE](LICENSE) for details.

---

## Support

- Issues: https://github.com/dvnuo/engineering-flow-platform/issues
- Discussions: https://github.com/dvnuo/engineering-flow-platform/discussions
