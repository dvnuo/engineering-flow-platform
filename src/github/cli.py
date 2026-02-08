"""
GitHub CLI Wrapper - gh command wrapper.

Provides functionality equivalent to GitHub REST API,
but executes using gh CLI (supports Enterprise).
"""

import asyncio
import shlex
from pathlib import Path
from typing import Optional, Dict, Any

from src.config import config

DEFAULT_HOSTNAME = "github.com"


class GitHubCLI:
    """GitHub CLI wrapper using 'gh' command."""
    
    def __init__(self, hostname: str = None):
        self.hostname = hostname or config.get("github.hostname", DEFAULT_HOSTNAME)
    
    async def run(self, args: list, cwd: str = None) -> tuple:
        """Run gh command, return (success, output)."""
        cmd = ["gh"] + args
        
        if self.hostname != DEFAULT_HOSTNAME:
            cmd = ["--hostname", self.hostname] + cmd
        
        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd or str(Path.home())
        )
        
        stdout, _ = await result.communicate()
        return result.returncode == 0, stdout.decode("utf-8").strip()
    
    async def _parse_table(self, output: str) -> list:
        """Parse tab-separated output into list of dicts."""
        if not output:
            return []
        lines = output.strip().split("\n")
        if not lines:
            return []
        headers = lines[0].split("\t")
        result = []
        for line in lines[1:]:
            values = line.split("\t")
            result.append(dict(zip(headers, values)))
        return result
    
    # ========== Issue Commands ==========
    
    async def issue_list(self, repo: str, state: str = "open", limit: int = 10) -> str:
        """List issues in repository."""
        success, output = await self.run([
            "issue", "list", 
            "--repo", repo, 
            "--state", state,
            "--limit", str(limit),
            "--json", "number,title,state,author"
        ])
        if not success:
            return f"Error: {output}"
        
        import json
        try:
            issues = json.loads(output)
            if not issues:
                return "No issues found."
            lines = [f"**Issues** ({len(issues)}):\n"]
            for issue in issues:
                num = issue.get("number", "")
                title = issue.get("title", "")[:40]
                state_emoji = "🔴" if issue.get("state") == "open" else "🟢"
                author = issue.get("author", {}).get("login", "unknown") if isinstance(issue.get("author"), dict) else issue.get("author", "unknown")
                lines.append(f"- {state_emoji} **#{num}** {title} (@{author})")
            return "\n".join(lines)
        except json.JSONDecodeError:
            return output
    
    async def issue_view(self, repo: str, issue_number: int) -> str:
        """View issue details."""
        success, output = await self.run([
            "issue", "view",
            "--repo", repo,
            "--issue", str(issue_number),
            "--json", "number,title,state,body,author"
        ])
        if not success:
            return f"Error: {output}"
        
        import json
        try:
            data = json.loads(output)
            lines = [
                f"**#{data.get('number')}: {data.get('title')}**",
                f"**State:** {data.get('state')}",
                f"**Author:** {data.get('author', {}).get('login', 'unknown')}",
                "",
                data.get('body', 'No description')[:500]
            ]
            return "\n".join(lines)
        except json.JSONDecodeError:
            return output
    
    async def issue_create(self, repo: str, title: str, body: str = "", labels: list = None) -> str:
        """Create a new issue."""
        args = ["issue", "create", "--repo", repo, "--title", title]
        if body:
            args.extend(["--body", body])
        if labels:
            args.extend(["--label", ",".join(labels)])
        
        success, output = await self.run(args)
        if success:
            return f"Issue created: {output}"
        return f"Error: {output}"
    
    async def issue_close(self, repo: str, issue_number: int) -> str:
        """Close an issue."""
        success, output = await self.run([
            "issue", "close",
            "--repo", repo,
            "--issue", str(issue_number)
        ])
        return "Issue closed." if success else f"Error: {output}"
    
    async def issue_reopen(self, repo: str, issue_number: int) -> str:
        """Reopen an issue."""
        success, output = await self.run([
            "issue", "reopen",
            "--repo", repo,
            "--issue", str(issue_number)
        ])
        return "Issue reopened." if success else f"Error: {output}"
    
    # ========== PR Commands ==========
    
    async def pr_list(self, repo: str, state: str = "open", limit: int = 20) -> str:
        """List PRs in repository."""
        success, output = await self.run([
            "pr", "list",
            "--repo", repo,
            "--state", state,
            "--limit", str(limit),
            "--json", "number,title,state,author"
        ])
        if not success:
            return f"Error: {output}"
        
        import json
        try:
            prs = json.loads(output)
            if not prs:
                return "No PRs found."
            lines = [f"**Pull Requests** ({len(prs)}):\n"]
            for pr in prs:
                num = pr.get("number", "")
                title = pr.get("title", "")[:40]
                state_emoji = "🟢" if pr.get("state") == "OPEN" else "🔴"
                author = pr.get("author", {}).get("login", "unknown") if isinstance(pr.get("author"), dict) else pr.get("author", "unknown")
                lines.append(f"- {state_emoji} **#{num}** {title} (@{author})")
            return "\n".join(lines)
        except json.JSONDecodeError:
            return output
    
    async def pr_view(self, repo: str, pr_number: int) -> str:
        """View PR details."""
        success, output = await self.run([
            "pr", "view",
            "--repo", repo,
            "--pr", str(pr_number),
            "--json", "number,title,state,body,author,additions,deletions"
        ])
        if not success:
            return f"Error: {output}"
        
        import json
        try:
            data = json.loads(output)
            lines = [
                f"**#{data.get('number')}: {data.get('title')}**",
                f"**State:** {data.get('state')}",
                f"**Author:** {data.get('author', {}).get('login', 'unknown')}",
                f"**Changes:** +{data.get('additions', 0)} -{data.get('deletions', 0)}",
                "",
                data.get('body', 'No description')[:500]
            ]
            return "\n".join(lines)
        except json.JSONDecodeError:
            return output
    
    async def pr_checks(self, repo: str, pr_number: int) -> str:
        """View PR check status."""
        success, output = await self.run([
            "pr", "checks",
            "--repo", repo,
            "--pr", str(pr_number)
        ])
        return output if success else f"Error: {output}"
    
    async def pr_checkout(self, repo: str, pr_number: int, cwd: str = None) -> str:
        """Checkout a PR locally."""
        success, output = await self.run([
            "pr", "checkout",
            "--repo", repo,
            str(pr_number)
        ], cwd=cwd)
        return "Checked out PR." if success else f"Error: {output}"
    
    async def pr_merge(self, repo: str, pr_number: int, method: str = "merge") -> str:
        """Merge a PR."""
        success, output = await self.run([
            "pr", "merge",
            "--repo", repo,
            "--pr", str(pr_number),
            "--admin", "--merge" if method == "merge" else "--squash" if method == "squash" else "--rebase"
        ])
        return "PR merged." if success else f"Error: {output}"
    
    # ========== Repository Commands ==========
    
    async def repo_view(self, repo: str) -> str:
        """View repository info."""
        success, output = await self.run([
            "repo", "view",
            repo,
            "--json", "name,description,defaultBranch,stargazerCount"
        ])
        if not success:
            return f"Error: {output}"
        
        import json
        try:
            data = json.loads(output)
            return (
                f"**{data.get('name', repo)}**\n"
                f"{data.get('description', 'No description')}\n"
                f"⭐ {data.get('stargazerCount', 0)} | "
                f"Default: {data.get('defaultBranch', 'main')}"
            )
        except json.JSONDecodeError:
            return output
    
    async def repo_clone(self, repo: str, cwd: str = None) -> str:
        """Clone a repository."""
        success, output = await self.run([
            "repo", "clone", repo
        ], cwd=cwd)
        return f"Cloned {repo}." if success else f"Error: {output}"
    
    async def repo_fork(self, repo: str, cwd: str = None) -> str:
        """Fork a repository."""
        success, output = await self.run([
            "repo", "fork", repo, "--clone=false"
        ], cwd=cwd)
        return f"Forked {repo}." if success else f"Error: {output}"
    
    # ========== Run Commands ==========
    
    async def run_list(self, repo: str, limit: int = 10) -> str:
        """List workflow runs."""
        success, output = await self.run([
            "run", "list",
            "--repo", repo,
            "--limit", str(limit),
            "--json", "name,status,conclusion,databaseId"
        ])
        if not success:
            return f"Error: {output}"
        
        import json
        try:
            runs = json.loads(output)
            if not runs:
                return "No runs found."
            lines = [f"**Workflow Runs** ({len(runs)}):\n"]
            for run in runs:
                name = run.get("name", "")[:25]
                status = run.get("status", "")
                conclusion = run.get("conclusion", "")
                num = run.get("databaseId", "")
                emoji = "✅" if conclusion == "success" else "❌" if conclusion == "failure" else "⏳" if status == "in_progress" else "⚪"
                lines.append(f"- {emoji} **{name}** #{num}")
            return "\n".join(lines)
        except json.JSONDecodeError:
            return output
    
    async def run_view(self, repo: str, run_id: int) -> str:
        """View workflow run details."""
        success, output = await self.run([
            "run", "view",
            "--repo", repo,
            str(run_id)
        ])
        return output if success else f"Error: {output}"
    
    async def run_rerun(self, repo: str, run_id: int) -> str:
        """Rerun a workflow."""
        success, output = await self.run([
            "run", "rerun",
            "--repo", repo,
            str(run_id)
        ])
        return "Run rerun initiated." if success else f"Error: {output}"
    
    # ========== Gist Commands ==========
    
    async def gist_list(self, limit: int = 10) -> str:
        """List gists."""
        success, output = await self.run([
            "gist", "list",
            "--limit", str(limit)
        ])
        return output if success else f"Error: {output}"


__all__ = ["GitHubCLI"]
