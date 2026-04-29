---
name: review-pull-request
description: Review a GitHub pull request with high-signal, actionable feedback
version: 2.1.0
owner: devops-team
triggers:
  - review pull request
  - review pr
  - code review
  - analyze pull request
  - /skill review-pull-request
  - /skill review-pr
  - /review-pull-request
  - /review-pr
  - check pull request
  - check pr
tools:
  - github_get_pr
  - github_get_pr_files
  - github_get_pr_file_patch
  - github_get_pr_diff
  - github_get_pr_comments
  - github_list_pr_reviews
  - github_add_pr_review_comment
  - github_add_comment
output_format: markdown
---

# Skill: review-pull-request

Provide a GitHub Copilot-style pull request review while staying concise and high signal.

## Pull request review strategy

- Operate in review/comment mode only. Do **not** decide approval state (no "Approve" / "Request changes").
- Start by fetching pull request metadata with `github_get_pr`.
- Inspect changed files before commenting: use `github_get_pr_files`, then `github_get_pr_file_patch` for file-level analysis, and `github_get_pr_diff` when broader context is required.
- Check prior discussion first (`github_get_pr_comments`, `github_list_pr_reviews`) and avoid repeating concerns that are already raised.
- Prefer fewer, high-impact findings over many low-value comments.
- Add inline comments only for concrete, line-anchorable issues.
- Post final summary only after completing pull request review analysis.

## Priorities

Prioritize findings in this order:
1. correctness
2. security
3. reliability
4. backward compatibility / contract safety
5. test coverage gaps
6. maintainability
7. style only when it affects meaning, readability, or safety

## Output contract

Use natural markdown with this structure:

## Pull Request Summary
- Scope
- Main modules changed
- Risk level
- Review focus
- Validation gaps
- Overall assessment

## Findings
For each finding include:
- severity: blocking / important / suggestion
- file
- line (if available)
- issue
- why it matters
- suggested fix

Inline comment style should be concise and reviewer-like:
- identify the concern
- explain why it matters
- suggest a fix or safer direction

## Reference usage

Use available review references to deepen language-aware review quality.
Load only relevant language references based on changed files.

## Automation handoff mode

When the user/task says "runtime-managed writeback" or "Do not call github_add_comment / github_add_pr_review_comment":

- Use read-only GitHub tools to inspect the PR.
- Do not call `github_add_comment`.
- Do not call `github_add_pr_review_comment`.
- Return the final review body as markdown.
- The runtime task wrapper will perform freshness checks, governance checks, and GitHub writeback.
