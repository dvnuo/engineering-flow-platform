# skills/ - Skill Declarations

This directory contains declarative skill definitions (.md files with YAML frontmatter).

## Structure

```
skills/
├── review-pr.md           # Single-file skill
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

- **Single-file skills**: `skills/*.md` (e.g., `review-pr.md`)
- **Directory skills**: `skills/*/skill.md` (e.g., `skill_creator/skill.md`)

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
