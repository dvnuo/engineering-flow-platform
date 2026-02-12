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
- "generate test cases for this feature"
- "write pytest tests"

## Notes

- LLM generates test code based on requirements
- Uses pytest conventions by default
- Includes TODO comments for implementation details
