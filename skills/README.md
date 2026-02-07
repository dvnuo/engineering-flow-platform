# Skills Directory

## Directory Structure

```
skills/
├── __init__.py              # Skill registration module
├── decorator.py             # @skill decorator definition
├── executor/                # Skill execution engine
│   ├── __init__.py
│   └── executor.py         # execute_skill() function
├── coding_agent/            # Coding agent (Codex/Claude/Pi)
│   ├── SKILL.md
│   └── skill.py
├── cron/                    # Scheduled task skills
│   ├── __init__.py
│   └── cron_skill.py
├── git/                     # Git operation skills
│   ├── __init__.py
│   └── git_skill.py
├── github-skill/            # GitHub API integration
│   ├── __init__.py
│   └── github_skill.py
├── git-skill/               # Git wrapper skills
├── skill_creator/           # Skill creation tool
│   ├── SKILL.md
│   ├── scripts/
│   │   └── package_skill.py
│   └── skill.py
├── summarize/               # Text summarization skill
│   ├── __init__.py
│   └── summarize_skill.py
├── test_case_generator/     # Test case generation
│   ├── __init__.py
│   └── test_case_skill.py
└── test_case_generator/     # (duplicate, needs cleanup)
```

## How It Works

### 1. Skill Registration (@skill decorator)
```python
from skills.decorator import skill, SkillResult

@skill
def my_skill(param1: str, param2: int = 10) -> SkillResult:
    """Skill description.
    
    Args:
        param1: Description of param1
        param2: Description of param2 (default: 10)
    
    Returns:
        SkillResult with success, output, error, data
    """
    return SkillResult(success=True, output="result")
```

### 2. Skill Execution Flow
```
User Request → @skill decorator → Skill Registry → Executor → SkillResult
```

### 3. SkillResult Structure
```python
class SkillResult:
    success: bool      # Whether the operation succeeded
    output: str        # Normal output content
    error: str         # Error message if failed
    data: dict         # Additional structured data
```

### 4. Skill Registry (skills/__init__.py)
```python
# Skills are auto-discovered and registered
SKILL_REGISTRY = {
    "skill_name": skill_function,
    "git_commit": git_commit_skill,
    "github_pr": github_pr_skill,
}

def execute_skill(skill_name: str, **kwargs) -> SkillResult:
    """Execute a skill by name."""
    skill_func = SKILL_REGISTRY.get(skill_name)
    if not skill_func:
        return SkillResult(success=False, error=f"Unknown skill: {skill_name}")
    return skill_func(**kwargs)
```

## What Problems It Solves

- **Code Reusability**: Standardized skill definition via @skill decorator
- **Unified Interface**: All skills return SkillResult
- **Dynamic Execution**: Executor calls skills by name
- **Parameter Validation**: Function signatures define parameters
- **Auto-discovery**: Skills are auto-registered on import

## Configuration Options

### Core Configuration (config.yaml)

```yaml
# config.yaml
skills:
  # Skill execution settings
  execution:
    timeout: 300              # Default timeout in seconds
    retry_count: 3           # Number of retries on failure
    retry_delay: 1           # Delay between retries (seconds)
  
  # Registry settings
  registry:
    auto_register: true      # Auto-discover skills on startup
    excluded_skills: []      # Skills to exclude
  
  # Per-skill configurations
  skills:
    coding_agent:
      default_agent: "codex"
      workdir_enabled: true
      pty_required: true
    
    github:
      api_url: "https://api.github.com"
      rate_limit: 5000       # API rate limit per hour
      timeout: 30            # Request timeout
    
    git:
      safe_directory: "*"    # Git safe directories
      default_branch: "master"
```

### Environment Variables

```bash
# GitHub integration
GITHUB_TOKEN=ghp_xxxxxxxxxxxx
GITHUB_ORG=myorganization

# Git configuration
GIT_SSH_KEY=/path/to/private_key
GIT_USER_NAME=My Bot
GIT_USER_EMAIL=bot@example.com

# Coding agent
CODEX_API_KEY=sk-xxxxxxxxxxxx
CLAUDE_API_KEY=sk-ant-xxxxxxxx
```

### Coding Agent Specific (skills/coding_agent/skill.py)

```python
# Runtime configuration
CODING_AGENT_CONFIG = {
    "default_agent": "codex",      # codex, claude, opencode, pi
    "mode": "full-auto",           # full-auto, yolo, vanilla
    "pty": True,                   # Require PTY for interactive agents
    "background": False,           # Run in background
    "timeout": 300,               # Execution timeout (seconds)
    "workdir": None,              # Working directory constraint
}
```

### Git Configuration (skills/git/)

```python
GIT_CONFIG = {
    "user": {
        "name": None,             # GIT_USER_NAME
        "email": None,            # GIT_USER_EMAIL
    },
    "ssh": {
        "key_path": None,         # GIT_SSH_KEY
        " passphrase": None,
    },
    "safe_directories": ["*"],    # Git safe directories
    "default_remote": "origin",
    "default_branch": "master",
}
```

## How to Run

