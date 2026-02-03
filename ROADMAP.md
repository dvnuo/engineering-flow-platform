# OpenClaw vs CodeW Feature Comparison & Roadmap

## Overview

| Aspect | OpenClaw (Original) | CodeW (Our Implementation) |
|--------|---------------------|---------------------------|
| **Language** | TypeScript/Node.js | Python |
| **License** | MIT | MIT |
| **Architecture** | Gateway + Pi Agent | Simple monolithic |
| **Status** | Production ready | In development |

---

## Feature Comparison Matrix (Updated 2026-02-02)

### Core Platform

| Feature | OpenClaw | CodeW | Status |
|---------|----------|-------|--------|
| Gateway WS Control Plane | ✅ | ⚠️ HTTP only | TODO |
| Sessions Management | ✅ | ✅ Persistent + Queue | Complete |
| Presence & Typing | ✅ | ❌ | TODO |
| Configuration System | ✅ JSON | ✅ YAML + Hot Reload | Complete |
| Multi-agent Routing | ✅ | ❌ | TODO |
| Session Pruning | ✅ | ✅ Basic | Complete |
| Retry Policy | ✅ | ⚠️ Basic (LLM only) | Partial |
| Streaming/Chunks | ✅ | ❌ | TODO |
| Execution Queue | ✅ | ✅ Per-session | Complete |

### Channels

| Feature | OpenClaw | CodeW | Status |
|---------|----------|-------|--------|
| Discord | ✅ | ✅ Bot API | Complete |
| WhatsApp | ✅ Baileys | ❌ | TODO |
| Telegram | ✅ grammY | ❌ | TODO |
| Slack | ✅ Bolt | ❌ | TODO |
| Google Chat | ✅ | ❌ | TODO |
| Signal | ✅ signal-cli | ❌ | TODO |
| iMessage | ✅ imsg | ❌ | TODO |
| Microsoft Teams | ✅ | ❌ | TODO |
| Jira | ✅ | ⚠️ Webhook only | Partial |
| WebChat | ✅ | ✅ HTTP + Static Files | Complete |

### Tools & Automation

| Feature | OpenClaw | CodeW | Status |
|---------|----------|-------|--------|
| exec | ✅ | ✅ | Complete |
| read | ✅ | ✅ | Complete |
| write | ✅ | ✅ | Complete |
| edit | ✅ apply_patch | ✅ | Complete |
| web_search | ✅ Brave | ✅ Brave | Complete |
| web_fetch | ✅ | ✅ | Complete |
| browser | ✅ CDP control | ❌ | TODO |
| canvas | ✅ A2UI | ❌ | TODO |
| nodes | ✅ Device control | ❌ | TODO |
| cron | ✅ | ❌ | TODO |
| sessions_* | ✅ Multi-agent | ❌ | TODO |
| message | ✅ All channels | ⚠️ Discord only | Partial |

### Agent Capabilities

| Feature | OpenClaw | CodeW | Status |
|---------|----------|-------|--------|
| Tool Calling | ✅ Claude-native | ✅ OpenAI Function | Complete |
| ReAct Pattern | ✅ pi-agent-core | ✅ Custom | Complete |
| System Prompt | ✅ Dynamic | ✅ Template | Complete |
| Memory/Context | ✅ Compaction | ✅ Pruning + Compactor | Complete |
| Multi-turn | ✅ | ✅ | Complete |
| Thinking Levels | ✅ | ❌ | TODO |
| Usage Tracking | ✅ | ✅ Token + Cost | Complete |

### Apps & Nodes

| Feature | OpenClaw | CodeW | Status |
|---------|----------|-------|--------|
| macOS App | ✅ Menu bar | ❌ | TODO |
| iOS Node | ✅ | ❌ | TODO |
| Android Node | ✅ | ❌ | TODO |
| Voice Wake | ✅ ElevenLabs | ❌ | TODO |
| Talk Mode | ✅ | ❌ | TODO |
| Canvas Render | ✅ A2UI | ❌ | TODO |

### Security

| Feature | OpenClaw | CodeW | Status |
|---------|----------|-------|--------|
| DM Pairing | ✅ | ❌ | TODO |
| Sandbox Mode | ✅ Docker | ❌ | TODO |
| Allowlist/Denylist | ✅ | ❌ | TODO |
| Elevated Mode | ✅ | ❌ | TODO |

