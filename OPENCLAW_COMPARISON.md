# OpenClaw vs Engineering Flow - Skills/Tools 结构对比

## 📊 整体对比

| 特性 | OpenClaw (main) | Engineering Flow |
|------|------------------|------------------|
| **语言** | TypeScript/Node | Python |
| **Skills 数量** | 52 | 11 |
| **实现方式** | CLI + Markdown | Python 函数 + @skill 装饰器 |
| **工具层** | src/tools + extensions | src/tools + tools/ |

## 🏗️ 架构对比

### OpenClaw 结构

```
openclaw-main/
├── skills/                    # 52 个 Skills
│   ├── github/               # 只含 SKILL.md
│   ├── coding-agent/
│   ├── weather/
│   └── ...
│
├── src/
│   ├── tools/                # 工具函数
│   ├── agents/               # Agent 核心
│   ├── providers/            # LLM Provider
│   ├── runtime/              # 运行时
│   ├── gateway/              # 控制平面
│   └── ...
│
└── extensions/               # 扩展 (BlueBubbles 等)
```

### Engineering Flow 结构

```
engineering-flow/
├── skills/                    # Skills
│   ├── __init__.py           # 注册
│   ├── decorator.py           # @skill 装饰器
│   ├── github/
│   │   ├── SKILL.md
│   │   └── skill.py          # Python 实现 ⬅️ OpenClaw 没有
│   ├── git/
│   │   ├── SKILL.md
│   │   └── skill.py
│   └── ...
│
├── tools/                     # 跨领域工具
│   ├── subagent.py
│   ├── integration.py
│   └── subagent_schemas.py
│
└── src/
    ├── tools/                # 工具函数 (重复!)
    │   ├── git.py
    │   ├── github.py
    │   ├── jira.py
    │   └── confluence.py
    │
    └── integrations/          # 集成实现
        └── git/
            └── api.py
```

## 🔑 核心差异

### 1. Skill 定义方式

**OpenClaw**: 纯声明式
```yaml
# skills/github/SKILL.md
---
name: github
description: "Interact with GitHub using the `gh` CLI."
metadata:
  {
    "openclaw": {
      "emoji": "🐙",
      "requires": { "bins": ["gh"] }
    }
  }
---

# GitHub Skill
Use `gh` CLI to interact with GitHub...
```

**Engineering Flow**: 声明式 + Python 实现
```python
# skills/github/skill.py
from skills.decorator import skill, SkillResult

@skill
async def github(command: str = "repo list", args: str = "", hostname=None) -> SkillResult:
    """Execute GitHub CLI commands."""
    result = await shell(f"gh {command} {args}")
    return SkillResult(success=True, output=result)
```

```yaml
# skills/github/SKILL.md
---
name: github
description: "Interact with GitHub using the `gh` CLI."
---

# GitHub Skill
...
```

### 2. 工具调用

**OpenClaw**: 直接 CLI 调用
```typescript
// Agent 中直接调用
const result = await this.tools.exec({
  command: `gh pr list --repo ${repo}`,
  shell: true
});
```

**Engineering Flow**: 工具函数 → Skill 装饰器
```python
# tools/github.py (工具层)
async def github_cli(command: str, args: str = "") -> str:
    return await exec(f"gh {command} {args}")

# skills/github/skill.py (Skill 层)
from tools.github import github_cli
from skills.decorator import skill

@skill
async def github(command: str, args: str = "") -> SkillResult:
    output = await github_cli(command, args)
    return SkillResult(success=True, output=output)
```

### 3. 重复问题对比

**OpenClaw**: ✅ 清晰
```
src/tools/     → 通用工具
skills/        → 声明式配置
extensions/    → 独立插件
```

**Engineering Flow**: ❌ 混乱
```
src/tools/          → 工具函数
tools/              → SubAgent, Integration (与 src/tools 重复?)
skills/*/skill.py   → 调用 src.integrations.*
skills/*/tools.py   → 独立实现 (与 src/tools 重复!)
src/integrations/   → 实际实现
```

## 📁 文件位置对比

