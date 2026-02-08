# 重构目录结构 - 统一 OpenClaw 架构风格

## 📋 概述

本 Issue 旨在重构 `engineering-flow` 项目的目录结构，使其与 [OpenClaw](https://github.com/openclaw/openclaw) 架构保持一致，提升代码可维护性和项目清晰度。

## 🎯 重构目标

### 1. 统一 Skills 结构
- **目标**: Skill 目录只包含 `SKILL.md` 声明文件
- **原则**: 实现代码全部移至 `src/` 目录

### 2. 消除代码重复
- **问题**: `src/tools/` 与 `skills/*/tools.py` 功能重复
- **解决**: 合并到统一的 `src/tools/` 目录

### 3. 简化目录层级
- **目标**: 删除 `tools/` 第一层目录
- **原则**: 所有实现代码统一放在 `src/`

### 4. 明确职责划分
- `skills/` - 声明式配置
- `src/` - 所有实现代码
- 取消 `tools/`, `integrations/` 等平行目录

## 📁 当前问题分析

### 问题 1: Skills 目录混乱

```
当前结构:
├── skills/
│   ├── git/
│   │   ├── SKILL.md         ✅ 声明
│   │   ├── skill.py         ❌ 不应有代码
│   │   └── tools.py         ❌ 重复实现
│   └── ...
│
├── tools/                    ❌ 不应有独立目录
│   ├── subagent.py
│   └── integration.py
│
└── src/
    ├── tools/               ❌ 与 skills/*/tools.py 重复
    │   ├── git.py
    │   ├── github.py
    │   └── ...
    │
    └── integrations/        ❌ 职责不清
        └── git/
            └── api.py
```

### 问题 2: 代码重复

| 功能 | 文件位置 | 问题 |
|------|----------|------|
| Git 操作 | `skills/git/tools.py` | 独立实现 |
| Git 操作 | `src/tools/git.py` | 重复实现 |
| Git 操作 | `src/integrations/git/api.py` | 实际实现 |

### 问题 3: 职责不清

- `tools/` vs `src/tools/` - 边界模糊
- `skills/*/skill.py` - 应只放声明
- `integrations/` - 应合并到 `src/`

## ✅ 目标结构

```
engineering-flow/
├── skills/                   # 🎯 声明式 Skill
│   ├── __init__.py          # Skill 注册
│   ├── decorator.py         # @skill 装饰器
│   ├── coding_agent/
│   │   └── SKILL.md        # ✅ 只有声明
│   ├── git/
│   │   └── SKILL.md        # ✅ 只有声明
│   ├── github/
│   │   └── SKILL.md        # ✅ 只有声明
│   └── ...
│
├── src/                     # 🔧 所有实现代码
│   ├── __init__.py
│   ├── agents/             # Agent 运行时
│   ├── tools/             # ✅ 统一的工具目录
│   │   ├── __init__.py
│   │   ├── git.py
│   │   ├── github.py
│   │   ├── jira.py
│   │   ├── confluence.py
│   │   ├── exec.py
│   │   └── process.py
│   ├── providers/         # LLM Provider
│   ├── runtime/          # 运行时
│   ├── gateway/           # 控制平面
│   ├── channels/         # 消息通道
│   ├── cron/             # 定时任务
│   ├── session/          # 会话管理
│   ├── memory/           # 记忆存储
│   └── config.py         # 配置
│
├── tests/                   # 测试
├── config.yaml              # 配置文件
├── requirements.txt         # 依赖
└── README.md
```

## 🔄 迁移步骤

### Phase 1: 准备
- [ ] 创建备份分支 `refactor/structure-v2`
- [ ] 确认所有测试通过
- [ ] 文档化当前导入关系

### Phase 2: 合并 Tools
```bash
mv src/tools/*.py tools/
rm -rf src/tools
```

### Phase 3: 清理 Skills
```bash
rm skills/*/skill.py 2>/dev/null
rm skills/*/tools.py 2>/dev/null
```

### Phase 4: 重构 src/
```bash
mkdir -p src/tools
mv tools/*.py src/tools/
rm -rf tools
```

### Phase 5: 验证
- [ ] 运行所有测试
- [ ] 验证导入路径
- [ ] 更新文档

## 📝 详细迁移清单

### Skills 清理
| Skill | 当前文件 | 目标 |
|-------|---------|------|
| coding_agent | `skill.py` | 删除 |
| cron | `skill.py` | 删除 |
| git | `skill.py`, `tools.py` | 删除 |
| github | `skill.py` | 删除 |
| summarize | `skill.py` | 删除 |
| test_case_generator | `skill.py` | 删除 |

### 工具合并
| 当前 | 目标 |
|-----|------|
| `src/tools/*.py` | `src/tools/*.py` |
| `tools/subagent.py` | `src/agents/subagent.py` |
| `tools/integration.py` | `src/integration.py` |

## ✅ 验收标准

1. [ ] `skills/` 只包含 `SKILL.md`
2. [ ] 删除 `tools/` 目录
3. [ ] 删除 `src/integrations/` 目录
4. [ ] 所有工具在 `src/tools/`
5. [ ] 所有测试通过
6. [ ] 文档已更新

## 📅 时间线

- **Phase 1-5**: ~5 个工作日
