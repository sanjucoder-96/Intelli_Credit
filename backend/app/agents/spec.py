"""Agent configuration models (ARCHITECTURE §5).

An :class:`AgentSpec` is registered data, not a code fork: most platform
agents are configured instances of a generic implementation. Phase 5 binds
``system_prompt_ref``/``output_schema_ref`` to the prompt and schema
registries; in Phase 2 they are carried but unused.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelPolicy(BaseModel):
    """Which model tier an agent uses and when it escalates (ADR-0005).

    Defaults track the current Gemini generation (see
    :class:`app.core.config.GeminiConfig`); S3.4 constructs the caller's policy
    from config so a per-environment model override flows through.
    """

    primary: str = "gemini-3.7-flash"
    escalate_to: str | None = "gemini-3.1-pro-preview"
    temperature: float = 0.2
    thinking_budget: int | None = None


class RetryPolicy(BaseModel):
    """Typed retry budgets per failure class (ARCHITECTURE §5)."""

    transient_max: int = 3
    repair_max: int = 2
    regen_max: int = 1
    tool_retry: int = 1


class ReflectionPolicy(BaseModel):
    """Bounded self-critique configuration; unbounded loops are banned."""

    enabled: bool = False  # off by default; high-stakes synthesis agents turn it on
    critic_model: str = "gemini-2.5-flash"
    max_revisions: int = 1
    rubric_ref: str = "default"


class StepBudget(BaseModel):
    """Hard per-node resource caps enforced by :class:`~app.agents.budget.BudgetMeter`."""

    max_tool_calls: int = 12
    max_llm_calls: int = 8
    max_cost_usd: float = 0.50


def _default_confidence_weights() -> dict[str, float]:
    """ARCHITECTURE §5 default component weights for the confidence formula.

    All six measured components carry weight by default — in particular
    ``validators``, so a failing deterministic-validator suite sinks the
    score the moment Phase 5 wires real validators (components an agent
    doesn't measure are omitted with weight renormalization).
    """
    return {
        "schema": 0.10,
        "critic": 0.20,
        "validators": 0.15,
        "evidence": 0.30,
        "source": 0.15,
        "tool": 0.10,
    }


class AgentSpec(BaseModel):
    """Registered definition of one agent — configuration, not code."""

    name: str
    impl: str = "generic_llm"
    system_prompt_ref: str = ""
    output_schema_ref: str = ""
    tools: list[str] = Field(default_factory=list)
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    reflection: ReflectionPolicy = Field(default_factory=ReflectionPolicy)
    confidence_weights: dict[str, float] = Field(default_factory=_default_confidence_weights)
    confidence_floor: float = 0.6
    budget: StepBudget = Field(default_factory=StepBudget)
