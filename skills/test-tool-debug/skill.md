name: test-tool-debug
description: Debug skill to test tool calling in workflow steps
triggers:
  - /test-tool-debug
  - /test_tool_debug

steps:
  - id: echo_test
    title: Echo Test
    type: llm
    objective: Call the test_echo tool to verify tool execution works
    instructions: |
      Use the test_echo tool with message="hello" and data="test123" to verify tool calling works.
    allowed_tools:
      - test_echo
    completion_check:
      - status exists
      - summary exists
    next_step: null