### Test Skills
```bash
# Run all skill tests
pytest tests/test_*.py -v

# Run specific skill tests
pytest tests/test_coding_agent.py -v

# Run with coverage
pytest --cov=skills --cov-report=html
```

### Execute via Executor
```python
from skills import execute_skill

# Call a skill
result = execute_skill("coding_agent", 
                      command="exec", 
                      agent="codex", 
                      prompt="Build a REST API")

# Check result
if result.success:
    print(result.output)
else:
    print(f"Error: {result.error}")
```

### Manual Skill Testing
```python
from skills.coding_agent.skill import coding_agent

result = coding_agent(
    command="exec",
    agent="codex",
    prompt="Create a hello world function",
    mode="full-auto",
    workdir="/tmp/test"
)
```

## Development Principles

### 1. Skill Naming Conventions
```python
# Use snake_case
def git_commit(): ...
def github_create_issue(): ...

# Use prefixes for grouping
git_*          # Git-related skills
github_*       # GitHub API skills
coding_agent_* # Coding agent related
```

### 2. Function Signatures
```python
# Required: Type hints for all parameters
@skill
def my_skill(
    param1: str,              # Required parameters first
    param2: int = 10,         # Optional with defaults
    optional_param: bool = False
) -> SkillResult:
    """Clear docstring describing the skill."""
    # Implementation
    return SkillResult(...)
```

### 3. Error Handling
```python
@skill
def safe_skill(operation: str) -> SkillResult:
    try:
        # Business logic
        result = perform_operation(operation)
        return SkillResult(success=True, output=str(result))
    except ValueError as e:
        return SkillResult(success=False, error=f"Invalid input: {e}")
    except Exception as e:
        return SkillResult(success=False, error=f"Unexpected error: {e}")
```

### 4. Documentation Requirements
```python
@skill
def complex_skill(param: str) -> SkillResult:
    """Brief skill description.
    
    This section explains what the skill does in detail.
    It should be comprehensive enough for users to understand.
    
    Args:
        param: Detailed description of parameter
    
    Returns:
        SkillResult containing:
            - success: Boolean indicating success/failure
            - output: String result or description
            - data: Optional dict with additional info
    
    Examples:
        >>> skill(param="test")
        SkillResult(success=True, output="result")
    """
    ...
```

### 5. Testing Standards
```python
class TestMySkill:
    """Test suite for my_skill."""
    
    def test_success_case(self):
        """Test successful execution."""
        result = my_skill(param="valid")
        assert result.success is True
        assert "expected" in result.output
    
    def test_failure_case(self):
        """Test error handling."""
        result = my_skill(param="invalid")
        assert result.success is False
        assert result.error is not None
    
    def test_edge_cases(self):
        """Test boundary conditions."""
        result = my_skill(param="")
        assert result.success is False
```

### 6. Performance Guidelines
- **Timeout Awareness**: Set appropriate timeouts
- **Resource Cleanup**: Use context managers
- **Async Support**: Consider async for I/O bound tasks
- **Caching**: Cache expensive operations when appropriate

## Available Skills

| Skill Name | Function | Status | Dependencies |
|------------|----------|--------|--------------|
| coding_agent | Run Codex/Claude/Pi agents | ✅ Complete | bash, pty |
| git_commit | Commit changes | ✅ Complete | git |
| git_push | Push to remote | ✅ Complete | git |
| github_create_issue | Create GitHub issue | ✅ Complete | github-token |
| github_pr | Manage PRs | ✅ Complete | github-token |
| summarize | Text summarization | ✅ Complete | - |
| test_case_generator | Generate tests | ✅ Complete | - |
| skill_creator | Create new skills | ✅ Complete | - |

## Extending the Framework

### Create a New Skill

1. **Create skill file**:
```python
# skills/my_skill/skill.py
from skills.decorator import skill, SkillResult

@skill
def my_skill(input_param: str) -> SkillResult:
    """My new skill."""
    return SkillResult(success=True, output=f"Processed: {input_param}")
```

2. **Create documentation**:
```markdown
# skills/my_skill/SKILL.md
---
name: my-skill
description: Process input parameters
---

# My Skill

## Usage

...
```

3. **Add tests**:
```python
# tests/test_my_skill.py
def test_my_skill():
    from skills.my_skill.skill import my_skill
    result = my_skill("test")
    assert result.success is True
```

### Best Practices

1. **Keep skills focused** - Single responsibility
2. **Use type hints** - For all parameters
3. **Document thoroughly** - SKILL.md + docstrings
4. **Test comprehensively** - Unit + integration tests
5. **Handle errors gracefully** - Never let exceptions escape
6. **Follow naming** - snake_case with clear prefixes

## Troubleshooting

### Skill Not Found
```python
# Check if skill is registered
from skills import SKILL_REGISTRY
print(SKILL_REGISTRY.keys())
```

### Import Errors
```bash
# Ensure skills package is in PYTHONPATH
export PYTHONPATH=/path/to/opsclaw:$PYTHONPATH
```

### Timeout Issues
```python
# Increase timeout in config
skills:
  execution:
    timeout: 600  # 10 minutes
```
