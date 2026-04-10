"""Runtime execution package."""

from typing import Any


def build_default_execution_bus(*args: Any, **kwargs: Any):
    from src.runtime.execution_bus import build_default_execution_bus as _build_default_execution_bus

    return _build_default_execution_bus(*args, **kwargs)


from src.runtime.contracts import (
    ExecutionRequest,
    ExecutionResult,
    make_execution_request,
    make_execution_result,
)
from src.runtime.capability_registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    DefaultCapabilityRegistry,
    build_default_capability_registry,
    capability_registry,
    get_capability_registry,
)
from src.runtime.events import build_runtime_event, normalize_event_payload
from src.runtime.governance import GovernanceHooks
from src.runtime.governance_bus import (
    DefaultGovernanceBus,
    GovernanceAuditRecord,
    GovernanceBus,
    GovernanceDecision,
    build_default_governance_bus,
)
from src.runtime.recovery_pipeline import (
    DefaultRecoveryPipeline,
    RecoveryHydrationResult,
    RecoveryPipeline,
    RecoverySnapshot,
    build_default_recovery_pipeline,
    get_recovery_pipeline,
    recovery_pipeline,
)

__all__ = [
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionBus",
    "CapabilityDescriptor",
    "CapabilityRegistry",
    "DefaultCapabilityRegistry",
    "build_default_capability_registry",
    "get_capability_registry",
    "capability_registry",
    "GovernanceHooks",
    "GovernanceBus",
    "GovernanceDecision",
    "GovernanceAuditRecord",
    "DefaultGovernanceBus",
    "RecoverySnapshot",
    "RecoveryHydrationResult",
    "RecoveryPipeline",
    "DefaultRecoveryPipeline",
    "get_recovery_pipeline",
    "recovery_pipeline",
    "make_execution_request",
    "make_execution_result",
    "build_default_execution_bus",
    "build_default_governance_bus",
    "build_default_recovery_pipeline",
    "build_runtime_event",
    "normalize_event_payload",
]
def __getattr__(name: str) -> Any:
    if name == "ExecutionBus":
        from src.runtime.execution_bus import ExecutionBus as _ExecutionBus

        return _ExecutionBus
    if name == "build_default_execution_bus":
        return build_default_execution_bus
    raise AttributeError(name)
