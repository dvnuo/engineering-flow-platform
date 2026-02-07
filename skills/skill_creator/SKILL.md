---
name: skill-creator
description: Create or update AgentSkills with scripts, references, and assets. Use when designing, structuring, or packaging skills.
metadata:
  emoji: 🛠️
  requires:
    bins: [python3]
    anyBins: []
    env: []
    config: []
---

# Skill Creator

Create, update, and package AgentSkills with scripts, references, and assets.

## What This Skill Does

This skill helps you create and manage other skills. It provides:
1. **init** - Initialize a new skill from template
2. **package** - Package a skill into a distributable .skill file
3. **validate** - Validate skill structure and requirements
4. **list** - List all existing skills

## When to Use This Skill

Use this skill when:
- Creating a new skill from scratch
- Packaging a developed skill for distribution
- Validating skill structure before sharing
- Listing available skills in a directory

## Quick Start

### Option 1: Use the Skill Function

```python
from skills.skill_creator.skill import skill_creator

# Create a new skill
result = skill_creator(
    command="init",
    name="pdf-editor",
    path="skills/"
)

# Package a skill
result = skill_creator(
    command="package",
    path="skills/pdf-editor"
)

# Validate a skill
result = skill_creator(
    command="validate",
    path="skills/pdf-editor"
)

# List skills
result = skill_creator(
    command="list",
    path="skills/"
)
```

### Option 2: Use Scripts Directly

```bash
# Initialize a new skill
python3 skills/skill_creator/scripts/init_skill.py pdf-editor --path skills/

# Package a skill
python3 skills/skill_creator/scripts/package_skill.py skills/pdf-editor/

# Validate without packaging
python3 skills/skill_creator/scripts/package_skill.py skills/pdf-editor/ --validate-only
```

## Creating a New Skill: Step by Step

### Step 1: Initialize the Skill Template

Run the initialization script:

```bash
python3 skills/skill_creator/scripts/init_skill.py my-new-skill --path skills/
```

This creates:

```
skills/my-new-skill/
├── SKILL.md           # Template with frontmatter
├── scripts/          # Empty directory
├── references/        # Empty directory
└── assets/           # Empty directory
```

### Step 2: Edit SKILL.md

Update the YAML frontmatter:

```yaml
---
name: my-new-skill
description: A description of what this skill does. Include what it does and when to use it.
metadata:
  emoji: 🎯
  requires:
    bins: [git, python3]
    anyBins: []
    env: []
    config: []
---
```

Write the skill body with:

```markdown
# My New Skill

Brief description.

## Quick Start

\`\`\`
my-new-skill command="help"
\`\`\`

## Commands

| Command | Description |
|---------|-------------|
| help | Show help |
| run | Run the task |

## Examples

Example usage patterns.

## See Also

- references/additional.md for more info
```

### Step 3: Add Resources

Add files to the appropriate directories:

**scripts/** - For executable code:
```
skills/my-new-skill/scripts/
├── main.py           # Main script
└── utils.py          # Utility functions
```

**references/** - For documentation:
```
skills/my-new-skill/references/
├── api.md            # API documentation
└── examples.md       # Usage examples
```

**assets/** - For output files:
```
skills/my-new-skill/assets/
├── template.html     # HTML template
└── config.json       # Default config
```

### Step 4: Package the Skill

```bash
python3 skills/skill_creator/scripts/package_skill.py skills/my-new-skill/
```

This validates the skill and creates:
```
skills/my-new-skill.skill    # Distributable package
```

## Skill Structure Explained

### SKILL.md (Required)

Every skill must have a SKILL.md file with:

1. **YAML Frontmatter** (Required):
   ```yaml
   ---
   name: skill-name
   description: What the skill does and when to use it
   metadata:
     emoji: 🎯
     requires:
       bins: [python3, git]
       anyBins: [curl, wget]
       env: [API_KEY]
       config: [~/.config/app]
   ---
   ```

2. **Markdown Body** (Required):
   - Quick start examples
   - Command reference
   - Usage patterns

### scripts/ (Optional)

Executable scripts that can be run without loading into context.

Example: `scripts/process.py`
```python
#!/usr/bin/env python3
"""Process files."""

import sys

def main():
    print("Processing files...")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### references/ (Optional)

Documentation loaded as needed when the skill is triggered.

Example: `references/api.md`
```markdown
# API Reference

## Functions

### process_file(input, output)

Process a file from input to output.

**Parameters:**
- `input` (str): Input file path
- `output` (str): Output file path

**Returns:**
- bool: Success status
```

### assets/ (Optional)

Files used in output but not loaded into context.

Example: `assets/template.html`
```html
<!DOCTYPE html>
<html>
<head><title>{{title}}</title></head>
<body>{{content}}</body>
</html>
```

## Naming Conventions

### Good Names
- `pdf-editor` - Lowercase with hyphens
- `git-branch-manager` - Verb-noun pattern
- `jira-issue-creator` - Tool-noun pattern
- `docker-deploy` - Tool-action pattern

### Bad Names
- `GitBranchManager` - Uppercase not allowed
- `git_branch` - Underscores not allowed
- `my--skill` - Double hyphens not allowed

## Validation

The package script validates:

1. **SKILL.md exists**
2. **YAML frontmatter has name and description**
3. **Skill name is valid (lowercase, hyphens, max 64 chars)**
4. **Directories exist if created**

## Packaging

When packaging, the script:

1. Validates the skill structure
2. Creates a `.skill` file (zip format)
3. Includes all files in the skill directory

Example:
```
skills/pdf-editor.skill
```

## Best Practices

### 1. Keep SKILL.md Concise

Only include essential information. Move detailed docs to references/.

### 2. Use Progressive Disclosure

```
SKILL.md         -> ~100 words (always loaded)
references/*.md  -> Detailed docs (loaded when needed)
scripts/*.py     -> Executable (run without loading)
```

### 3. Clear Naming

Use descriptive names that indicate the skill's purpose.

### 4. Test Scripts

Ensure all scripts work correctly before packaging.

## Examples

### Example 1: PDF Editor Skill

```bash
# Initialize
python3 skills/skill_creator/scripts/init_skill.py pdf-editor --path skills/

# Edit SKILL.md
# Add scripts/rotate.py, scripts/merge.py
# Add references/api.md

# Package
python3 skills/skill_creator/scripts/package_skill.py skills/pdf-editor/
```

### Example 2: Git Branch Manager

```bash
# Initialize
python3 skills/skill_creator/scripts/init_skill.py git-branch-manager --path skills/

# Edit SKILL.md
# Add scripts/create_branch.py
# Add references/commands.md

# Package
python3 skills/skill_creator/scripts/package_skill.py skills/git-branch-manager/
```

## Troubleshooting

### "SKILL.md is required"

Make sure SKILL.md exists in the skill directory.

### "Invalid skill name"

Check that the name:
- Contains only lowercase letters, digits, and hyphens
- Doesn't start or end with a hyphen
- Has no consecutive hyphens
- Is 64 characters or less

### "Validation failed"

Run with `--validate-only` to see all errors:
```bash
python3 skills/skill_creator/scripts/package_skill.py skills/my-skill/ --validate-only
```

## See Also

- `skills/decorator.py` - Skill decorator for registering skills
- `skills/executor/` - Skill execution engine
