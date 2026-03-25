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
      - Return JSON with status, summary, and artifacts containing search_results
    allowed_tools:
      - web_search
    completion_check:
      - artifacts.search_results
    next_step: analyze_results

  - id: analyze_results
    title: Analyze Results
    objective: Analyze the search results and extract key insights
    instructions:
      - Review the search results from the previous step
      - Identify patterns, contradictions, or key themes
      - Summarize the top 3 findings
      - Return JSON with status, summary, and artifacts containing top_findings
    allowed_tools:
      - web_search
    completion_check:
      - artifacts.top_findings
    next_step: generate_report

  - id: generate_report
    title: Generate Report
    objective: Create a structured report with the analysis
    instructions:
      - Combine all findings from previous steps
      - Format as markdown with clear sections
      - Include all relevant artifacts
      - Return JSON with status, summary, and next_step=null
    allowed_tools: []
    completion_check:
      - summary
    next_step: null
---

# Skill: Test Workflow

This skill demonstrates the step-orchestrated workflow execution feature (Issue #362).

## Workflow Steps

### Step 1: Gather Information
- Uses `web_search` tool to collect information
- Results stored in `artifacts.search_results`

### Step 2: Analyze Results
- Analyzes the gathered information
- Outputs `artifacts.top_findings`

### Step 3: Generate Report
- Creates final markdown report

## Usage

```
/test-workflow
/skill test-workflow
test workflow execution
```

## Expected Output

Each step returns JSON:
```json
{
  "status": "success",
  "summary": "What was done",
  "artifacts": {"key": "value"},
  "next_step": "next_step_id"
}
```
