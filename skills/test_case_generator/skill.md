---
name: test-case-generator
description: Generate automated test cases from requirements
version: 1.0.0
owner: devops-team
triggers:
  - create tests
  - generate test cases
  - write tests
  - test case
tools: []
strategy:
  - "1. Extract requirements from user input"
  - "2. Generate pytest-compatible test code"
  - "3. Return formatted code block"
output_format: markdown
---

# Test Case Generator

**Capability**: Generate automated test cases from requirements using LLM.

## How It Works

This is handled by the LLM directly - no separate tool needed.

When user asks to "create tests" or "generate test cases":
1. Extract requirements from user input or Jira ticket
2. Generate pytest-compatible test code
3. Return formatted code block

## Example Response

```python
import pytest

class TestUserLogin:
    """Test cases for user login functionality."""
    
    def test_login_success(self):
        """Test successful login."""
        pass
```

## Usage

Just ask naturally:
- "create tests for user login"
- "generate test cases for the payment module"
- "write unit tests for user authentication"