### DevOps

| Feature | OpenClaw | CodeW | Status |
|---------|----------|-------|--------|
| Health Checks | ✅ | ✅ /health | Complete |
| Config Reload | ✅ | ✅ API | Complete |
| Logging | ✅ Structured | ⚠️ Basic | Partial |
| **Docker Support** | ✅ | ✅ | **Complete** |
| Tailscale | ✅ Serve/Funnel | ❌ | TODO |
| WebChat UI | ✅ | ✅ HTML/CSS/JS | Complete |
| **Workspace Volume** | ✅ | ✅ | **Complete** |

---

## Progress Summary (2026-02-02)

### Completed This Session ✅

| Feature | File | Tests |
|---------|------|-------|
| Session Persistence | `session/persistence.py` | ✅ 9 tests |
| Usage Tracking | `session/usage.py` | ✅ 10 tests |
| Execution Queue | `agent/queue.py` | ✅ Tests passed |
| Session Pruning | `session/pruning.py` | ✅ Tests passed |
| WebChat UI | `gateway/webchat.py` + `templates/` + `static/` | ✅ 12 tests |
| Usage in Agent | `agent/core.py`, `agent/llm.py` | ✅ |

### Statistics

| Metric | Count |
|--------|-------|
| Total Files | 40+ |
| Total Lines | 5000+ |
| Tests | 76+ |
| PRs Merged | 6+ |
| Channels | 2 (+5 planned) |
| Skills | 2 (+10 planned) |

---

## Roadmap (Priority Order)

### Phase 1: Core Stability ✅ DONE
- [x] Discord Channel
- [x] LLM Integration (OpenAI)
- [x] Basic Tools (exec, read, write, edit)
- [x] Tool Calling (ReAct Pattern)
- [x] Config Hot Reload
- [x] Session Persistence
- [x] Usage Tracking
- [x] WebChat UI
- [x] Session Pruning
- [x] Execution Queue

### Phase 2: Channel Expansion (Next - Updated 2026-02-02)
Based on comparison with OpenClaw, priority channels:

| Priority | Channel | Library | Status |
|----------|---------|---------|--------|
| P0 | **Telegram** | python-telegram-bot | TODO |
| P0 | **Slack** | slack-sdk | TODO |
| P1 | **WhatsApp** | pyrogram/baileys | TODO |
| P1 | **WebSocket** | Native aiohttp | TODO |
| P2 | **Signal** | signal-cli | TODO |
| P2 | **iMessage** | py-imessage | TODO |

- [ ] Add Telegram channel (P0 - most requested)
- [ ] Add Slack channel (P0 - work场景)
- [ ] Add WebSocket support for real-time (P1)
- [ ] Improve Jira integration (bi-directional) (P1)

### Phase 3: Tool Enhancement (Updated 2026-02-02)
Based on comparison with OpenClaw:

| Priority | Tool | Status |
|----------|------|--------|
| P0 | **Weather** (skill) | TODO |
| P0 | **Summarize** (skill) | TODO |
| P1 | **browser** (CDP) | TODO |
| P1 | **cron** (scheduling) | TODO |
| P1 | **Hooks system** | TODO |
| P1 | **Claude provider** | TODO |
| P2 | **canvas** (A2UI) | TODO |
| P2 | **TTS** (ElevenLabs) | TODO |
| P2 | **Ollama** (local LLM) | TODO |

- [ ] Implement weather skill (no API key required)
- [ ] Implement summarize skill
- [ ] Implement browser control (CDP)
- [ ] Add cron support for scheduled tasks
- [ ] Add hooks system for extensibility
- [ ] Add Claude provider support
- [ ] Implement canvas/A2UI (low priority)
- [ ] Add TTS support (low priority)
- [ ] Add Ollama local LLM support (low priority)

### Phase 4: Security & Polish
- [ ] DM pairing mode
- [ ] Tool allowlist/denylist
- [ ] Elevated mode
- [ ] Audit logging

### Phase 5: Advanced Features
- [ ] Voice Wake (ElevenLabs)
- [ ] iOS/Android nodes
- [ ] macOS companion app
- [ ] Tailscale integration
- [ ] Docker deployment

---

