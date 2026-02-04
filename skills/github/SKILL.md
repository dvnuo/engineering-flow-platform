# GitHub Skill

Use this skill to interact with GitHub using the `gh` CLI. This skill allows the AI to clone repositories, manage issues, PRs, and more.

## Available Tools

| Tool | Description |
|------|-------------|
| `github_clone` | Clone a repository |
| `github_repo_clone` | Clone using gh repo clone (with interactive selection) |
| `github_issue_list` | List issues in a repository |
| `github_pr_list` | List pull requests |
| `github_pr_checks` | Check CI status on a PR |
| `github_run_list` | List workflow runs |
| `github_run_view` | View a workflow run |
| `github_api` | Make arbitrary GitHub API calls |

## Usage Examples

```
User: Clone the repo itwake/codew
AI: → github_clone(repo="itwake/codew")

User: Clone my fork of the project
AI: → github_clone(repo="itwake/codew", repo="yourusername/codew")

User: List open issues
AI: → github_issue_list(owner="itwake", repo="codew")

User: Check PR status
AI: → github_pr_checks(pr_number=55, owner="itwake", repo="codew")

User: View workflow run
AI: → github_run_view(run_id="12345678", owner="itwake", repo="codew")

User: Get repository info via API
AI: → github_api(endpoint="repos/itwake/codew")
```

## Installation

### macOS (Homebrew)
```bash
brew install gh
```

### Linux (apt)
```bash
apt install gh
```

### Verify Installation
```bash
gh --version
```

## Authentication

The AI will automatically use `gh auth` if needed. Users can authenticate with:
```bash
gh auth login
```

## Notes

- Always specify `--repo owner/repo` when not in a git directory
- Use `gh api` for data not available through other subcommands
- Most commands support `--json` for structured output
