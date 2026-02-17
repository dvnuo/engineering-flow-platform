---
name: test-simple-ref
description: A simple test skill with reference files
version: 1.0.0
owner: test
triggers:
  - test ref
tools:
  - exec
strategy:
  - Read the reference files to understand the context
  - Summarize what you learned from the reference files
output_format: markdown
---
