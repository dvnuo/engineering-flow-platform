"""Skills Framework - Modular skill processing engine.

Reference: https://github.com/dvnuo/engineering-flow-platform/issues/169

Architecture:
- Skills are prompt-level abstractions (NOT executable code)
- Tools are executable capabilities
- LLM decides when to call tools based on skill guidance

Core Components:
- Skill Registry: Central storage of all available skills
- Execution Tracer: Audit and replay capability
- Skill Prompt Builder: Dynamic skill injection into prompts

Example Skill:
- review-pr.skill.yaml: Reviews GitHub PRs using github tools
"""

from .registry import Skill, SkillRegistry, skill_registry, load_all_skills
from .tracer import ExecutionTracer, SkillExecution, ToolCall, execution_tracer, get_tracer

__all__ = [
    # Registry
    "Skill",
    "SkillRegistry", 
    "skill_registry",
    "load_all_skills",
    # Tracer
    "ExecutionTracer",
    "SkillExecution",
    "ToolCall",
    "execution_tracer",
    "get_tracer",
]

__version__ = "1.0.0"
