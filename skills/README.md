# skills/ - Skill Declarations

This directory contains declarative skill definitions only (SKILL.md files).

## Structure

```
skills/
├── coding_agent/
│   └── SKILL.md
├── git/
│   └── SKILL.md
├── github/
│   └── SKILL.md
├── test_case_generator/
│   └── SKILL.md
└── skill_creator/
    ├── SKILL.md
    └── references/
```

## Principles

- **SKILL.md** contains only metadata and descriptions
- No implementation code in this directory
- Implementation lives in `src/` (e.g., `src/git/`, `src/github/`)

## SKILL.md Format

Each skill should have a `SKILL.md` with:
- Name and description
- Commands/parameters
- Examples
- Dependencies (if any)
