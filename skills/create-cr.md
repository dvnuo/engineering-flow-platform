---
name: create-cr
description: Create a GitHub pull request for code review
version: 1.0.0
owner: devops-team
triggers:
  - create cr
  - submit code review
  - create pull request
  - /skill create-cr
  - /create-cr
  - open pr
  - new pr
tools:
  - github_list_branches
  - github_get_default_branch
  - github_create_branch
  - github_get_file_content
  - github_create_or_update_file
  - github_create_pull_request
strategy:
  - "1. Use github_list_branches to see available branches"
  - "2. Use github_get_default_branch to find the target branch"
  - "3. Use github_create_branch to create a new feature branch"
  - "4. Use github_get_file_content to view files to modify"
  - "5. Use github_create_or_update_file to commit code changes"
  - "6. Use github_create_pull_request to create the PR"
output_format: markdown
---

# Skill: Create CR

Create a GitHub pull request for code review.

## Strategy

1. **List branches** - Use `github_list_branches` to see existing branches
2. **Get default branch** - Use `github_get_default_branch` to find target (usually main/master)
3. **Create branch** - Use `github_create_branch` to create a new feature branch
4. **View files** - Use `github_get_file_content` to see files you want to modify
5. **Commit changes** - Use `github_create_or_update_file` to push code changes
6. **Create PR** - Use `github_create_pull_request` to open a PR for review

## Usage Examples

- "create a pull request for fixing bug 123"
- "/create-cr"
- "submit code review for feature-login"
- "open PR from feature-branch to main"

## Prerequisites

Before creating a PR, you should:
1. Know the file paths you want to modify
2. Know the content or changes you want to make
3. Have a clear PR title and description

## Output Format

Provide structured markdown with:
- **Branch Created**: Feature branch name
- **Files Modified**: List of files changed
- **Commit(s)**: Summary of commits made
- **PR Created**: PR link and number
- **Next Steps**: What the reviewer should do
