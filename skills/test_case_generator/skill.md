---
name: test-case-generator
description: Generate automated test cases from requirements (Issue #362 example)
version: 1.1.0
owner: devops-team
triggers:
  - create tests
  - generate test cases
  - write tests
  - test case
tools: []
output_format: json
steps:
  - id: extract_requirements
    title: Extract Requirements
    objective: Extract testable requirements from user input
    instructions:
      - Analyze the user's request carefully
      - Identify explicit requirements mentioned
      - Identify implicit requirements from context
      - List all testable scenarios
      - Do NOT generate test code in this step
    allowed_tools: []
    references: []
    completion_check:
      - artifacts.requirements exists
      - summary is not empty
    next_step: generate_tests

  - id: generate_tests
    title: Generate Test Code
    objective: Generate pytest-compatible test code based on requirements
    instructions:
      - Use the extracted requirements from previous step
      - Generate readable, well-structured pytest tests
      - Include docstrings for each test
      - Use descriptive test names (test_<feature>_<scenario>)
      - Follow pytest best practices
    allowed_tools: []
    references: []
    completion_check:
      - artifacts.test_code exists
    next_step: finalize

  - id: finalize
    title: Finalize Response
    objective: Prepare the final user-facing response
    instructions:
      - Summarize what was generated
      - Include the complete test code in a code block
      - Provide any usage instructions
    allowed_tools: []
    references: []
    completion_check:
      - summary is not empty
    next_step: null
---

# Test Case Generator

**Capability**: Generate automated test cases from requirements using step-orchestrated workflow (Issue #362).

## How It Works

This skill demonstrates step-based execution (Issue #362).

### Step 1: Extract Requirements
- Analyzes user input
- Identifies testable requirements
- Outputs structured requirements list

### Step 2: Generate Tests
- Generates pytest-compatible test code
- Based on extracted requirements

### Step 3: Finalize
- Prepares final response
- Formats code properly

## Usage

Just ask naturally:
- "create tests for user login"
- "generate test cases for the payment module"
- "write unit tests for user authentication"

## Step Output Format

Each step returns structured JSON:
```json
{
  "status": "success",
  "summary": "Description of what was done",
  "artifacts": {
    "requirements": ["list of requirements"],
    "test_code": "generated pytest code"
  },
  "next_step": "next_step_id"
}
```

## Example Response

After all steps complete:
```
## Summary
Generated 5 test cases for user login functionality.

## Test Code
```python
import pytest

class TestUserLogin:
    """Test cases for user login functionality."""
    
    def test_login_success(self):
        """Test successful login with valid credentials."""
        pass
    
    def test_login_invalid_password(self):
        """Test login fails with invalid password."""
        pass
```
```
