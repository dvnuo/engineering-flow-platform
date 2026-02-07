# Issue #91: Tools OpenClaw Structure Implementation

**URL**: https://github.com/opsclaw/opsclaw/issues/91  
**Branch**: `feature/tools-openclaw-structure`  
**Status**: 🚧 In Progress

---

## Progress Update (2026-02-07)

### ✅ Implemented Tools

| Tool | SKILL.md | tool.py | Status |
|------|----------|---------|--------|
| canvas | ✅ | ⏳ | Documentation only |
| nodes | ✅ | ✅ | Basic implementation |
| sessions | ✅ | ✅ | Basic implementation |
| cron | ✅ | ✅ | Basic implementation |
| browser | ✅ | ✅ | Basic implementation |
| message | ✅ | ✅ | Basic implementation |
| web | ✅ | ✅ | Basic implementation |
| image | ✅ | ✅ | Basic implementation |
| tts | ✅ | ✅ | Basic implementation |
| gateway | ✅ | ✅ | Basic implementation |
| memory | ✅ | ✅ | Basic implementation |

### ⏳ Pending Tools

| Tool | Status |
|------|--------|
| subagent | Refactor existing |
| integration | Refactor existing |
| canvas | Full implementation |

---

## Files Created

```
tools/
├── __init__.py                    # Tool exports
├── IMPLEMENTATION.md              # This file
├── canvas/
│   └── SKILL.md                  # Canvas documentation
├── nodes/
│   ├── SKILL.md                  # Nodes documentation
│   └── tool.py                   # Nodes implementation
├── sessions/
│   ├── SKILL.md                  # Sessions documentation
│   └── tool.py                   # Sessions implementation
├── cron/
│   ├── SKILL.md                  # Cron documentation
│   └── tool.py                   # Cron implementation
├── browser/
│   ├── SKILL.md                  # Browser documentation
│   └── tool.py                   # Browser implementation
├── message/
│   ├── SKILL.md                  # Message documentation
│   └── tool.py                   # Message implementation
├── web/
│   ├── SKILL.md                  # Web documentation
│   └── tool.py                   # Web implementation
├── image/
│   ├── SKILL.md                  # Image documentation
│   └── tool.py                   # Image implementation
├── tts/
│   ├── SKILL.md                  # TTS documentation
│   └── tool.py                   # TTS implementation
├── gateway/
│   ├── SKILL.md                  # Gateway documentation
│   └── tool.py                   # Gateway implementation
├── memory/
│   ├── SKILL.md                  # Memory documentation
│   └── tool.py                   # Memory implementation
├── subagent.py                   # Existing (refactor needed)
├── integration.py                # Existing (refactor needed)
└── subagent_schemas.py           # Existing (keep)
```

---

## OpenClaw Expected Structure

```
src/agents/tools/
├── canvas-tool.ts           # A2UI Canvas control
├── nodes-tool.ts           # 远程节点控制
├── sessions-spawn-tool.ts  # 子代理生成
├── cron-tool.ts            # 定时任务
├── browser-tool.ts         # 浏览器控制
├── message-tool.ts         # 消息发送
├── web-search.ts           # 网络搜索
├── web-fetch.ts            # 网页抓取
└── ... 40+ 工具
```

### Tool Patterns (OpenClaw)

Each tool follows this pattern:
1. **SKILL.md** with YAML frontmatter
2. **Python module** with tool functions
3. **Schema definition** for tool arguments
4. **Integration** in tools registry

---

## Current OpsClaw Tools Status

### ✅ Existing Tools (Need Refactor)

| Tool | Files | YAML Frontmatter | Status |
|------|-------|-----------------|--------|
| `subagent` | subagent.py, subagent_schemas.py | ❌ | 需改造 |
| `integration` | integration.py | ❌ | 需改造 |

### ✅ New Tools Created

| Category | Tools Created |
|----------|--------------|
| Canvas | canvas (docs only) |
| Nodes | nodes (full) |
| Sessions | sessions (full) |
| Cron | cron (full) |
| Browser | browser (full) |
| Message | message (full) |
| Web | web (full) |
| Image | image (full) |
| TTS | tts (full) |
| Gateway | gateway (full) |
| Memory | memory (full) |

---

## Implementation Plan

### Phase 1: Core Tools Structure ✅
1. ✅ Create tools/canvas/ - Canvas control tool
2. ✅ Create tools/nodes/ - Remote node control
3. ✅ Create tools/browser/ - Browser control
4. ✅ Create tools/message/ - Message sending

### Phase 2: Session & Cron Tools ✅
1. ✅ Create tools/sessions/ - Session management
2. ✅ Create tools/cron/ - Cron job management

### Phase 3: Web & Utility Tools ✅
1. ✅ Create tools/web/ - Web search and fetch
2. ✅ Create tools/memory/ - Memory tools
3. ✅ Create tools/gateway/ - Gateway control
4. ✅ Create tools/image/ - Image analysis
5. ✅ Create tools/tts/ - Text to speech

### Phase 4: Integration (Next)
1. ⏳ Update tools/__init__.py - Tool registry
2. ⏳ Refactor subagent.py - Use new structure
3. ⏳ Update integration.py - Tool loader
4. ⏳ Create tests - Test suite

---

## Next Steps

1. ✅ Create branch `feature/tools-openclaw-structure`
2. ✅ Create SKILL.md for each tool
3. ✅ Implement tool functions
4. ⏳ Refactor existing subagent.py
5. ⏳ Update integration.py
6. ⏳ Create tests
7. ⏳ Submit PR

---

## References

- [OpenClaw Tools Source](https://github.com/openclaw/openclaw/tree/main/src/agents/tools)
- [PR #111 - Skills Structure](https://github.com/opsclaw/opsclaw/pull/111)
- [Issue #90 - Skills Implementation](https://github.com/opsclaw/opsclaw/issues/90)
