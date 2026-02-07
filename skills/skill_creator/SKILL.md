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

Create or update AgentSkills with proper structure and packaging.

## About Skills

Skills are modular packages that extend agent capabilities with specialized knowledge, workflows, and tools.

### What Skills Provide

1. **Specialized workflows** - Multi-step procedures for specific domains
2. **Tool integrations** - Instructions for working with specific file formats or APIs
3. **Domain expertise** - Company-specific knowledge, schemas, business logic
4. **Bundled resources** - Scripts, references, and assets for complex tasks

## Skill Structure

```
skill-name/
├── SKILL.md           # YAML frontmatter + Markdown (必需)
├── scripts/           # 可执行脚本 (Python/Bash)
├── references/        # 参考文档 (按需加载)
└── assets/           # 资源文件 (模板、图片)
```

### SKILL.md Format

```yaml
---
name: <skill-name>
description: <skill-description>
metadata:
  emoji: <emoji>
  requires:
    bins: [<cli-tools>]
    anyBins: [<alternative-tools>]
    env: [<required-env-vars>]
    config: [<required-config-files>]
---
```

## Progressive Disclosure

Skills use three-level loading:

1. **Metadata (~100 words)** - Always in context
2. **SKILL.md body (<5000 words)** - When skill triggers
3. **Bundled resources** - As needed (scripts can run without loading)

## Creating a New Skill

### Step 1: Initialize Skill

```bash
python3 skills/skill-creator/scripts/init_skill.py <skill-name> --path skills/
```

Creates:
- `skills/<skill-name>/SKILL.md` - Template with frontmatter
- `skills/<skill-name>/scripts/` - Scripts directory
- `skills/<skill-name>/references/` - References directory
- `skills/<skill-name>/assets/` - Assets directory

### Step 2: Edit SKILL.md

Update frontmatter:
```yaml
---
name: my-skill
description: What this skill does and when to use it
metadata:
  emoji: 🎯
  requires:
    bins: [git, python3]
---
```

Write body with:
- Quick start examples
- Detailed workflows
- Links to reference files

### Step 3: Add Resources

**scripts/** - Executable code:
- `scripts/rotate_pdf.py` - PDF rotation
- Scripts run without loading into context

**references/** - Documentation:
- API docs, schemas, policies
- Loaded as needed
- Keep SKILL.md lean

**assets/** - Output files:
- Templates, icons, boilerplate
- Used in final output

### Step 4: Package Skill

```bash
python3 skills/skill-creator/scripts/package_skill.py skills/<skill-name>/
```

Validates and creates distributable package.

## Skill Naming

- Lowercase letters, digits, hyphens only
- Under 64 characters
- Verb-led phrases: `git-branch-manager`, `jira-issue-creator`
- Namespace by tool: `gh-address-comments`, `linear-address-issue`

## Examples

### Initialize a new skill:
```
skill-creator init name="pdf-editor" path="skills/"
```

### Package an existing skill:
```
skill-creator package path="skills/pdf-editor/"
```

### Update skill resources:
```
skill-creator add-script name="pdf-editor" script="scripts/rotate.py"
```

## Best Practices

1. **Concise is key** - Challenge every piece of information
2. **Set appropriate freedom**:
   - Low freedom: Specific scripts with few parameters
   - Medium freedom: Pseudocode with parameters
   - High freedom: Text-based instructions
3. **Progressive disclosure** - Keep SKILL.md under 500 lines
4. **Reference files** - Link to detailed docs from SKILL.md

## Common Patterns

### Pattern 1: High-level guide with references
```markdown
# PDF Processing

Quick start:
\`\`\`python
extract_text("file.pdf")
\`\`\`

Advanced features:
- Forms: See [FORMS.md](references/FORMS.md)
- API: See [REFERENCE.md](references/REFERENCE.md)
```

### Pattern 2: Domain-specific organization
```
bigquery-skill/
├── SKILL.md
└── references/
    ├── finance.md
    ├── sales.md
    └── product.md
```

## Integration with Agent

Skills are loaded based on:
1. **name** - Matched against user requests
2. **description** - Used for context matching
3. **metadata.requires** - Checked before execution

Use the skill decorator:
```python
from skills.decorator import skill

@skill
def my_skill(command="help"):
    """Execute my custom skill."""
    ...
```

## See Also

- `skills/decorator.py` - Skill decorator and utilities
- `skills/executor/` - Skill execution engine
- OpenClaw skill-creator: https://github.com/openclaw/openclaw/tree/main/skills/skill-creator
