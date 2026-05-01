# Engineering Flow Platform 重构指引 - 学习现代 Agent 框架架构

> **Note（历史文档）**：本文件主要是历史性重构草图/设计记录。当前已落地实现请以 `src/git/api.py`、`src/github/api.py`、`config.yaml.example` 为准：Git transport 已收口为 HTTPS + `github.api_token`（askpass），runtime 不依赖 `gh` CLI auth，也不依赖 SSH setup。

> **Note**: This is a historical refactor guide. It predates the external engineering-flow-platform-skills repository. Runtime skill metadata now uses lowercase skill.md, and business skill assets are loaded from /app/skills or EFP_SKILLS_DIR.

## 现状分析

### 当前问题

```
当前结构：
├── channel/          # 已有 GitHub, Jira, Confluence 实现
├── skills/           # 已有 GitHub, Git, Executor 实现  
├── tools/            # 已有 integration.py (Jira + Confluence 工具)
└── cron/             # mention_poller.py (GitHub, Jira, Confluence)

问题：
1. GitHub: channel/github.py + skills/github/skill.py 重复
2. Git: skills/git/skill.py + 未复用的 Git 工具
3. Jira: channel/jira.py + tools/integration.py 重复
4. Confluence: channel/confluence.py + tools/integration.py 重复

说明（当前设计收口）：
- Git transport 已统一到 `src/git/api.py`，使用 HTTPS + `github.api_token`（askpass）。
- `src/github/api.py` 是 GitHub runtime 主路径。
```

---

## 目标架构

```
engineering-flow-platform/
├── src/                           # 核心源码 (参考业界最佳实践)
│   ├── channels/                  # Channel 适配器 (复用)
│   │   ├── github.py              # GitHub REST API
│   │   ├── jira.py                # Jira REST API
│   │   ├── confluence.py          # Confluence REST API
│   │   └── __init__.py           # Channel 工厂
│   │
│   ├── integrations/              # 集成实现 (新增，核心逻辑)
│   │   ├── __init__.py
│   │   ├── github/               # GitHub 集成
│   │   │   ├── __init__.py
│   │   │   ├── api.py            # GitHub REST API 实现
│   │   │   └── types.py          # 类型定义
│   │   │
│   │   ├── git/                  # Git 集成
│   │   │   ├── __init__.py
│   │   │   ├── api.py           # Git 命令封装
│   │   │
│   │   ├── jira/                 # Jira 集成
│   │   │   ├── __init__.py
│   │   │   ├── api.py           # Jira REST API 实现
│   │   │   └── types.py         # 类型定义
│   │   │
│   │   └── confluence/           # Confluence 集成
│   │       ├── __init__.py
│   │       ├── api.py           # Confluence REST API
│   │       └── types.py         # 类型定义
│   │
│   ├── tools/                    # 工具函数 (Agent 调用)
│   │   ├── __init__.py           # 工具注册
│   │   ├── github.py             # GitHub 工具 (调用 integrations/github/api.py)
│   │   ├── jira.py               # Jira 工具 (调用 integrations/jira/api.py)
│   │   ├── confluence.py         # Confluence 工具
│   │   └── git.py               # Git 工具
│   │
│   ├── skills/                   # Skills (Agent Skills)
│   │   ├── __init__.py
│   │   ├── git/                  # Git Skill
│   │   │   ├── SKILL.md
│   │   │   └── skill.py          # 调用 src/integrations/git/api.py
│   │   │
│   │   ├── github/               # GitHub Skill
│   │   │   ├── SKILL.md
│   │   │   └── skill.py          # 调用 src/integrations/github/*
│   │   │
│   │   └── cron/                 # Cron Skill
│   │       ├── SKILL.md
│   │       └── skill.py
│   │
│   ├── agents/                   # Agent 核心
│   │   ├── core.py
│   │   ├── llm.py
│   │   ├── thinking.py
│   │   └── memory.py
│   │
│   └── config.py                  # 配置
│
├── channel/                      # Channel 入口 (轻量，调用 src/integrations/*)
│   ├── github.py                # 导入 src/integrations/github/api.py
│   ├── jira.py                  # 导入 src/integrations/jira/api.py
│   ├── confluence.py            # 导入 src/integrations/confluence/api.py
│   └── __init__.py
│
├── skills/                       # Skills 入口 (轻量，调用 src/skills/*)
│   ├── git/
│   │   ├── SKILL.md
│   │   └── skill.py            # 导入 src/skills/git/skill.py
│   ├── github/
│   │   ├── SKILL.md
│   │   └── skill.py            # 导入 src/skills/github/skill.py
│   └── __init__.py
│
├── tools/                        # 工具入口 (轻量，调用 src/tools/*)
│   ├── integration.py            # 导入 src/tools/*.py
│   └── __init__.py
│
└── cron/                         # 定时任务 (调用 src/integrations/*)
    └── mention_poller.py
```

---

## 重构步骤

### Phase 1: 创建目录结构

```bash
# 创建核心目录
mkdir -p src/integrations/{github,git,jira,confluence}
mkdir -p src/tools
mkdir -p src/skills/{git,github}

# 迁移文件
mv channel/github.py src/integrations/github/api.py
mv channel/jira.py src/integrations/jira/api.py
mv channel/confluence.py src/integrations/confluence/api.py
```

### Phase 2: 重构 GitHub 示例

#### 2.1 创建 `src/integrations/github/__init__.py`

```python
"""GitHub Integration - Single source of truth for GitHub operations."""

from .api import GitHubClient
__all__ = ["GitHubClient"]
```

#### 2.2 重构 `src/integrations/github/api.py`

