# Test Case Generator Skill

*Generate automated test cases from requirements.*

## Description

This skill generates pytest-compatible test cases based on requirements described in natural language or Jira ticket descriptions.

## Inputs

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `requirements` | string | Yes | The requirement description or acceptance criteria |
| `framework` | string | No | Test framework (default: pytest) |
| `language` | string | No | Programming language (default: python) |
| `test_type` | string | No | Unit/Integration/E2E (default: unit) |

## Output

Returns a formatted test code block with:
- Test class(es) with descriptive test methods
- Proper docstrings for each test
- TODO comments for implementation details
- Common assertions based on requirements

## Examples

**Input:**
```yaml
requirements: "用户应该能够通过邮箱和密码登录系统，登录成功后跳转到仪表板"
framework: "pytest"
```

**Output:**
```python
import pytest

class TestUserLogin:
    """Test cases for user login functionality."""
    
    def test_login_with_valid_credentials(self):
        """Test successful login with valid email and password."""
        # TODO: Implement based on requirements
        # Expected: User should be authenticated and redirected to dashboard
        pass
    
    def test_login_with_invalid_password(self):
        """Test login failure with invalid password."""
        # TODO: Implement based on requirements
        pass
    
    def test_login_with_nonexistent_email(self):
        """Test login with email that doesn't exist."""
        pass
    
    def test_login_empty_fields(self):
        """Test login with empty email or password."""
        pass
```

## Usage Notes

- Generates **unit tests** by default (test individual functions/methods)
- Uses **pytest** convention (test_* method names, docstrings)
- Includes TODO comments for actual implementation
- Can be extended to support other frameworks (unittest, jest, etc.)
