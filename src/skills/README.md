# skills/ - Skill Registry and Management

## Overview

The Skills module provides skill discovery, matching, and execution capabilities for the Engineering Flow Platform. Skills are self-contained task automation units defined in YAML files with metadata and triggers.

## Structure

```
skills/
├── registry.py    # Skill loading, matching, and discovery
├── runtime.py     # Skill runtime config + prompt block composition
└── tracer.py      # Skill execution tracing for UI
```

## Runtime Architecture (Skill-as-Command)

- Skill matching still happens in `SkillRegistry.match_skill(...)`.
- A match now becomes a `SkillRuntimeConfig` (not a separate skill workflow).
- Prompt injection is layered with compact blocks:
  1. **System rules**: hard runtime constraints summary.
  2. **Developer instructions**: description + strategy + compact body.
  3. **References summary**: filenames/paths only.
- The prompt assembly is built as explicit layers first, then serialized once at the final LLM request boundary.
- `allowed_tools` is enforced in the unified tool loop at runtime.
- `task_tools` marks tool names that should run through the task boundary (`src/agents/tasks.py`) rather than direct execution.
- `hooks` can register lightweight runtime hook points (`pre_tool`, `post_tool`) and optional callable adapters (`pre_tool:module.path.fn`), with safe failure handling.
- Callable hook adapters are resolved with a safe allowlist policy: default `src.hooks.` only. Test hooks (`tests.`) require `SKILL_RUNTIME_ENABLE_TEST_HOOKS=1`.
- Async hook callables/results are rejected by default (`unsupported_async_hook`) to keep runtime hook execution sync-safe.
- Hook adapters may optionally return:
  - `{"modified_args": {...}}` (pre-tool)
  - `{"short_circuit_result": ...}` (pre-tool)
  - `{"result_override": ...}` (post-tool)
- References stay compact by default (metadata + short availability context), without inlining full reference file contents.
- Explicit `references` entries are normalized to absolute paths (relative paths resolve from the skill directory/source file).
- For implicit references, fallback scanning is narrow (`references/` folder first, then `ref-*.md`-style local patterns) to avoid cross-skill contamination.

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
when_to_use:
  - For triage workflows
references:
  - references/playbook.md
model: gpt-5-mini
hooks:
  - precheck
task_tools:
  - run_command
risk_level: medium
planning_mode: auto   # auto|required|off
staging_mode: auto    # auto|required|off
execution_style: direct  # direct|stepwise
ask_user_policy: blocked_only  # blocked_only|permissive
---
```

### Response flow frontmatter

- `planning_mode`: `required` forces an initial plan; `off` disables plan-first behavior; `auto` follows runtime policy.
- `staging_mode`: `required` forces staged/phase output; `off` disables staged output; `auto` follows runtime policy.
- `execution_style`: `direct` completes in one turn when possible; `stepwise` keeps one-step progression.
- `ask_user_policy`: `blocked_only` asks only for truly blocking inputs; `permissive` allows broader clarification.

When `execution_style` or `ask_user_policy` is omitted from skill frontmatter, runtime falls back to `llm.response_flow.default_skill_execution_style` and `llm.response_flow.ask_user_policy` respectively. If the skill explicitly sets either field, the skill value takes precedence over global defaults.

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
