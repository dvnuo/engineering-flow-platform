# skills/ - Skill Declarations

This directory contains declarative skill definitions (.md files with YAML frontmatter).

## Structure

```
skills/
├── review-pull-request/
│   ├── skill.md          # Directory-based skill
│   └── references/
├── test_case_generator/
│   └── skill.md          # Directory-based skill
└── skill_creator/
    ├── skill.md
    └── references/
```

## Principles

- **.md files** contain YAML frontmatter for metadata
- No implementation code in this directory
- Implementation lives in `src/` (e.g., `src/git/`, `src/github/`)

## Skill Naming Convention

- **Single-file skills**: `skills/*.md` (legacy format)
- **Directory skills**: `skills/*/skill.md` (preferred, e.g., `review-pull-request/skill.md`)

## Format

Each skill should have YAML frontmatter:

```yaml
---
name: skill-name
description: "Brief description"
version: "1.0.0"
owner: "team-name"
triggers:
  - /skill-name
  - trigger phrase
tools:
  - tool_name
strategy:
  - "Step 1: Do something"
  - "Step 2: Do more"
output_format: markdown
---
```

Followed by human-readable documentation.

## Skill Mode Demo Skills

This repository also includes multiple **demo skills** for validating skill mode behavior in realistic QA/dev workflows.

These demos are intentionally designed to exercise:
- `[ASK_USER]` when key input is missing
- `[EXECUTE]` for meaningful incremental progress
- `[FINISH]` for task wrap-up
- planning behavior differences (single-step vs multi-step)
- collaboration with full tool availability (used only when clearly helpful)
