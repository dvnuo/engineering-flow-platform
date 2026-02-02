# OpenClaw vs CodeW Feature Comparison & Roadmap

## Overview

| Aspect | OpenClaw (Original) | CodeW (Our Implementation) |
|--------|---------------------|---------------------------|
| **Language** | TypeScript/Node.js | Python |
| **License** | MIT | MIT |
| **Architecture** | Gateway + Pi Agent | Simple monolithic |
| **Status** | Production ready | In development |

---

## Feature Comparison Matrix

### Core Platform

| Feature | OpenClaw | CodeW | Status |
|---------|----------|-------|--------|
| Gateway WS Control Plane | ✅ | ⚠️ HTTP only | TODO |
| Sessions Management | ✅ | ✅ Basic | Complete |
| Presence & Typing | ✅ | ❌ | TODO |
| Configuration System | ✅ JSON | ✅ YAML | Complete |
| Multi-agent Routing | ✅ | ❌ | TODO |
| Session Pruning | ✅ | ❌ | TODO |
| Retry Policy | ✅ | ❌ | TODO |
| Streaming/Chunks | ✅ | ❌ | TODO |

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
| WebChat | ✅ | ❌ | TODO |

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
| Tool Calling | ✅ Claude-native | ⚠️ OpenAI API | Complete |
| ReAct Pattern | ✅ pi-agent-core | ✅ Custom | Complete |
| System Prompt | ✅ Dynamic | ✅ Template | Complete |
| Memory/Context | ✅ Compaction | ⚠️ Basic | Partial |
| Multi-turn | ✅ | ✅ | Complete |
| Thinking Levels | ✅ | ❌ | TODO |

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
| Web UI | ✅ Control UI | ❌ | TODO |

---

## Roadmap (Priority Order)

### Phase 1: Core Stability (Current)
- [x] Discord Channel
- [x] LLM Integration (OpenAI)
- [x] Basic Tools (exec, read, write, edit)
- [x] Tool Calling (ReAct Pattern)
- [x] Config Hot Reload
- [ ] **TODO**: Add unit tests for all modules
- [ ] **TODO**: Add integration tests

### Phase 2: Channel Expansion
- [ ] Add WhatsApp channel
- [ ] Add Telegram channel  
- [ ] Add Slack channel
- [ ] Add WebChat endpoint
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

## Code Structure Comparison

### OpenClaw Structure
```
openclaw/
├── src/
│   ├── agent/          # Pi agent runtime
│   ├── gateway/        # WS control plane
│   ├── channels/      # 15+ messaging channels
│   ├── tools/         # 20+ tools
│   ├── nodes/         # Device nodes
│   └── platforms/     # macOS, iOS, Android
├── docs/              # Full documentation
└── packages/          # npm packages
```

### CodeW Structure
```
codew/
├── main.py            # Entry point
├── config.py         # YAML config loader
├── agent/           # LLM + ReAct agent
├── gateway/         # HTTP server + webhooks
├── channel/         # Discord, Jira
├── skills/          # Skills executor
├── session/         # Session manager
└── tests/           # Unit tests
```

---

## Key Differences

### 1. Runtime Environment
- **OpenClaw**: Node.js ≥22, TypeScript
- **CodeW**: Python 3.9+, asyncio

### 2. Agent Runtime
- **OpenClaw**: Uses [pi-agent-core](https://github.com/peter-steiner/pi-agent) with streaming
- **CodeW**: Custom simple implementation

### 3. Communication
- **OpenClaw**: WebSocket gateway (ws://127.0.0.1:18789)
- **CodeW**: HTTP server (http://0.0.0.0:8000) + Discord Bot API

### 4. Configuration
- **OpenClaw**: JSON format, JSON5 support
- **CodeW**: YAML format

### 5. Tool Calling
- **OpenClaw**: Claude-optimized, native tool support
- **CodeW**: OpenAI Function Calling API

---

## Implementation Priority

Based on feature comparison, here's what's most impactful:

### High Priority
1. ✅ Add more channels (WhatsApp, Telegram, Slack)
2. ✅ Add browser control tool
3. ✅ Add proper session compaction
4. ✅ Add logging improvements

### Medium Priority
1. Add cron/wakeup support
2. Implement sessions_* multi-agent tools
3. Add sandbox mode for security
4. Implement WebChat UI

### Low Priority
1. Voice Wake + Talk Mode
2. iOS/Android nodes
3. macOS companion app
4. Tailscale integration

---

## References

- OpenClaw Docs: https://docs.openclaw.ai
- OpenClaw GitHub: https://github.com/openclaw/openclaw
- OpenClaw Discord: https://discord.gg/clawd

---

*Last updated: 2026-02-02*
*Generated for CodeW project roadmap planning*