| 功能 | OpenClaw | Engineering Flow |
|------|----------|------------------|
| Git 操作 | `skills/github/SKILL.md` | `skills/github/skill.py` + `src/tools/git.py` |
| GitHub API | `skills/github/SKILL.md` | `skills/github/skill.py` + `src/tools/github.py` |
| Git 实现 | `src/tools/exec.ts` | `src/integrations/git/api.py` + `src/tools/git.py` |
| Shell 执行 | `src/tools/exec.ts` | `tools/exec.py` + `src/tools/` |
| SubAgent | `src/agents/subagent-*.js` | `tools/subagent.py` |
| Skill 注册 | 自动扫描 | `skills/__init__.py` |

## ✅ Engineering Flow 问题清单

### 问题 1: tools/ vs src/tools/

```
tools/:
├── integration.py
├── subagent.py
└── subagent_schemas.py

src/tools/:
├── git.py         ← 与 skills/git/tools.py 重复
├── github.py
├── jira.py
└── confluence.py
```

**建议**: 合并到单一 `tools/` 目录

### 问题 2: skills/*/tools.py 重复

```
skills/git/tools.py        ← 独立实现
src/tools/git.py           ← 调用 src.integrations
src/integrations/git/api.py ← 实际实现
```

**建议**: 只保留 `tools/git.py`，skills/ 只放 skill.py

### 问题 3: 职责不清

| 文件 | 应该做什么 | 实际做什么 |
|------|----------|----------|
| skills/git/skill.py | 调用 tools/git | 直接调用 src.integrations |
| tools/git.py | 工具函数 | 调用 src.integrations |
| src/integrations/git/api.py | 实际实现 | ✅ 正确 |

## 🎯 建议的工程化结构

```
engineering-flow/
├── skills/                  # Agent 可调用层
│   ├── __init__.py         # 自动注册 @skill 装饰的函数
│   ├── decorator.py         # @skill 装饰器
│   ├── coding_agent/       # 编码代理
│   ├── github/             # GitHub CLI 封装
│   │   ├── SKILL.md
│   │   └── skill.py        # 只调用 tools.github_*
│   ├── git/
│   │   ├── SKILL.md
│   │   └── skill.py        # 只调用 tools.git_*
│   └── ...
│
├── tools/                   # 工具函数层 (纯函数)
│   ├── __init__.py         # 导出所有工具
│   ├── git.py              # git_status, git_commit 等
│   ├── github.py           # gh CLI 封装
│   ├── jira.py             # Jira API
│   ├── confluence.py        # Confluence API
│   ├── exec.py             # Shell 执行
│   └── process.py          # 进程管理
│
├── integrations/            # 第三方库集成
│   ├── git/
│   │   └── api.py          # GitPython 等封装
│   ├── github/
│   │   └── api.py          # PyGithub 等封装
│   └── ...
│
└── src/                    # 内部实现细节
    └── ...                 # 其他内部模块
```

## 📝 迁移步骤

### Step 1: 合并 tools/
```bash
# 移动 src/tools/* 到 tools/
mv src/tools/*.py tools/
rm -rf src/tools

# 删除 skills/*/tools.py (用 tools/ 替代)
rm skills/*/tools.py 2>/dev/null
```

### Step 2: 简化 skills/
```python
# skills/git/skill.py
from tools.git import git_status, git_commit
from skills.decorator import skill, SkillResult

@skill
async def git(operation: str, **kwargs) -> SkillResult:
    operations = {
        "status": git_status,
        "commit": git_commit,
    }
    if op := operations.get(operation):
        result = await op(**kwargs)
        return SkillResult(success=True, output=result)
    return SkillResult(success=False, error=f"Unknown: {operation}")
```

### Step 3: 更新导入
```bash
# 批量更新 skills/*/skill.py 的导入
# 从: from src.integrations.git import ...
# 到: from tools.git import ...
```

## 🔍 OpenClaw 可借鉴之处

1. **纯声明式 Skills**: SKILL.md + YAML metadata，无需 Python 代码
2. **CLI First**: 通过 CLI 工具实现功能，保持简单
3. **插件扩展**: extensions/ 目录支持独立插件
4. **类型安全**: TypeScript 静态类型检查

## 📚 参考链接

- OpenClaw Skills: `/root/.openclaw/workspace/openclaw-main/skills/`
- OpenClaw Source: `/root/.openclaw/workspace/openclaw-main/src/`
- Engineering Flow: `/root/.openclaw/workspace/engineering-flow/`
