# Skills Directory

> Declarative skill definitions following OpenClaw architecture

## Directory Structure

```
skills/
├── __init__.py              # Skill registration (empty, for backward compat)
├── decorator.py             # @skill decorator definition
├── coding_agent/            # Coding agent skill
│   └── SKILL.md
├── git/                     # Git skill
│   └── SKILL.md
├── github/                  # GitHub skill
│   └── SKILL.md
├── test_case_generator/     # Test generation skill
│   └── SKILL.md
└── skill_creator/          # Skill creation tool
    ├── SKILL.md
    └── references/
```

## Core Principle

**Skills are declarative only** - Each skill contains only `SKILL.md` file with documentation. Implementation is in `src/<skill_name>/`.

This follows the [OpenClaw](https://github.com/openclaw/openclaw) architecture pattern.

## Skill Structure

### SKILL.md Template

```yaml
---
name: skill-name
description: "Brief description of the skill"
---

# Skill Name

## Description

Detailed description of what this skill does.

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| param1 | string | Yes | Description |
| param2 | integer | No | Description (default: 10) |

## Examples

\`\`\`python
# Example usage
result = skill_name(param1="value")
\`\`\`
```

## Available Skills

| Skill | Description | Implementation |
|-------|-------------|----------------|
| `coding_agent` | Run Codex/Claude/Pi agents | `src/executor/` |
| `git` | Git operations | `src/git/` |
| `github` | GitHub CLI operations | `src/github/` |
| `test_case_generator` | Generate test cases | `src/skill_creator/` |
| `skill_creator` | Create new skills | `src/skill_creator/` |

## How Skills Work

```
User Request → Match Skill → Execute Tool → Return Result
     ↓              ↓           ↓            ↓
   SKILL.md    skill name    src/*/     ToolResult
```

## Creating a New Skill

### Step 1: Create SKILL.md

Create `skills/my_skill/SKILL.md`:

```yaml
---
name: my-skill
description: "Description of my skill"
---

# My Skill

Describe the skill...
```

### Step 2: Create Tool Implementation

Add tool to `src/my_skill/__init__.py`:

```python
from src import ToolResult

async def my_tool(param: str) -> ToolResult:
    """Tool implementation."""
    return ToolResult(success=True, output="Result")
```

### Step 3: Update src/__init__.py

Export the new tool:

```python
from .my_skill import my_tool

__all__ = [
    ...
    "my_tool",
]
```

## Best Practices

1. **Declarative Only** - SKILL.md should describe, not implement
2. **Clear Documentation** - Include parameters and examples
3. **Type Hints** - Use in tool implementations
4. **Error Handling** - Return ToolResult with proper status

## Related

- [OpenClaw Skills](https://github.com/openclaw/openclaw/tree/main/skills)
- [src/](../src/) - Tool implementations
- [src/executor/](../src/executor/) - Skill execution engine
