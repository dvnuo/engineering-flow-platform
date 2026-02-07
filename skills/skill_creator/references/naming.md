# Skill Naming Guidelines

## Rules

1. **Lowercase only** - No uppercase letters
2. **Hyphens for spaces** - `my-skill`, not `my_skill` or `mySkill`
3. **Digits allowed** - `pdf-editor-v2`
4. **Max 64 characters**
5. **No consecutive hyphens** - `my--skill` is invalid

## Examples

### Good Names
```markdown
- git-branch-manager
- jira-issue-creator
- pdf-editor
- docker-deploy
- api文档生成器  # Chinese allowed
```

### Bad Names
```markdown
- GitBranchManager  # Uppercase
- git_branch_manager  # Underscores
- my--skill  # Consecutive hyphens
- my-skill-  # Trailing hyphen
```

## Naming Patterns

### Tool-Namespace Pattern
```
<tool>-<action>
- gh-address-comments
- linear-address-issue
- jira-create-ticket
```

### Domain Pattern
```
<domain>-<focus>
- finance-report-generator
- marketing-copy-writer
- docs-optimizer
```

### Language/Framework Pattern
```
<language>-<purpose>
- python-api-builder
- react-component-generator
```

## Triggers

Skill names should match likely user requests:

| Skill Name | Triggers When User Says... |
|------------|---------------------------|
| `pdf-editor` | "edit pdf", "modify pdf", "rotate pdf" |
| `git-branch-manager` | "create branch", "delete branch", "merge" |
| `docker-deploy` | "deploy docker", "build container" |

Include trigger patterns in description:
```yaml
description: |
  Create and manage git branches. Use when user says:
  - "create a new branch"
  - "switch to another branch"
  - "delete this branch"
```
