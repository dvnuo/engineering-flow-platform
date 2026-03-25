# skills/ - Skill Declarations

This directory contains declarative skill definitions (.md files with YAML frontmatter).

## Structure

```
skills/
├── review-pr.md              # Single-file skill (legacy)
├── test-workflow/           # Step-based workflow skill
│   └── skill.md
├── test_case_generator/
│   └── skill.md             # Step-based workflow example
└── skill_creator/
    └── skill.md
```

## Principles

- **.md files** contain YAML frontmatter for metadata
- No implementation code in this directory
- Implementation lives in `src/` (e.g., `src/github/`, `src/jira/`)

## Skill Naming Convention

- **Single-file skills**: `skills/*.md` (e.g., `review-pr.md`)
- **Directory skills**: `skills/*/skill.md` (e.g., `skill_creator/skill.md`)

---

## Skill Execution Modes

Skills support two execution modes:

### 1. Legacy Mode (strategy-based)

Single-prompt injection - skill guidance is injected as one block into the system prompt.

```yaml
---
name: my-legacy-skill
description: "Legacy skill example"
triggers:
  - /my-legacy
tools:
  - github_search
strategy:
  - "Step 1: Do something"
  - "Step 2: Do more"
output_format: markdown
---
```

### 2. Step-Based Mode (Issue #362) ✨

Step-orchestrated execution - skill runs as a multi-step workflow with per-step control.

```yaml
---
name: my-workflow-skill
description: "Step-based skill example"
version: 1.1.0
triggers:
  - /my-workflow
output_format: json
steps:
  - id: step_1
    title: "First Step"
    objective: "What this step accomplishes"
    instructions:
      - "Instruction 1"
      - "Instruction 2"
    allowed_tools:
      - github_search
    references:
      - patterns.md
    completion_check:
      - artifacts.search_results
    next_step: step_2
```

---

## Step Fields (Issue #362)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | ✅ | Unique step identifier (used for `next_step`) |
| `title` | string | ✅ | Human-readable step name |
| `objective` | string | ✅ | What the step aims to accomplish |
| `instructions` | list | ❌ | Step-specific instructions for LLM |
| `allowed_tools` | list | ❌ | Tools available for this step (empty = LLM only) |
| `references` | list | ❌ | Reference files to load for this step |
| `completion_check` | list | ❌ | Validation criteria for step output |
| `next_step` | string | ✅ | ID of next step, or `null` for final step |

---

## Step Output Schema (Issue #362)

Each step must return structured JSON:

```json
{
  "status": "success|needs_retry|failed",
  "summary": "Brief description of step completion",
  "artifacts": {
    "key": "value"
  },
  "next_step": "next_step_id"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `success` = proceed, `needs_retry` = retry, `failed` = abort |
| `summary` | string | Brief description of what was accomplished |
| `artifacts` | object | Key-value pairs passed to next step |
| `next_step` | string | ID of next step, or `null` if final |

---

## Completion Check Format

Validation rules for `completion_check`:

```
artifacts.key        → Check artifact exists
artifacts.key exists → Same as above (backward compat)
summary              → Check field exists
```

Example:
```yaml
completion_check:
  - artifacts.search_results   # search_results must exist in artifacts
  - summary                    # summary field must exist
```

---

## Reference Files

Reference files are loaded per step and appended to the step prompt.

```
skills/
└── my-skill/
    ├── skill.md
    └── references/
        ├── patterns.md
        └── guidelines.md
```

In skill.yaml:
```yaml
steps:
  - id: analyze
    references:
      - patterns.md
      - guidelines.md
```

---

## Tool Filtering

When a step specifies `allowed_tools`, only those tools are available to the LLM.

```yaml
steps:
  - id: search
    allowed_tools:
      - github_search_issues
      - github_get_issue
```

If `allowed_tools` is empty `[]`, the LLM runs without tool access (pure reasoning).

---

## Execution Flow

```
User: /my-workflow

1. Skill matched → has_steps = True
2. Build step prompt for step_1
3. Filter tools based on allowed_tools
4. LLM executes step, returns JSON
5. Validate completion_check
6. If valid: move to next_step
7. Repeat until next_step = null
```

---

## Example: test_case_generator

```yaml
---
name: test-case-generator
description: Generate test cases from requirements
triggers:
  - create tests
  - generate test cases
output_format: json

steps:
  - id: extract_requirements
    title: Extract Requirements
    objective: Extract testable requirements from input
    instructions:
      - Analyze user input
      - Identify testable scenarios
    allowed_tools: []
    completion_check:
      - artifacts.requirements
    next_step: generate_tests

  - id: generate_tests
    title: Generate Test Code
    objective: Generate pytest tests
    instructions:
      - Use extracted requirements
      - Generate pytest code
    allowed_tools: []
    completion_check:
      - artifacts.test_code
    next_step: finalize

  - id: finalize
    title: Finalize
    objective: Prepare final response
    instructions:
      - Summarize results
      - Format code
    allowed_tools: []
    completion_check:
      - summary
    next_step: null
```

---

## Migration: Legacy → Step-Based

To migrate a legacy skill:

1. Rename `strategy` to `steps`
2. Convert each strategy item into a step with `id`, `title`, `objective`
3. Define `allowed_tools` per step
4. Add `completion_check` for validation
5. Set `next_step` for flow control

Example:
```yaml
# Legacy
strategy:
  - "Step 1: Do X"
  - "Step 2: Do Y"

# Step-based
steps:
  - id: do_x
    title: "Do X"
    objective: "Do X to prepare"
    instructions:
      - "Step 1: Do X"
    allowed_tools: [tool_a]
    completion_check: []
    next_step: do_y

  - id: do_y
    title: "Do Y"
    objective: "Do Y to complete"
    instructions:
      - "Step 2: Do Y"
    allowed_tools: []
    completion_check: []
    next_step: null
```

---

## Logging

Workflow execution logs:

```
[Skill] Matched skill: my-skill
[Skill] Using step-based workflow: 3 steps
[Workflow] Step 1: step_id - Step Title
[Workflow] Step 1 result: status=success, summary=...
[Workflow] Step 2: ...
[Workflow] All steps completed
```

---

## Best Practices

1. **Keep steps focused** - One clear objective per step
2. **Use completion_check** - Validate artifacts before advancing
3. **Limit allowed_tools** - Only tools relevant to the step
4. **Reference files** - For complex steps, use reference loading
5. **Meaningful IDs** - Step IDs used for navigation

---

## Backward Compatibility

- Skills with `strategy` → legacy single-prompt mode
- Skills with `steps` → step-orchestrated mode
- Field aliases: `workflow` → `steps`, `required_tools` → `allowed_tools`
