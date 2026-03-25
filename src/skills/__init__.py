"""Skills Framework - Modular skill processing engine.

Architecture:
- Skills are prompt-level abstractions (NOT executable code)
- Tools are executable capabilities
- LLM decides when to call tools based on skill guidance

Core Components:
- Skill Registry: Central storage of all available skills
- Execution Tracer: Audit and replay capability
- Skill Prompt Builder: Dynamic skill injection into prompts
- Workflow Executor: Step-orchestrated execution (NEW - Issue #362)

Example Skill:
- review-pr.skill.yaml: Reviews GitHub PRs using github tools
"""

from .registry import Skill, SkillStep, SkillRegistry, skill_registry, load_all_skills
from .tracer import ExecutionTracer, SkillExecution, ToolCall, execution_tracer, get_tracer
from .workflows import (
    WorkflowExecutor,
    WorkflowStep,
    StepResult,
    WorkflowContext,
    StepStatus,
    workflow_executor,
    get_workflow_executor,
    parse_skill_as_workflow,
)

__all__ = [
    # Registry
    "Skill",
    "SkillStep",
    "SkillRegistry", 
    "skill_registry",
    "load_all_skills",
    # Tracer
    "ExecutionTracer",
    "SkillExecution",
    "ToolCall",
    "execution_tracer",
    "get_tracer",
    # Workflow (NEW - Issue #362)
    "WorkflowExecutor",
    "WorkflowStep",
    "StepResult",
    "WorkflowContext",
    "StepStatus",
    "workflow_executor",
    "get_workflow_executor",
    "parse_skill_as_workflow",
]

__version__ = "2.0.0"
