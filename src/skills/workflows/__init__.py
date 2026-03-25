"""Workflow Orchestration for Skills.

This module provides step-orchestrated workflow execution for skills,
replacing the single-prompt injection approach.

Architecture:
- WorkflowExecutor: Manages multi-step execution with state
- WorkflowStep: Represents a single step in a workflow
- StepResult: Result of executing a step

Example workflow:
1. Step: Gather requirements (tool: jira_search)
2. Step: Analyze code (tool: github_code_search)
3. Step: Generate output (tool: create_file)
"""

import logging
import uuid
import json
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class StepStatus(Enum):
    """Status of a workflow step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class WorkflowStep:
    """A single step in a skill workflow.
    
    Attributes:
        id: Unique identifier for this step
        name: Human-readable step name
        description: What this step does
        tool: Tool to call for this step (optional - can use LLM reasoning)
        inputs: Expected inputs from previous steps or user
        outputs: What this step produces
        validation: Optional validation rules for step result
        required_tools: Tools this step is allowed to use
    """
    id: str
    name: str
    description: str
    tool: Optional[str] = None  # Tool to call, or None for LLM reasoning step
    inputs: List[str] = field(default_factory=list)  # Expected inputs (from context or previous steps)
    outputs: List[str] = field(default_factory=list)  # What this step produces
    validation: Optional[str] = None  # Validation rule (e.g., "result must contain X")
    required_tools: List[str] = field(default_factory=list)  # Tools allowed for this step
    prompt_template: Optional[str] = None  # Custom prompt for this step


@dataclass
class StepResult:
    """Result of executing a workflow step."""
    step_id: str
    status: StepStatus
    output: Any = None
    error: Optional[str] = None
    tool_calls: List[Dict] = field(default_factory=list)  # Tools called during this step
    duration_ms: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "tool_calls": self.tool_calls,
            "duration_ms": self.duration_ms,
        }


@dataclass
class WorkflowContext:
    """Execution context passed between steps.
    
    This holds the state that accumulates as the workflow progresses.
    """
    workflow_id: str
    skill_name: str
    user_message: str
    session_id: str
    
    # State that accumulates during execution
    step_results: Dict[str, StepResult] = field(default_factory=dict)
    shared_state: Dict[str, Any] = field(default_factory=dict)  # Arbitrary key-value state
    
    # Execution metadata
    current_step_index: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    def get_previous_step_output(self, step_id: str) -> Optional[Any]:
        """Get output from a previous step."""
        if step_id in self.step_results:
            return self.step_results[step_id].output
        return None
    
    def get_all_outputs(self) -> Dict[str, Any]:
        """Get all step outputs as a dictionary."""
        return {sid: sr.output for sid, sr in self.step_results.items() if sr.output}
    
    def is_step_completed(self, step_id: str) -> bool:
        """Check if a step has been completed."""
        return step_id in self.step_results and self.step_results[step_id].status == StepStatus.COMPLETED


class WorkflowExecutor:
    """Executes skill workflows step-by-step.
    
    This replaces the single-prompt injection approach with
    progressive step orchestration.
    
    Usage:
        executor = WorkflowExecutor()
        context = WorkflowExecutor.create_context(
            skill_name="review-pr",
            user_message="Review PR #123"
        )
        result = await executor.execute_workflow(workflow, context, llm_client)
    """
    
    def __init__(self):
        self.active_workflows: Dict[str, WorkflowContext] = {}
    
    @staticmethod
    def create_context(
        workflow_id: str,
        skill_name: str,
        user_message: str,
        session_id: str,
    ) -> WorkflowContext:
        """Create a new workflow execution context."""
        from datetime import datetime
        return WorkflowContext(
            workflow_id=workflow_id,
            skill_name=skill_name,
            user_message=user_message,
            session_id=session_id,
            started_at=datetime.utcnow().isoformat(),
        )
    
    def get_context(self, workflow_id: str) -> Optional[WorkflowContext]:
        """Get an active workflow context."""
        return self.active_workflows.get(workflow_id)
    
    def register_context(self, context: WorkflowContext):
        """Register a new workflow context."""
        self.active_workflows[context.workflow_id] = context
    
    def unregister_context(self, workflow_id: str):
        """Remove a workflow context after completion."""
        if workflow_id in self.active_workflows:
            del self.active_workflows[workflow_id]
    
    async def execute_workflow(
        self,
        workflow: List[WorkflowStep],
        context: WorkflowContext,
        llm_client: Any,
        tool_executor: Callable,
    ) -> Dict[str, Any]:
        """Execute a workflow step-by-step.
        
        Args:
            workflow: List of workflow steps
            context: Execution context
            llm_client: LLM client for reasoning steps
            tool_executor: Function to execute tools
            
        Returns:
            Final workflow result with all step results
        """
        import time
        from datetime import datetime
        
        logger.info(f"[Workflow] Starting workflow '{context.skill_name}' with {len(workflow)} steps")
        
        self.register_context(context)
        final_response = None
        
        try:
            for i, step in enumerate(workflow):
                context.current_step_index = i
                step_start = time.time()
                
                logger.info(f"[Workflow] Step {i+1}/{len(workflow)}: {step.name}")
                
                # Create step result placeholder
                step_result = StepResult(
                    step_id=step.id,
                    status=StepStatus.RUNNING,
                )
                context.step_results[step.id] = step_result
                
                try:
                    # Execute step based on type
                    if step.tool:
                        # Tool-based step
                        result = await self._execute_tool_step(
                            step, context, tool_executor
                        )
                    else:
                        # LLM reasoning step
                        result = await self._execute_llm_step(
                            step, context, llm_client
                        )
                    
                    step_result.status = StepStatus.COMPLETED
                    step_result.output = result
                    step_result.duration_ms = (time.time() - step_start) * 1000
                    
                    logger.info(f"[Workflow] Step {step.name} completed in {step_result.duration_ms:.0f}ms")
                    
                except Exception as e:
                    step_result.status = StepStatus.FAILED
                    step_result.error = str(e)
                    step_result.duration_ms = (time.time() - step_start) * 1000
                    
                    logger.error(f"[Workflow] Step {step.name} failed: {e}")
                    
                    # Check if we should continue or abort
                    # For now, we abort on failure
                    break
            
            # Build final response from step results
            final_response = self._build_workflow_response(context, workflow)
            
        finally:
            context.completed_at = datetime.utcnow().isoformat()
            self.unregister_context(context.workflow_id)
        
        return {
            "success": True,
            "workflow_id": context.workflow_id,
            "skill_name": context.skill_name,
            "step_results": {sid: sr.to_dict() for sid, sr in context.step_results.items()},
            "final_response": final_response,
            "context": {
                "started_at": context.started_at,
                "completed_at": context.completed_at,
            }
        }
    
    async def _execute_tool_step(
        self,
        step: WorkflowStep,
        context: WorkflowContext,
        tool_executor: Callable,
    ) -> Any:
        """Execute a tool-based step."""
        if not step.tool:
            raise ValueError(f"Step {step.id} has no tool specified")
        
        # Prepare tool arguments from previous steps
        tool_args = {}
        for input_name in step.inputs:
            # Try to get from shared state first
            if input_name in context.shared_state:
                tool_args[input_name] = context.shared_state[input_name]
            else:
                # Try to get from previous step outputs
                for prev_step_id, prev_result in context.step_results.items():
                    if prev_result.output and isinstance(prev_result.output, dict):
                        if input_name in prev_result.output:
                            tool_args[input_name] = prev_result.output[input_name]
        
        # Execute the tool
        logger.debug(f"[Workflow] Calling tool {step.tool} with args: {tool_args}")
        result = await tool_executor(step.tool, tool_args)
        
        # Update shared state with outputs
        for output_name in step.outputs:
            if isinstance(result, dict) and output_name in result:
                context.shared_state[output_name] = result[output_name]
            elif hasattr(result, output_name):
                context.shared_state[output_name] = getattr(result, output_name)
        
        return result
    
    async def _execute_llm_step(
        self,
        step: WorkflowStep,
        context: WorkflowContext,
        llm_client: Any,
    ) -> Any:
        """Execute an LLM reasoning step."""
        # Build prompt for this step
        prompt = self._build_step_prompt(step, context)
        
        # Get allowed tools for this step
        allowed_tools = step.required_tools if step.required_tools else None
        
        # Call LLM
        logger.debug(f"[Workflow] LLM reasoning for step {step.id}")
        
        llm_result = await llm_client.generate(
            prompt=prompt,
            allowed_tools=allowed_tools,
        )
        
        # Parse LLM response
        content = llm_result.get("content", "")
        
        # Update shared state if LLM provided structured output
        try:
            if content.startswith("{") or content.startswith("["):
                structured = json.loads(content)
                if isinstance(structured, dict):
                    context.shared_state.update(structured)
        except json.JSONDecodeError:
            pass
        
        return content
    
    def _build_step_prompt(self, step: WorkflowStep, context: WorkflowContext) -> str:
        """Build prompt for an LLM reasoning step."""
        parts = [
            f"Skill: {context.skill_name}",
            f"Current step: {step.name}",
            f"Step description: {step.description}",
            "",
        ]
        
        # Add context from previous steps
        if context.step_results:
            parts.append("Previous step results:")
            for sid, result in context.step_results.items():
                if result.output:
                    parts.append(f"  - {sid}: {result.output}")
            parts.append("")
        
        # Add custom prompt if provided
        if step.prompt_template:
            parts.append(step.prompt_template)
        else:
            # Default prompt
            parts.append(f"Based on the previous results, {step.description.lower()}")
            parts.append("")
            parts.append("Provide your reasoning and any output in structured format.")
        
        return "\n".join(parts)
    
    def _build_workflow_response(
        self,
        context: WorkflowContext,
        workflow: List[WorkflowStep],
    ) -> str:
        """Build final response from workflow results."""
        # Collect all step outputs
        outputs = []
        for step in workflow:
            if step.id in context.step_results:
                result = context.step_results[step.id]
                if result.status == StepStatus.COMPLETED and result.output:
                    outputs.append(f"## {step.name}\n{result.output}")
        
        return "\n\n".join(outputs)


def parse_skill_as_workflow(skill_data: Dict) -> List[WorkflowStep]:
    """Parse skill YAML/JSON into workflow steps.
    
    This allows skills to define workflow structure instead of
    just prompt injection.
    
    Expected format in skill YAML:
        workflow:
          - id: step_1
            name: Gather Info
            description: Gather required information
            tool: jira_search
            inputs: [query]
            outputs: [results]
          - id: step_2
            name: Analyze
            description: Analyze the results
            required_tools: [github_search]
    """
    workflow_config = skill_data.get("workflow", [])
    
    if not workflow_config:
        # No workflow defined - return empty, fallback to prompt injection
        return []
    
    steps = []
    for i, step_def in enumerate(workflow_config):
        step = WorkflowStep(
            id=step_def.get("id", f"step_{i+1}"),
            name=step_def.get("name", f"Step {i+1}"),
            description=step_def.get("description", ""),
            tool=step_def.get("tool"),
            inputs=step_def.get("inputs", []),
            outputs=step_def.get("outputs", []),
            validation=step_def.get("validation"),
            required_tools=step_def.get("required_tools", []),
            prompt_template=step_def.get("prompt_template"),
        )
        steps.append(step)
    
    return steps


# Global workflow executor instance
workflow_executor = WorkflowExecutor()


def get_workflow_executor() -> WorkflowExecutor:
    """Get the global workflow executor."""
    return workflow_executor
