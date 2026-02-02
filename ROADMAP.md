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
| Docker Support | ✅ | ❌ | TODO |
| Tailscale | ✅ Serve/Funnel | ❌ | TODO |
| WebChat UI | ✅ | ✅ HTML/CSS/JS | Complete |

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
| Total Files | 30+ |
| Total Lines | 4000+ |
| Tests | 50+ |
| PRs Merged | 5+ |

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

### Phase 2: Channel Expansion (Next)
- [ ] Add WhatsApp channel
- [ ] Add Telegram channel
- [ ] Add Slack channel
- [ ] Add WebChat endpoint (WS support)
- [ ] Improve Jira integration (bi-directional)

### Phase 3: Tool Enhancement
- [ ] Implement browser control
- [ ] Implement canvas/A2UI
- [ ] Add cron support
- [ ] Add sessions_* tools for multi-agent
- [ ] Implement sandbox mode

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

### CodeW Structure (Current)
```
codew/
├── main.py             # Entry point
├── config.py          # YAML config loader
├── agent/             # LLM + ReAct agent
│   ├── core.py        # Agent logic
│   ├── llm.py        # LLM client
│   └── queue.py      # Execution queue
├── gateway/           # HTTP server + webhooks
│   ├── server.py     # Main server
│   ├── webchat.py    # WebChat handler
│   ├── templates/    # HTML templates
│   └── static/       # CSS/JS files
├── channel/           # Message channels
│   ├── discord.py    # Discord bot
│   └── jira.py       # Jira webhook
├── session/           # Session management
│   ├── manager.py    # In-memory sessions
│   ├── persistence.py # JSONL persistence
│   ├── usage.py      # Token tracking
│   └── pruning.py    # Context pruning
├── skills/            # Skills executor
├── tests/             # Unit tests (50+ tests)
└── docs/              # Documentation
```

---

## References

- OpenClaw Docs: https://docs.openclaw.ai
- OpenClaw GitHub: https://github.com/openclaw/openclaw
- OpenClaw Discord: https://discord.gg/clawd

---

*Last updated: 2026-02-02*
*Generated for CodeW project roadmap planning*
