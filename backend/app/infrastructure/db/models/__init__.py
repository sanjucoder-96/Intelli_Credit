"""ORM models — importing this package populates ``Base.metadata`` fully.

The Alembic env and metadata tests rely on this package importing every
model module. 17 tables shipped in Phase 2; Phase 3 adds chunks and
memory_items (pgvector + generated tsvector), document_stages (ingestion
checkpoints), findings (append-only supersession), scorecard_configs /
score_breakdowns (ADR-0009), and agent_execution_payloads (ADR-0012) —
24 tables total.
"""

from app.infrastructure.db.models.agents import (
    AgentExecution,
    AgentExecutionPayload,
    LLMInteraction,
)
from app.infrastructure.db.models.cases import LoanApplication
from app.infrastructure.db.models.chunks import EMBEDDING_DIM, Chunk
from app.infrastructure.db.models.companies import Company
from app.infrastructure.db.models.documents import Document, DocumentStage, ExtractedField
from app.infrastructure.db.models.evidence import Evidence, ResearchSnapshot
from app.infrastructure.db.models.findings import Finding
from app.infrastructure.db.models.identity import Organization, User
from app.infrastructure.db.models.memory import MemoryItem
from app.infrastructure.db.models.platform import AuditLog, FeatureFlag, ModelRegistry
from app.infrastructure.db.models.reports import Report
from app.infrastructure.db.models.risk import RiskAssessment, ScoreBreakdown, ScorecardConfig
from app.infrastructure.db.models.workflows import Workflow, WorkflowExecution

__all__ = [
    "EMBEDDING_DIM",
    "AgentExecution",
    "AgentExecutionPayload",
    "AuditLog",
    "Chunk",
    "Company",
    "Document",
    "DocumentStage",
    "Evidence",
    "ExtractedField",
    "FeatureFlag",
    "Finding",
    "LLMInteraction",
    "LoanApplication",
    "MemoryItem",
    "ModelRegistry",
    "Organization",
    "Report",
    "ResearchSnapshot",
    "RiskAssessment",
    "ScoreBreakdown",
    "ScorecardConfig",
    "User",
    "Workflow",
    "WorkflowExecution",
]
