# src/skills - Runtime skill registry and loading infrastructure

## Overview

`src/skills` contains runtime infrastructure code (registry/runtime/tracer), not business skill content.

Business skills have been moved to a separate repository: `engineering-flow-platform-skills`.
In production, Portal/K8s checks out that skills repository into `/app/skills` inside the runtime container.

## What this directory contains

- `registry.py`: skill discovery, loading, metadata parsing, trigger matching
- `runtime.py`: runtime prompt/config assembly for active skills
- `tracer.py`: runtime skill execution tracing

## Runtime skill discovery behavior

`SkillRegistry` resolves project skills in this order:

1. `EFP_SKILLS_DIR` (if set and non-empty)
2. `/app/skills` (if it exists)
3. repo-root `skills/` (local development fallback only)
4. fallback `Path("skills")` (safe no-op when missing)

User override skills directory:

- `EFP_USER_SKILLS_DIR` (if set and non-empty), otherwise
- `~/.efp/skills`

Canonical skill metadata file is lowercase `skill.md`.
It is not an uppercase legacy filename and not `skill.yaml`.

Supported discovery patterns include:

- `/app/skills/*.md`
- `/app/skills/<skill-name>/skill.md`

Python-backed legacy skills (`skill.py`) are loaded by `src/agents/executor.py` from the same resolved skills directory.

## Quick Start

```python
from src.skills.registry import SkillRegistry

registry = SkillRegistry(project_skills_dir="/app/skills")
registry.load_skills()
```

## Development Guide

- Do **not** add business skills into this EFP repository.
- Add/modify business skills in `engineering-flow-platform-skills`.
- This repo should only maintain registry/runtime/tracer/loader behavior.
- Tests in this repo should use `tmp_path` or `tests/fixtures`, and must not depend on a real checked-out external skills repo.
