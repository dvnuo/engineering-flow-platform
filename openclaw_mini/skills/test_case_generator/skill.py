"""Test Case Generator Skill for OpenClaw Mini."""

import re
from typing import Any, Dict

from openclaw_mini.agent.llm import llm_client


class TestCaseSkill:
    """Generate automated test cases from requirements."""

    def __init__(self):
        self.name = "test_case_generator"
        self.description = "Generate pytest test cases from requirements"

    async def generate(
        self,
        requirements: str,
        framework: str = "pytest",
        language: str = "python",
        test_type: str = "unit",
    ) -> str:
        """Generate test cases based on requirements.
        
        Args:
            requirements: Description of the feature/requirements
            framework: Test framework (pytest, unittest, etc.)
            language: Programming language
            test_type: Type of tests (unit, integration, e2e)
            
        Returns:
            Generated test code as string
        """
        # Build prompt for LLM
        system_prompt = """You are a QA Engineer specializing in generating automated test cases.
Your task is to generate comprehensive, well-structured test cases based on given requirements.

Guidelines:
1. Generate pytest-compatible test code
2. Use descriptive test method names (snake_case, test_* prefix)
3. Add clear docstrings for each test
4. Include TODO comments for actual implementation
5. Cover both positive and negative test scenarios
6. Follow Arrange-Act-Assert pattern where applicable

Output only the test code, no explanations."""

        user_prompt = f"""Generate test cases for the following requirements:

## Requirements
{requirements}

## Framework: {framework}
## Language: {language}
## Test Type: {test_type}

Generate a complete Python test file with:
1. Necessary imports
2. Test class(es) if applicable
3. All relevant test methods with docstrings
4. TODO comments for implementation details

Focus on edge cases and error conditions."""

        try:
            response = await llm_client.complete(
                prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=2000,
                temperature=0.3,
            )
            return response
        except Exception as e:
            return f"# Error generating test cases: {e}"

    def parse_requirements_from_jira(self, description: str) -> str:
        """Parse and clean requirements from Jira description (ADF format)."""
        # Remove ADF formatting and extract text
        if isinstance(description, dict):
            content = description.get("content", [])
            text_parts = []
            for block in content:
                if block.get("type") == "paragraph":
                    for item in block.get("content", []):
                        if item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
            return " ".join(text_parts)
        return description


# Global skill instance
test_case_skill = TestCaseSkill()