## Gap Analysis - What Still Needs Implementation

### High Priority (Channels)
1. **WhatsApp** - Baileys library needed
2. **Telegram** - grammY library needed
3. **WebChat WebSocket** - Real-time updates

### Medium Priority (Tools)
1. **browser** - CDP control
2. **canvas** - A2UI rendering
3. **nodes** - Device control
4. **cron** - Scheduled tasks

### Low Priority (Advanced)
1. **Voice** - ElevenLabs integration
2. **Docker** - Container support
3. **Tailscale** - Remote access
4. **Sandbox** - Security isolation

---

## Code Structure Comparison

### OpenClaw Structure
```
openclaw/
├── src/
│   ├── agent/          # Pi agent runtime
│   ├── gateway/        # WS control plane
│   ├── channels/       # 15+ messaging channels
│   ├── tools/          # 20+ tools
│   ├── nodes/          # Device nodes
│   └── platforms/      # macOS, iOS, Android
├── docs/               # Full documentation
└── packages/           # npm packages
```

### CodeW Structure (Current - Updated 2026-02-02)
```
codew/
├── main.py             # Entry point
├── config.py          # YAML config loader
├── Dockerfile         # Container image
├── docker-compose.yml # Docker deployment
├── agent/             # LLM + ReAct agent
│   ├── core.py        # Agent logic
│   ├── llm.py         # LLM client
│   ├── memory.py      # Memory system (NEW)
│   └── queue.py       # Execution queue
├── gateway/           # HTTP server + webhooks
│   ├── server.py      # Main server
│   ├── webchat.py     # WebChat handler
│   ├── templates/     # HTML templates
│   └── static/        # CSS/JS files
├── channel/           # Message channels
│   ├── discord.py     # Discord bot
│   └── jira.py        # Jira webhook
├── session/           # Session management
│   ├── manager.py     # In-memory sessions
│   ├── persistence.py # JSONL persistence
│   ├── usage.py       # Token tracking
│   └── pruning.py     # Context pruning
├── skills/            # Skills executor
│   ├── executor/      # Skill execution framework
│   └── test_case_generator/ # Test case skill
├── workspace/         # Memory files (NEW)
│   ├── *.example      # Template files
│   └── memory/        # Daily notes
├── tests/             # Unit tests (76+ tests)
└── docs/              # Documentation
    └── COMPARISON.md  # CodeW vs OpenClaw analysis
```

---

## References

- OpenClaw Docs: https://docs.openclaw.ai
- OpenClaw GitHub: https://github.com/openclaw/openclaw
- OpenClaw Discord: https://discord.gg/clawd
- **CodeW vs OpenClaw Comparison**: [docs/COMPARISON.md](docs/COMPARISON.md)
- **OpenClaw Original**: `/root/.openclaw/workspace/openclaw_original/`

## Comparison Summary (2026-02-02)

| Dimension | OpenClaw | CodeW | Gap |
|-----------|----------|-------|-----|
| Lines of Code | ~87,000 | ~5,000 | 17x |
| Channels | 26 | 2 | 24 |
| Skills | 54 | 2 | 52 |
| Platforms | macOS/iOS/Android | Web only | 3 |
| Language | TypeScript | Python | - |
| Memory System | ✅ | ✅ | Equal |
| Docker Support | ✅ | ✅ | Equal |

### Recommended Next Steps

1. **Telegram Channel** - Most requested, mature Python library
2. **Slack Channel** - Work场景, high demand
3. **Weather Skill** - No API key, quick win
4. **Summarize Skill** - High utility, easy to implement
5. **Complete Persistence** - SQLite + disk storage

See [docs/COMPARISON.md](docs/COMPARISON.md) for detailed analysis.

---

*Last updated: 2026-02-02*
*Generated for CodeW project roadmap planning*

---

## Phase 6: Long-term Memory Enhancement (NEW - 2026-02-03)

Based on OpenClaw's memory system design, implement SQLite + vector search for durable memory.

### Reference: OpenClaw Memory System

| Component | OpenClaw | CodeW (Current) | Implementation |
|-----------|----------|-----------------|----------------|
| **Storage** | SQLite + sqlite-vec | Markdown files | TODO |
| **Vector Search** | ✅ | ❌ | TODO |
| **Semantic Search** | ✅ | ❌ | TODO |
| **Hybrid Search** | Vector + BM25 | ❌ | TODO |
| **Embedding Cache** | SQLite-based | ❌ | TODO |
| **Session Indexing** | Optional | ❌ | TODO |

