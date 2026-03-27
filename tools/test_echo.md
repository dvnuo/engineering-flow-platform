# Test Tool: echo

This tool returns the input arguments for testing purposes.

## Tool Schema

```yaml
name: test_echo
description: Echo back the input arguments. For testing tool execution.
parameters:
  type: object
  properties:
    message:
      type: string
      description: Message to echo back
    data:
      type: string
      description: Additional data to include
  required:
    - message
```

## Usage

Use this tool to verify that tool calling works correctly in workflow steps.

## Implementation

Located in `src/tools/__init__.py` as `test_echo` function.
