# Engineering Flow Platform 重构指引 - 学习现代 Agent 框架架构

> **Note（历史文档）**：本文件主要是历史性重构草图/设计记录。当前已落地实现请以 `src/external_cli/`、`src/git/api.py`、`config.yaml.example` 为准：Jira/Confluence 使用外部 CLI，GitHub 使用 `gh`/`git`。

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
- Jira/Confluence 已统一到外部 `jira`/`confluence` CLI。
- GitHub runtime 写回和读取已统一到 `src/external_cli/github.py`，由 `gh`/`git` 执行。
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

#### 2.3 当前替代路径

上面的历史草图已经被 runtime-v2 替代。当前分层不再保留 Python 侧
Jira/GitHub/Confluence API client 或 channel 兼容层：

- Jira/Confluence 运行时行为通过 `src/external_cli/jira.py` 和
  `src/external_cli/confluence.py` 调用外部 CLI。
- GitHub 运行时读写通过 `src/external_cli/github.py` 调用 `gh`，Git
  操作继续使用 `git`。
- Runtime profile 应用时由 `src/external_cli/profile_config.py` 投影到
  Atlassian CLI 配置、`gh` hosts 配置和 Git 用户配置。

---

## 当前验证步骤

```bash
python3.11 -m pytest -q
git diff --check
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