### Memory Layers (Reference from OpenClaw)

```
┌─────────────────────────────────────────────────────────────┐
│                    ~/.openclaw/memory/                       │
│                    <agentId>.sqlite                          │
├─────────────────────────────────────────────────────────────┤
│ Layer 1: Daily Notes                                         │
│   - File: memory/YYYY-MM-DD.md                              │
│   - Purpose: Day-to-day context                             │
│   - Lifecycle: Read today + yesterday at session start      │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Long-term Memory                                   │
│   - File: MEMORY.md                                         │
│   - Purpose: Durable decisions, preferences, facts          │
│   - Context: Only load in main private session              │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: Session Transcripts (Optional)                     │
│   - File: ~/.openclaw/agents/<agentId>/sessions/*.jsonl    │
│   - Purpose: Index session history for semantic search      │
│   - Note: Opt-in, debounced async indexing                  │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Plan

#### Step 1: SQLite Storage Layer
- [ ] Create `memory/sqlite_store.py`
- [ ] Implement SQLite connection management
- [ ] Define schema for memory chunks and embeddings
- [ ] Add migration support for existing Markdown files

#### Step 2: Vector Search Integration
- [ ] Evaluate embedding providers:
  - OpenAI `text-embedding-3-small`
  - Local (GGUF via node-llama-cpp)
  - Custom OpenAI-compatible endpoint
- [ ] Implement embedding cache in SQLite
- [ ] Add sqlite-vec extension support (optional acceleration)

#### Step 3: Hybrid Search (Vector + BM25)
- [ ] Implement BM25 full-text search
- [ ] Create weighted score fusion:
  ```
  finalScore = 0.7 * vectorScore + 0.3 * textScore
  ```
- [ ] Add candidate pool retrieval and union

#### Step 4: Memory Tools Integration
- [ ] Update `memory_search` tool with semantic search
- [ ] Update `memory_get` tool for SQLite-backed retrieval
- [ ] Add memory index watching (debounced sync)

#### Step 5: Configuration
- [ ] Add memory config section to `config.yaml.example`:
  ```yaml
  memory:
    enabled: true
    provider: "openai"  # or "local", "gemini"
    model: "text-embedding-3-small"
    hybrid:
      enabled: true
      vector_weight: 0.7
      text_weight: 0.3
    cache:
      enabled: true
      max_entries: 50000
  ```

### Files to Create/Modify

| File | Changes |
|------|---------|
| `memory/sqlite_store.py` | NEW - SQLite storage layer |
| `memory/embedding.py` | NEW - Embedding provider |
| `memory/search.py` | NEW - Hybrid search engine |
| `memory/config.py` | NEW - Memory configuration |
| `memory/__init__.py` | NEW - Module exports |
| `config.yaml.example` | ADD - Memory config section |
| `skills/executor/tools.py` | UPDATE - Register memory tools |

### Dependencies

| Package | Purpose |
|---------|---------|
| `sqlite-vec` | Vector acceleration (optional) |
| `chromadb` | Alternative vector DB (simpler) |
| `sentence-transformers` | Local embeddings (no API key) |
| `rank-bm25` | BM25 implementation |

### Estimated Effort

| Task | Complexity | Time |
|------|------------|------|
| SQLite storage layer | Medium | 2-3 days |
| Embedding integration | Medium | 2-3 days |
| Hybrid search | Medium | 2-3 days |
| Tool integration | Low | 1 day |
| Testing | Medium | 2 days |
| **Total** | - | **9-12 days** |

### Quick Win Options

1. **SQLite only (no vectors)**: Store memory chunks in SQLite, skip vector search
   - Time: 2-3 days
   - Benefit: Fast lookups, structured storage

2. **Sentence-Transformers local**: Free embeddings, no API key needed
   - Time: 1-2 days
   - Model: `all-MiniLM-L6-v2` (~90MB, CPU-friendly)

3. **BM25 only**: Keyword search without vectors
   - Time: 1 day
   - Benefit: Exact match for IDs, env vars, code symbols
