"""Agent tool registry, tool belt, and the concrete tools (ARCHITECTURE §5).

Tools are the only side-effecting capabilities an agent has, and the
workflow definition whitelists them per agent (ADR-0008): a compliance agent
physically cannot call the web because ``web_search`` is not on its belt.
Every use charges the step budget and records a trace event, so tool spend is
bounded and auditable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.agents.context import TraceEvent
from app.core.errors import ForbiddenError, NotFoundError
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.agents.context import AgentContext

logger = get_logger("app.agents.tools")


class ToolResult(BaseModel):
    """Uniform tool outcome (agents branch on ``ok``, never on exceptions)."""

    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class Tool(Protocol):
    """A named, schema-validated capability."""

    name: str
    args_schema: type[BaseModel]
    side_effects: bool

    async def run(self, ctx: AgentContext, args: BaseModel) -> ToolResult: ...


class ToolRegistry:
    """Process-wide tool catalogue (one of the nine extension registries)."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise NotFoundError(f"Tool '{name}' is not registered")
        return tool

    def names(self) -> list[str]:
        return sorted(self._tools)


tool_registry = ToolRegistry()


class ToolBelt:
    """The whitelisted subset of tools available to one agent at one node.

    Tracks call/success counters for the confidence ``tool`` component.
    """

    def __init__(self, registry: ToolRegistry, allowed: list[str]) -> None:
        self._registry = registry
        self._allowed = frozenset(allowed)
        self.calls = 0
        self.successes = 0

    def can_use(self, name: str) -> bool:
        return name in self._allowed

    def success_ratio(self) -> float | None:
        return self.successes / self.calls if self.calls else None

    async def use(self, ctx: AgentContext, name: str, args: dict[str, Any]) -> ToolResult:
        """Validate, authorize, budget, trace, and run a tool call."""
        if name not in self._allowed:
            raise ForbiddenError(
                f"Agent is not permitted to use tool '{name}'",
                details={"allowed": sorted(self._allowed)},
            )
        tool = self._registry.get(name)
        self.calls += 1
        ctx.budget.charge_tool()
        try:
            validated = tool.args_schema.model_validate(args)
            result = await tool.run(ctx, validated)
        except Exception as exc:  # tool failures are data, not crashes
            logger.warning("tool_failed", tool=name, error=str(exc))
            result = ToolResult(ok=False, error=str(exc)[:300])
        if result.ok:
            self.successes += 1
        await ctx.trace.record(
            TraceEvent(
                node=ctx.node,
                agent="",
                seq=self.calls,
                kind="tool_call",
                payload={"tool": name, "ok": result.ok},
            )
        )
        return result


# --------------------------------------------------------------------- tools
class HybridSearchArgs(BaseModel):
    query: str
    doc_types: list[str] | None = None
    top_k: int = Field(default=8, ge=1, le=30)


class HybridSearchTool:
    """Case-scoped hybrid retrieval over the evidence corpus (ARCHITECTURE §8)."""

    name = "hybrid_search"
    args_schema: type[BaseModel] = HybridSearchArgs
    side_effects = False

    async def run(self, ctx: AgentContext, args: BaseModel) -> ToolResult:
        import asyncio

        from app.infrastructure.db.retrieval import HybridRetriever

        assert isinstance(args, HybridSearchArgs)
        if ctx.session_factory is None or ctx.tenant is None or ctx.embedder is None:
            return ToolResult(ok=False, error="retrieval context unavailable")
        vector = await asyncio.to_thread(ctx.embedder.embed, [args.query])
        async with ctx.session_factory() as session:
            hits = await HybridRetriever(session).retrieve(
                org_id=ctx.tenant.org_id,
                query_text=args.query,
                query_vector=vector[0],
                loan_application_id=ctx.case_id,
                doc_types=args.doc_types,
                top_k=args.top_k,
            )
        return ToolResult(
            ok=True,
            data={
                "hits": [
                    {
                        "chunk_id": str(hit.chunk_id),
                        "document_id": str(hit.document_id),
                        "doc_type": hit.doc_type,
                        "text": hit.text,
                        "page": hit.page_start,
                        "rrf_score": hit.rrf_score,
                    }
                    for hit in hits
                ]
            },
        )


class WebSearchArgs(BaseModel):
    query: str
    max_results: int = Field(default=5, ge=1, le=10)


class WebSearchTool:
    """Budgeted web research via Tavily, with source snapshotting (§6)."""

    name = "web_search"
    args_schema: type[BaseModel] = WebSearchArgs
    side_effects = True  # network egress + snapshot writes

    async def run(self, ctx: AgentContext, args: BaseModel) -> ToolResult:
        assert isinstance(args, WebSearchArgs)
        if ctx.research is None:
            return ToolResult(ok=False, error="research client unavailable")
        response = await ctx.research.search(args.query, max_results=args.max_results)
        return ToolResult(
            ok=response.configured,
            data={
                "configured": response.configured,
                "results": [
                    {
                        "title": result.title,
                        "url": result.url,
                        "content": result.content,
                        "snapshot_key": result.snapshot_key,
                        "score": result.score,
                    }
                    for result in response.results
                ],
            },
            error=None if response.configured else "web research is not configured",
        )


tool_registry.register(HybridSearchTool())
tool_registry.register(WebSearchTool())


def build_tool_belt(allowed: list[str], registry: ToolRegistry | None = None) -> ToolBelt:
    """Construct a belt over the given whitelist (compiler calls this)."""
    return ToolBelt(registry or tool_registry, allowed)


__all__ = [
    "HybridSearchTool",
    "Tool",
    "ToolBelt",
    "ToolRegistry",
    "ToolResult",
    "WebSearchTool",
    "build_tool_belt",
    "tool_registry",
]


_ = UUID  # re-exported for tool arg schemas in later phases
