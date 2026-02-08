# Engineering Flow Platform - Skills/Tools 结构分析

## 当前结构问题

### 📁 重复的目录

| 位置 | 用途 | 问题 |
|------|------|------|
| `src/tools/` | 工具函数 | 低层实现 |
| `skills/` | Skill 封装 | 应该用工具 |
| `tools/` | 跨领域工具 | SubAgent, Integration |
| `src/integrations/` | 集成代码 | 与 skills/git 重复 |

### 🔄 混乱的 Git 相关文件

```
Git 相关文件：
├── skills/git/
│   ├── skill.py           # Git Skill (向后兼容)
│   └── tools.py           # Git 工具 (与 src/tools/git.py 重复)
│
├── src/tools/
│   ├── git.py             # Git 工具 (调用 src.integrations.git)
│   ├── github.py           # GitHub 工具
│   ├── jira.py            # Jira 工具
│   └── confluence.py       # Confluence 工具
│
└── src/integrations/
    └── git/
        └── api.py         # Git 客户端实现
```

### ❌ 问题清单

1. **src/tools/git.py vs skills/git/tools.py**
   - 两个文件做类似的事
   - `src/tools/git.py` 调用 `src.integrations.git`
   - `skills/git/tools.py` 是独立实现

2. **skills/git/skill.py vs skills/git/tools.py**
   - `skill.py` 使用 `@skill` 装饰器
   - `tools.py` 是独立的工具函数
   - 职责不清

3. **src/integrations/ vs src/tools/**
   - `integrations/` 包含实现
   - `tools/` 包含包装器
   - 边界模糊

4. **tools/ vs skills/**
   - `tools/` 有 SubAgent, Integration
   - `skills/` 有 executor, git, github
   - 应该有明确分工

## 建议的清晰结构

### 方案 A: 按职责分层

```
engineering-flow/
├── skills/                  # 🎯 Agent 可调用的技能
│   ├── __init__.py         # Skill 注册
│   ├── decorator.py        # @skill 装饰器
│   ├── coding_agent/       # 编码代理
│   ├── git/                # Git 相关 Skill
│   ├── github/             # GitHub API Skill
│   ├── summarize/          # 文本摘要
│   └── ...
│
├── tools/                  # 🔧 底层工具函数
│   ├── __init__.py
│   ├── git.py              # Git 操作
│   ├── github.py           # GitHub API
│   ├── jira.py             # Jira API
│   ├── confluence.py       # Confluence API
│   ├── exec.py             # Shell 执行
│   └── process.py          # 进程管理
│
└── integrations/           # 🔌 外部服务集成
    ├── git/
    │   └── api.py          # Git 客户端
    ├── github/
    │   └── api.py          # GitHub API 客户端
    ├── jira/
    │   └── api.py          # Jira API 客户端
    └── ...
```

### 方案 B: 扁平结构

```
engineering-flow/
├── skills/                  # 所有 Skill
│   ├── coding_agent/
│   ├── git/
│   ├── github/
│   └── ...
│
├── tools/                  # 所有 Tool
│   ├── git.py
│   ├── github.py
│   ├── jira.py
│   └── ...
│
└── src/                    # 内部实现
    └── integrations/       # 第三方库集成
```

## 推荐的清理步骤

### Step 1: 统一 tools/ 位置

```bash
# 合并 src/tools/* 到 tools/
mv src/tools/*.py tools/
rm -rf src/tools

# 合并 skills/*/tools.py 到 tools/
mv skills/git/tools.py tools/git_skill_api.py
rm -rf skills/*/tools.py
```

### Step 2: 简化 skills/ 结构

```
skills/
├── __init__.py         # 导入所有 skills
├── decorator.py        # @skill 装饰器
├── coding_agent/       # 保持
├── git/               # 只保留 skill.py
├── github/            # 只保留 skill.py
├── summarize/
└── ...
```

### Step 3: 明确依赖关系

```
Agent → Skills (@skill 装饰) → Tools (纯函数) → Integrations (第三方库)
```

## 当前代码示例对比

### ❌ 错误：职责不清

```python
# skills/git/skill.py
from skills.executor import SkillResult, skill
from src.integrations.git import GitClient  # 混用

@skill
async def git(command: str) -> SkillResult:
    ...
```

```python
# skills/git/tools.py  (独立实现，无 @skill)
class GitTools:
    def status(self): ...
    def commit(self): ...
```

### ✅ 正确：清晰分层

```python
# tools/git.py (纯工具函数)
async def git_status(workspace: str) -> str:
    return await git_client.status(workspace)

async def git_commit(message: str, workspace: str) -> str:
    return await git_client.commit(message, workspace)
```

```python
# skills/git/skill.py (只包装工具)
from tools.git import git_status, git_commit
from skills.decorator import skill, SkillResult

@skill
async def git(operation: str, **kwargs) -> SkillResult:
    tool_func = {
        "status": git_status,
        "commit": git_commit,
    }.get(operation)
    
    if tool_func:
        result = await tool_func(**kwargs)
        return SkillResult(success=True, output=result)
    return SkillResult(success=False, error=f"Unknown op: {operation}")
```

## OpenClaw 对比 (待补充)

需要先完整克隆 openclaw-main 才能对比。

## 行动项

- [ ] 决定采用方案 A 或 B
- [ ] 合并 tools/ 位置
- [ ] 清理 skills/*/tools.py
- [ ] 更新导入路径
- [ ] 运行测试验证
