---
name: test-workflow
description: Test skill demonstrating workflow orchestration (Issue #362)
version: 1.0.0
owner: dev-team
triggers:
  - /skill test-workflow
  - /test-workflow
  - test workflow
workflow:
  - id: step_1
    name: Gather Info
    description: Search for relevant information using web search
    tool: web_search
    outputs: [search_results]
    validation: must not be empty
    required_tools:
      - web_search
  - id: step_2
    name: Analyze Results
    description: Analyze the search results and extract key insights
    prompt_template: |
      Based on the search results from step 1, analyze and summarize:
      1. What are the top 3 key findings?
      2. Are there any contradictions or disagreements?
      3. What additional questions arise?
    required_tools:
      - web_search
  - id: step_3
    name: Generate Output
    description: Generate a structured report with the analysis
    prompt_template: |
      Create a structured report combining all previous steps.
      Format as markdown with clear sections.
output_format: markdown
---

# Skill: Test Workflow

This skill demonstrates the step-orchestrated workflow execution feature (Issue #362).

## Workflow Steps

### Step 1: Gather Info
- Uses `web_search` tool to search for information
- Output is validated to ensure it's not empty

### Step 2: Analyze Results
- LLM analyzes the gathered information
- Extracts key insights and patterns

### Step 3: Generate Output
- Creates a structured final report

## Usage

```
/test-workflow
/skill test-workflow
test workflow execution
```

## Key Features Demonstrated

- **Step Orchestration**: Each step is executed sequentially
- **Tool Filtering**: Only specific tools are available per step
- **Step Validation**: Results are validated before advancing
- **Progressive Context**: Each step sees output from previous steps
