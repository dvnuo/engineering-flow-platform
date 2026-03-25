---
name: test-workflow
description: Test skill demonstrating step-orchestrated workflow execution (Issue #362)
version: 1.0.0
owner: dev-team
triggers:
  - /skill test-workflow
  - /test-workflow
  - test workflow
output_format: json
steps:
  - id: gather_info
    title: Gather Information
    objective: Search for relevant information using web search
    instructions:
      - Use the web_search tool to find information related to the user's query
      - Collect at least 3 search results
      - Extract key facts and URLs from results
    allowed_tools:
      - web_search
    completion_check:
      - artifacts.search_results exists
    next_step: analyze_results

  - id: analyze_results
    title: Analyze Results
    objective: Analyze the search results and extract key insights
    instructions:
      - Review the search results from the previous step
      - Identify patterns, contradictions, or key themes
      - Summarize the top 3 findings
    allowed_tools:
      - web_search
    completion_check:
      - artifacts.top_findings exists
      - summary is not empty
    next_step: generate_report

  - id: generate_report
    title: Generate Report
    objective: Create a structured report with the analysis
    instructions:
      - Combine all findings from previous steps
      - Format as markdown with clear sections
      - Include all relevant artifacts
    allowed_tools: []
    completion_check:
      - summary is not empty
    next_step: null
---

# Skill: Test Workflow

This skill demonstrates the step-orchestrated workflow execution feature (Issue #362).

## Workflow Steps

### Step 1: Gather Information
- Uses `web_search` tool to collect information
- Results are stored in `artifacts.search_results`

### Step 2: Analyze Results
- Analyzes the gathered information
- Extracts key findings into `artifacts.top_findings`

### Step 3: Generate Report
- Creates a final structured markdown report
- Combines all previous step outputs

## Usage

```
/test-workflow
/skill test-workflow
test workflow execution
```

## Key Features (Issue #362)

- **Step Orchestration**: Each step executed sequentially
- **Tool Filtering**: Only `web_search` available in steps 1-2
- **Step Validation**: Results validated before advancing
- **Structured JSON Output**: Each step returns JSON with status, summary, artifacts
- **Progressive Context**: Each step sees previous outputs via artifacts
- **Reference Loading**: Reference files loaded per step (if specified)

## Expected Step Output Format

Each step should return:
```json
{
  "status": "success",
  "summary": "Brief description of what was done",
  "artifacts": {
    "key": "value"
  },
  "next_step": "next_step_id"
}
```
