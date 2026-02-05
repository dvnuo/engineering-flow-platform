# GitHub Skill - GitHub CLI (gh)

Execute any GitHub CLI command with flexible arguments.

## Skill Signature

```python
github(command="repo list", args="--limit 10", hostname=None) -> SkillResult
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | No | gh subcommand (default: "repo list") |
| `args` | string | No | Additional arguments (space-separated) |
| `hostname` | string | No | GitHub Enterprise hostname (optional) |

## Examples

### Repository Operations

```python
# List repositories
github(command="repo", args="list --limit 10")

# Clone a repository
github(command="repo", args="clone owner/repo")

# Clone to specific directory
github(command="repo", args="clone owner/repo --path ./my-repo")

# View repository
github(command="repo", args="view owner/repo")

# Create repository
github(command="repo", args="create --name my-repo --public")
```

### Issue Operations

```python
# List issues
github(command="issue", args="list --repo owner/repo --state open --limit 10")

# View an issue
github(command="issue", args="view 123 --repo owner/repo")

# Create an issue
github(command="issue", args="create --repo owner/repo --title 'Bug: fix required' --body 'Description'")

# Close an issue
github(command="issue", args="close 123 --repo owner/repo")

# Comment on an issue
github(command="issue", args="comment 123 --repo owner/repo --body 'Thanks for reporting!'")
```

### Pull Request Operations

```python
# List PRs
github(command="pr", args="list --repo owner/repo --state open --limit 10")

# View a PR
github(command="pr", args="view 123 --repo owner/repo")

# Checkout a PR locally
github(command="pr", args="checkout 123 --repo owner/repo")

# Check CI status on PR
github(command="pr", args="checks 123 --repo owner/repo")

# Create a PR
github(command="pr", args="create --repo owner/repo --title 'feat: new feature' --body 'Description'")

# Merge a PR
github(command="pr", args="merge 123 --repo owner/repo --admin --subject 'Merge PR'")

# Review a PR
github(command="pr", args="review 123 --repo owner/repo --approve")
```

### Workflow/Action Operations

```python
# List workflow runs
github(command="run", args="list --repo owner/repo --limit 10")

# View a specific run
github(command="run", args="view 12345678 --repo owner/repo")

# View run logs
github(command="run", args="view 12345678 --repo owner/repo --log")

# Rerun a workflow
github(command="run", args="rerun 12345678 --repo owner/repo")

# List workflows
github(command="run", args="list --repo owner/repo")

# Disable a workflow
github(command="run", args="disable 12345 --repo owner/repo")
```

### GitHub API Calls

```python
# Get repository info
github(command="api", args="repos/owner/repo")

# List contributors
github(command="api", args="repos/owner/repo/contributors")

# Get commit
github(command="api", args="repos/owner/repo/commits/sha")

# Search code
github(command="api", args="search/code?q=repo:owner/repo+function_name")

# Get workflow runs via API
github(command="api", args="repos/owner/repo/actions/runs")

# Create issue via API
github(command="api", args="repos/owner/repo/issues --method POST --input -", body='{"title":"Bug"}')
```

### Enterprise GitHub

```python
# Use with GitHub Enterprise
github(command="repo", args="list --enterprise mycompany", hostname="github.enterprise.com")
```

## Authentication

```bash
# Login to GitHub
gh auth login

# Check status
gh auth status

# Refresh token
gh auth refresh
```

## Tips

1. **Use `--repo owner/repo`** when not in a git directory
2. **Use `--json`** for structured output in API calls
3. **Combine commands**: List → View → Create workflow
4. **Use `-` for stdin** with `--input -` for API POST/PUT
5. **Most subcommands support `--limit`** to control output

## Common Workflows

```python
# Find and checkout a PR
github(command="pr", args="list --repo owner/repo --state open")
github(command="pr", args="checkout 123 --repo owner/repo")

# Check CI, then merge
github(command="pr", args="checks 123 --repo owner/repo")
github(command="pr", args="merge 123 --repo owner/repo --admin")
```
