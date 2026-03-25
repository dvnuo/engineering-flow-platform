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
    objective: Search GitHub for relevant issues or PRs related to the query
    instructions:
      - Use the github_search_issues tool to search for relevant information
      - Search for issues related to the user's query
      - Collect the results
      - Return JSON with status, summary, and artifacts containing search_results
    allowed_tools:
      - github_search_issues
    completion_check:
      - artifacts.search_results
    next_step: analyze_results

  - id: analyze_results
    title: Analyze Results
    objective: Analyze the search results and extract key insights
    instructions:
      - Review the search results from the previous step
      - Identify patterns, contradictions, or key themes
      - Summarize the top findings
      - Return JSON with status, summary, and artifacts containing top_findings
    allowed_tools:
      - github_search_issues
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
- Uses `github_search_issues` tool to search GitHub
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