```python
"""
GitHub REST API Client - Single implementation.

所有 GitHub REST API 调用都通过这里。
被 channel/github.py, tools/github.py, skills/github/ 调用。
"""

import httpx
from config import config

class GitHubClient:
    """GitHub REST API client with rate limiting and retry."""
    
    def __init__(self, base_url: str = None, token: str = None):
        self.base_url = base_url or config.get("github.base_url", "https://api.github.com")
        self.token = token or config.get("github.api_token", "")
        self.enabled = config.get("github.enabled", False)
        
        self.client = httpx.AsyncClient(timeout=30.0)
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {self.token}",
        }
    
    async def get_issue(self, owner: str, repo: str, issue_number: int) -> dict:
        """Get issue or PR details."""
        return await self._request(
            "GET", 
            f"/repos/{owner}/{repo}/issues/{issue_number}"
        )
    
    async def search_issues(self, query: str, max_results: int = 10) -> dict:
        """Search issues and PRs."""
        return await self._request(
            "GET",
            "/search/issues",
            params={"q": query, "per_page": max_results}
        )
    
    async def add_comment(
        self, 
        owner: str, 
        repo: str, 
        issue_number: int, 
        body: str
    ) -> dict:
        """Add comment to issue/PR."""
        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body}
        )
    
    # ... 其他方法
```

#### 2.3 创建 `src/tools/github.py`

```python
"""GitHub Tools - Agent 调用入口。

调用 src/github/api.py（REST 主路径）
"""

from src.github import GitHubClient

# 全局实例
github_client = GitHubClient()

# ========== 工具函数 (OpenAI Functions Schema) ==========

async def github_get_issue(owner: str, repo: str, issue_number: int) -> str:
    """Get GitHub issue or PR details."""
    try:
        issue = await github_client.get_issue(owner, repo, issue_number)
        # ... 格式化输出
        return formatted
    except Exception as e:
        return f"Error: {e}"

def get_tools_schemas() -> list:
    """返回 GitHub 工具的 OpenAI Schema。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "github_get_issue",
                "description": "Get GitHub issue or PR details",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "issue_number": {"type": "integer", "description": "Issue or PR number"}
                    },
                    "required": ["owner", "repo", "issue_number"]
                }
            }
        },
        # ... 其他工具
    ]
```

#### 2.5 更新 `channel/github.py` (保持 API 兼容)

```python
"""GitHub Channel - 保持向后兼容。

导入 src/integrations/github/api.py 实现。
"""

from src.integrations.github.api import GitHubClient

# 保持原有 API
github_channel = GitHubClient()

# 原有函数别名
async def github_get_issue(owner: str, repo: str, issue_number: int):
    return await github_channel.get_issue(owner, repo, issue_number)
```

---

## 复用关系图

```
                    ┌─────────────────────┐
                    │  src/integrations/  │  ← 核心实现 (单一数据源)
                    │     github/         │
                    │     jira/          │
                    │     git/           │
                    │     confluence/     │
                    └─────────┬───────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  channel/   │ │   tools/    │ │   skills/   │
    │ github.py    │ │ github.py   │ │ github/     │
    │ jira.py      │ │ jira.py     │ │ skill.py    │
    │ confluence.py│ │ confluence.py│ │ cron/       │
    └─────────────┘ └─────────────┘ └─────────────┘
              │               │               │
              └───────────────┴───────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │      cron/          │
                    │  mention_poller.py  │
                    └─────────────────────┘
```

---

## 文件迁移清单

| 源文件 | 目标文件 | 说明 |
|--------|----------|------|
| `channel/github.py` | `src/integrations/github/api.py` | REST API 实现 |
| `channel/jira.py` | `src/integrations/jira/api.py` | REST API 实现 |
| `channel/confluence.py` | `src/integrations/confluence/api.py` | REST API 实现 |
| `skills/github/skill.py` | `src/github/api.py` | GitHub REST API 工作流 |
| `skills/git/skill.py` | `src/integrations/git/api.py` | Git 命令封装 |
| `tools/integration.py` | `src/tools/*.py` | 拆分为独立文件 |
| `skills/git/tools.py` | `src/git/api.py` | Git transport（HTTPS + github.api_token） |

---

## 关键原则

1. **单一数据源**: 每个集成 (GitHub, Jira, etc.) 只有一份实现
2. **复用优先**: channel, tools, skills 都调用 src/integrations/*
3. **向后兼容**: 保持原有导入路径，添加重导出
4. **类型安全**: 添加 Type Hints
5. **测试覆盖**: 每个集成有对应的测试文件

---

## 验证步骤

```bash
# 1. 运行现有测试
pytest tests/ -v

# 2. 验证导入路径
python -c "from channel.github import github_channel; print('OK')"
python -c "from skills.github.skill import github; print('OK')"
python -c "from tools.integration import JIRA_TOOLS; print('OK')"

# 3. 运行 Agent
python main.py --test
```

---

## 预期收益

| 指标 | 重构前 | 重构后 |
|------|--------|--------|
| GitHub 代码重复 | 2 处 | 0 处 |
| Jira 代码重复 | 2 处 | 0 处 |
| 维护成本 | 高 | 低 |
| 新增集成 | 需改 3 处 | 只需改 1 处 |

---

## TODO 清单

- [ ] Phase 1: 创建 src/integrations/ 目录结构
- [ ] Phase 2: 重构 GitHub (api.py)
- [ ] Phase 3: 重构 Jira (api.py)
- [ ] Phase 4: 重构 Confluence (api.py)
- [ ] Phase 5: 重构 Git (api.py, HTTPS + github.api_token)
- [ ] Phase 6: 更新 tools/integration.py
- [ ] Phase 7: 更新 channel/* 保持兼容
- [ ] Phase 8: 更新 skills/* 保持兼容
- [ ] Phase 9: 运行测试验证
