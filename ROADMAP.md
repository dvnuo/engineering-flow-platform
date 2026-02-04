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
| Slack | ✅ Bolt | ❌ | TODO |
| WhatsApp | ✅ Baileys | ❌ | TODO |
| Signal | ✅ signal-cli | ❌ | TODO |
| iMessage | ✅ imsg | ❌ | TODO |
| Jira | ✅ | ✅ REST API v2/v3 | Complete |
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
| Channels | 2 (+4 planned) |
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

### Phase 2: Channel Expansion (Next - Updated 2026-02-03)
Based on comparison with OpenClaw and enterprise needs:

| Priority | Channel | Library | Status |
|----------|---------|---------|--------|
| P0 | **Slack** | slack-sdk | TODO |
| P0 | **WebSocket** | Native aiohttp | TODO |
| P1 | **WhatsApp** | pyrogram/baileys | TODO |
| P2 | **Signal** | signal-cli | TODO |
| P2 | **iMessage** | py-imessage | TODO |

- [ ] Add Slack channel (P0 - work场景, 企业内部)
- [ ] Add WebSocket support for real-time (P0)
- [ ] Add WhatsApp channel (P1)
- [ ] Add Signal channel (P2)
- [ ] Add iMessage channel (P2)

### Jira Tools (Updated 2026-02-04)

Full Jira REST API v2/v3 support with 8 tools:

| Tool | Description |
|------|-------------|
| `jira_get_issue` | Get issue details |
| `jira_search` | Search with JQL |
| `jira_create_issue` | Create new issue |
| `jira_edit_issue` | **NEW** - Edit existing issue |
| `jira_transition` | Change status |
| `jira_get_transitions` | List available transitions |
| `jira_add_comment` | Add comment |
| `jira_get_comments` | Get comments |

### Phase 3: Tool Enhancement (Updated 2026-02-03)
Based on comparison with OpenClaw and enterprise needs:

| Priority | Tool | Status |
|----------|------|--------|
| P0 | **Summarize** (skill) | TODO |
| P1 | **browser** (CDP) | TODO |
| P1 | **cron** (scheduling) | TODO |
| P1 | **Hooks system** | TODO |
| P1 | **Claude provider** | TODO |
| P2 | **canvas** (A2UI) | TODO |
| P2 | **TTS** (ElevenLabs) | TODO |
| P2 | **Ollama** (local LLM) | TODO |

- [ ] Implement summarize skill (高实用性)
- [ ] Implement browser control (CDP)
- [ ] Add cron support for scheduled tasks
- [ ] Add hooks system for extensibility
- [ ] Add Claude provider support
- [ ] Implement canvas/A2UI (低优先级)
- [ ] Add TTS support (低优先级)
- [ ] Add Ollama local LLM support (低优先级)

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
1. **Slack** - Work场景, 企业内部
2. **WebSocket** - Real-time updates
3. **WhatsApp** - Baileys library needed

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
opsclaw/
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

- OpenClaw Docs: https://docs.opsclaw.ai
- OpenClaw GitHub: https://github.com/openclaw/openclaw
- OpenClaw Discord: https://discord.gg/clawd
- **CodeW vs OpenClaw Comparison**: [docs/COMPARISON.md](docs/COMPARISON.md)
- **OpenClaw Original**: `/root/.opsclaw/workspace/openclaw_original/`

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

1. **Slack Channel** - Work场景, 企业内部
2. **Summarize Skill** - 高实用性, 快速实现
3. **Complete Persistence** - SQLite + disk storage

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
| **Storage** | SQLite | ✅ SQLite | Complete |
| **Full-text Search** | ✅ FTS5 | ✅ FTS5 | Complete |
| **Vector Search** | ✅ sqlite-vec | 🔄 Requires additional deps | TODO |
| **Semantic Search** | ✅ | 🔄 Requires embedding provider | TODO |
| **Hybrid Search** | Vector + BM25 | 🔄 FTS5 only (BM25) | Partial |
| **Embedding Cache** | SQLite-based | ✅ Metadata only | Partial |
| **Session Indexing** | Optional | ❌ | TODO |

### Memory Layers (Reference from OpenClaw)

```
┌─────────────────────────────────────────────────────────────┐
│                    ~/.opsclaw/memory/                       │
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
│   - File: ~/.opsclaw/agents/<agentId>/sessions/*.jsonl    │
│   - Purpose: Index session history for semantic search      │
│   - Note: Opt-in, debounced async indexing                  │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Plan

#### Step 1: SQLite Storage Layer ✅ COMPLETED
- [x] Create `memory/sqlite_store.py`
- [x] Implement SQLite connection management
- [x] Define schema for memory chunks and embeddings
- [x] Add FTS5 full-text search (BM25)

#### Step 2: Vector Search Integration 📋 NEXT
- [ ] Evaluate embedding providers:
  - Option A: **sqlite-vec** (native, recommended by OpenClaw)
    - Fast in-process vector operations
    - Requires: `pip install sqlite-vec`
  - Option B: **External Vector DB** (ChromaDB, Weaviate, etc.)
    - Better for large-scale deployments
    - Requires: `pip install chromadb` or similar
  - Option C: **sentence-transformers** (local, no API key)
    - Model: `all-MiniLM-L6-v2` (~90MB, CPU-friendly)
- [ ] Implement embedding cache in SQLite
- [ ] Add sqlite-vec extension support (optional acceleration)

#### Step 3: Hybrid Search (Vector + BM25) ✅ PARTIAL
- [x] BM25 score normalization to 0-1 range
- [x] Apply text_weight configuration
- [ ] Implement weighted score fusion (waiting for vector search)
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

1. **Summarize Skill**: 高实用性, 企业内部场景
   - Time: 1-2 days
   - Benefit: 快速总结文档、对话、代码

2. **SQLite only (no vectors)**: Store memory chunks in SQLite, skip vector search
   - Time: 2-3 days
   - Benefit: Fast lookups, structured storage

3. **BM25 only**: Keyword search without vectors
   - Time: 1 day
   - Benefit: Exact match for IDs, env vars, code symbols
