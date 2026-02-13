---
name: review-pr
description: Review a GitHub pull request
version: 1.0.0
owner: devops-team
triggers:
  - review pr
  - code review
  - analyze pull request
  - /skill review-pr
  - /review-pr
tools:
  - github_get_issue
  - github_search_issues
  - github_add_comment
strategy:
  - "1. Use github_get_issue to fetch PR details (title, description, state, author)"
  - "2. Use github_search_issues to find related issues"
  - "3. Generate structured review feedback"
  - "4. Use github_add_comment to post review findings"
output_format: markdown
---

# Skill: Review PR

Review a GitHub pull request with structured feedback.

## Strategy

1. **Fetch PR details** - Use `github_get_pr` to get PR metadata (title, description, author, files changed)
2. **Analyze the diff** - Use `github_get_diff` to review code changes
3. **Generate feedback** - Provide constructive code review comments
4. **Post comment** - Use `github_comment_pr` to add review findings

## Examples

- "review PR 128 in dvnuo/engineering-flow-platform"
- "/review-pr 171"
- "code review for PR #42"

## Output Format

Markdown with:
- PR Summary
- Files Changed
- Code Quality Notes
- Security Concerns
- Suggestions
