# skills/ - Skill Registry and Management

## Overview

The Skills module provides skill discovery, matching, and execution capabilities for the Engineering Flow Platform. Skills are self-contained task automation units defined in YAML files with metadata and triggers.

## Structure

```
skills/
├── registry.py    # Skill loading, matching, and discovery
└── tracer.py      # Skill execution tracing for UI
```

## Components

### Skill Registry (`registry.py`)
- Loads skills from `skills/` directory and user skills from `~/.efp/skills/`
- Matches user messages to skills via triggers (exact match or pattern)
- Provides skill metadata to agent prompt builder
- Supports skill versioning and deprecation

### Skill Tracer (`tracer.py`)
- Tracks skill execution events for UI display
- Collects step-by-step execution data

## Quick Start

```python
from src.skills.registry import SkillRegistry

# Initialize registry
registry = SkillRegistry()
registry.load_skills()

# Match a skill by user message
matched = registry.match_skill("/test-simple-ref")
if matched:
    skill = matched[0]
    print(f"Matched: {skill.name}")
```

## Configuration

No additional configuration required. Skills are automatically discovered from:
- Project skills: `skills/` directory
- User skills: `~/.efp/skills/` (takes precedence)

## Dependencies

- `ruamel.yaml` - YAML parsing with comments preservation
- Standard library: `re`, `pathlib`, `logging`

## Skill Definition

Skills are defined in `skill.md` files:

```yaml
---
name: example-skill
description: A example skill
version: 1.0.0
owner: team
triggers:
  - /example
  - example skill
tools:
  - exec
strategy:
  - Read user input
  - Process the request
  - Return results
output_format: markdown
---
```

## Development Guide

1. Create a new directory in `skills/`
2. Add `skill.yaml` with required metadata
3. Define triggers that match user commands
4. List available tools in the `tools` section
5. Describe the execution strategy

### Best Practices

- Use descriptive skill names (kebab-case)
- Add multiple triggers for flexibility
- Limit tools to only what's necessary
- Keep strategy steps clear and actionable
