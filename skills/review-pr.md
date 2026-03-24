---
name: review-pr
description: Review a GitHub pull request with structured feedback
version: 1.0.0
owner: devops-team
triggers:
  - review pr
  - code review
  - analyze pull request
  - /skill review-pr
  - /review-pr
  - review pull request
  - check pr
tools:
  - github_get_issue
  - github_get_pr_files
  - github_get_pr_diff
  - github_get_pr_comments
  - github_list_pr_reviews
  - github_add_pr_review_comment
strategy:
  - "1. Use github_get_issue to fetch PR details (title, description, state, author)"
  - "2. Use github_get_pr_files to get list of changed files"
  - "3. Use github_get_pr_diff to review the code changes"
  - "4. Use github_get_pr_comments to see existing review comments"
  - "5. Use github_list_pr_reviews to check previous reviews"
  - "6. Use github_add_pr_review_comment to add review feedback"
output_format: markdown
---

# Skill: Review PR

Review a GitHub pull request with structured feedback.

## Strategy

1. **Fetch PR details** - Use `github_get_issue` to get PR metadata (title, description, author, state)
2. **Get changed files** - Use `github_get_pr_files` to see what files were modified
3. **Review the diff** - Use `github_get_pr_diff` to analyze code changes
4. **Check existing comments** - Use `github_get_pr_comments` to see what others have said
5. **Check previous reviews** - Use `github_list_pr_reviews` to see review history
6. **Add review comment** - Use `github_add_pr_review_comment` to post feedback

## Usage Examples

- "review PR 128 in dvnuo/engineering-flow-platform"
- "/review-pr 171"
- "code review for PR #42 in myorg/myrepo"
- "check pr 256 in dvnuo/engineering-flow-platform"

## Output Format

Provide structured markdown with:
- **PR Summary**: Title, description, author, state
- **Files Changed**: List of modified files with +/-
- **Code Review**: Key observations from diff
- **Security Concerns**: Any security issues found
- **Suggestions**: Improvement recommendations
- **Decision**: Approve / Request Changes / Comment
