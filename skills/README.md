# skills/ - Skill Declarations

This directory contains declarative skill definitions (.md files with YAML frontmatter).

## Structure

```
skills/
├── review-pr.md           # Single-file skill
├── test_case_generator/
│   └── skill.md          # Directory-based skill (step-based)
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

## Skill Execution Modes

Skills support two execution modes:

### 1. Legacy Mode (strategy-based)

Single-prompt injection - skill guidance is injected as one block into the system prompt.

```yaml
---
name: my-skill
description: "Legacy skill example"
triggers:
  - /my-skill
tools:
  - github_search
strategy:
  - "Step 1: Do something"
  - "Step 2: Do more"
output_format: markdown
---
```

### 2. Step-Based Mode (Issue #362)

Step-orchestrated execution - skill runs as a multi-step workflow with:
- Step-specific prompts
- Tool filtering per step
- Structured JSON output
- Progressive context passing
- Reference file loading per step

```yaml
---
name: my-workflow-skill
description: "Step-based skill example"
version: "1.1.0"
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
      - tool_name
    references:
      - ref-file.md
    completion_check:
      - artifacts.key exists
    next_step: step_2

  - id: step_2
    title: "Second Step"
    objective: "Next step objective"
    instructions:
      - "Use previous step artifacts"
    allowed_tools: []
    completion_check:
      - summary is not empty
    next_step: null
---
```

## Step Fields (Issue #362)

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique step identifier |
| `title` | string | Human-readable step name |
| `objective` | string | What the step aims to accomplish |
| `instructions` | list | Step-specific instructions for LLM |
| `allowed_tools` | list | Tools available for this step (empty = LLM only) |
| `references` | list | Reference files to load for this step |
| `completion_check` | list | Validation criteria for step output |
| `next_step` | string | ID of next step, or `null` for final step |

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

## Example: test_case_generator (Step-Based)

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
    objective: Extract testable requirements
    instructions:
      - Analyze user input
      - Identify testable scenarios
    allowed_tools: []
    completion_check:
      - artifacts.requirements exists
    next_step: generate_tests

  - id: generate_tests
    title: Generate Test Code
    objective: Generate pytest tests
    instructions:
      - Use extracted requirements
      - Generate pytest code
    allowed_tools: []
    completion_check:
      - artifacts.test_code exists
    next_step: finalize

  - id: finalize
    title: Finalize
    objective: Prepare final response
    instructions:
      - Summarize results
      - Format code
    allowed_tools: []
    completion_check:
      - summary is not empty
    next_step: null
---
```

## Reference Files

Reference files can be loaded per step for context:

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

## Backward Compatibility

- Skills with `strategy` use legacy single-prompt injection
- Skills with `steps` use step-orchestrated execution
- Field naming: `workflow` → `steps`, `required_tools` → `allowed_tools` (backward compat maintained)

## Tool Filtering

When a step specifies `allowed_tools`, only those tools are available to the LLM during that step. This prevents the LLM from using inappropriate tools and ensures step-specific focus.

Example:
```yaml
steps:
  - id: search
    allowed_tools:
      - github_search
      - web_search
```

## Logging

Workflow execution is logged with:
- `[Workflow] Starting workflow 'skill-name' with N steps`
- `[Workflow] Step 1: step_id - Step Title`
- `[Workflow] Step 1 result: status=success, summary=...`
- `[Workflow] All steps completed`

## Best Practices

1. **Keep steps focused** - Each step should have one clear objective
2. **Use completion_check** - Validate artifacts before advancing
3. **Limit allowed_tools** - Only include tools relevant to the step
4. **Provide reference files** - For complex steps, use reference loading
5. **Use meaningful IDs** - Step IDs are used for navigation

## Migration (Legacy → Step-Based)

To migrate a legacy skill:

1. Rename `strategy` to `steps`
2. Convert each strategy item into a step
3. Add `id`, `title`, `objective` fields
4. Define `allowed_tools` per step
5. Add `completion_check` for validation
6. Set `next_step` for flow control
