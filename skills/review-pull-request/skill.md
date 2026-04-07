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
- Check prior comments and reviews first (`github_get_pr_comments`, `github_list_pr_reviews`) and avoid repeating concerns that are already raised.
- Prefer fewer, high-impact findings over many low-value comments.
- For every actionable finding that can be anchored to a changed line, prefer an inline PR review comment.
- Do not put issue details in the final PR summary when those details can be expressed as inline comments.
- When a concrete, low-risk replacement is obvious, include a GitHub suggestion block in the inline comment.
- Post the final PR comment only after inline review analysis is complete, and keep that final comment summary-only.

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

Use natural markdown with this structure and behavior:

- Final PR comment must be **summary-only**, and include:
  - reviewed scope
  - main risk areas
  - validation gaps
  - concise overall assessment
  - whether inline findings were left
- Inline comments should carry issue-level detail and include:
  - file
  - line
  - issue
  - why it matters
  - suggested fix
  - GitHub suggestion block when a concrete low-risk replacement is appropriate

Explicitly avoid:
- dumping all issues into the final summary instead of inline comments
- repeating concerns already present in prior review threads
- fabricating suggestion blocks just to satisfy format

## Reference usage

Use available review references to deepen language-aware review quality.
Load only relevant language references based on changed files.
