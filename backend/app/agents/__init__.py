"""Agent runtime skeleton (ARCHITECTURE §5, ADR-0003).

One ``BaseAgent`` lifecycle serves all twelve platform agents. Phase 2 ships
the runtime contract only — planning/acting against Gemini, deterministic
validators, and the rubric critic land in Phase 5 behind these seams.
"""

from app.agents.base import BaseAgent, Plan, PlannedStep
from app.agents.budget import BudgetExceededError, BudgetMeter
from app.agents.context import AgentContext, LoggingTraceWriter, TraceEvent, TraceWriterPort
from app.agents.registry import AgentRegistry, agent_registry
from app.agents.results import AgentResult, ConfidenceReport, Critique, compute_confidence
from app.agents.spec import AgentSpec, ModelPolicy, ReflectionPolicy, RetryPolicy, StepBudget

__all__ = [
    "AgentContext",
    "AgentRegistry",
    "AgentResult",
    "AgentSpec",
    "BaseAgent",
    "BudgetExceededError",
    "BudgetMeter",
    "ConfidenceReport",
    "Critique",
    "LoggingTraceWriter",
    "ModelPolicy",
    "Plan",
    "PlannedStep",
    "ReflectionPolicy",
    "RetryPolicy",
    "StepBudget",
    "TraceEvent",
    "TraceWriterPort",
    "agent_registry",
    "compute_confidence",
]
