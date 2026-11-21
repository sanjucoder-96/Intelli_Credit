"""Canonical enums shared by the domain, ORM, and API layers.

Stored in Postgres as VARCHAR check-constrained values (``native_enum=False``)
so adding a member is an application deploy, not a database migration.
Phase 5/6 extend these with workflow- and scoring-specific members where the
docstrings note it.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """User roles; map directly to HITL gates (ADR-0013)."""

    ANALYST = "analyst"
    CREDIT_OFFICER = "credit_officer"
    ADMIN = "admin"


class Permission(StrEnum):
    """Fine-grained permissions granted through roles."""

    CASE_READ = "case_read"
    CASE_WRITE = "case_write"
    DOCUMENT_UPLOAD = "document_upload"
    RUN_START = "run_start"
    HITL_REVIEW = "hitl_review"
    REPORT_READ = "report_read"
    FLAGS_MANAGE = "flags_manage"
    USERS_MANAGE = "users_manage"
    MODELS_MANAGE = "models_manage"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.CREDIT_OFFICER: frozenset(
        {Permission.CASE_READ, Permission.HITL_REVIEW, Permission.REPORT_READ}
    ),
    Role.ANALYST: frozenset(
        {
            Permission.CASE_READ,
            Permission.CASE_WRITE,
            Permission.DOCUMENT_UPLOAD,
            Permission.RUN_START,
            Permission.REPORT_READ,
        }
    ),
}
"""Role → permission grants; the single source of truth for authorization."""


class DocumentStatus(StrEnum):
    """Lifecycle of an uploaded document through the ingestion pipeline."""

    UPLOADED = "uploaded"
    PROCESSING = "processing"
    NEEDS_PASSWORD = "needs_password"
    READY = "ready"
    FAILED = "failed"


class LoanApplicationStatus(StrEnum):
    """Lifecycle of a loan application (the ARCHITECTURE 'Case' aggregate)."""

    DRAFT = "draft"
    INGESTING = "ingesting"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    DECIDED = "decided"
    ARCHIVED = "archived"


class WorkflowExecutionStatus(StrEnum):
    """Lifecycle of one workflow run (the ARCHITECTURE 'WorkflowRun')."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED_HITL = "paused_hitl"
    NEEDS_ATTENTION = "needs_attention"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentExecutionKind(StrEnum):
    """Kind of a single evidence-ledger trace event (ADR-0012)."""

    PLAN = "plan"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    REFLECT = "reflect"
    REVISE = "revise"
    FINAL = "final"
    ERROR = "error"


class ExecutionStatus(StrEnum):
    """Outcome status of an agent step or LLM interaction."""

    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class EvidenceKind(StrEnum):
    """Provenance class of a piece of evidence (ARCHITECTURE §8)."""

    DOCUMENT = "document"
    WEB = "web"
    PRIMARY = "primary"
    DERIVED = "derived"


class Decision(StrEnum):
    """Terminal credit decision produced by the risk engine (ADR-0009)."""

    APPROVE = "approve"
    REFER = "refer"
    DECLINE = "decline"


class DataOrigin(StrEnum):
    """Real vs synthetic-demo data separation (ADR-0010)."""

    REAL = "real"
    SYNTHETIC_DEMO = "synthetic_demo"


class ModelStage(StrEnum):
    """ML model registry lifecycle stage (ADR-0010)."""

    CANDIDATE = "candidate"
    CHAMPION = "champion"
    ARCHIVED = "archived"


class FlagScope(StrEnum):
    """Feature-flag scoping level (ADR-0015)."""

    GLOBAL = "global"
    ORG = "org"
    WORKFLOW = "workflow"


class ReportStatus(StrEnum):
    """Lifecycle of a generated report artifact (ADR-0011)."""

    PENDING = "pending"
    RENDERED = "rendered"
    FAILED = "failed"


class IngestStage(StrEnum):
    """Stages of the per-document ingestion chain (ARCHITECTURE §7).

    Each (document, stage) pair is checkpointed in ``document_stages`` so
    Celery's at-least-once delivery re-runs no-op instead of double-writing.
    The Phase-4 pipeline executes these in order.
    """

    DETECT_TYPE = "detect_type"
    EXTRACT_TEXT_LAYER = "extract_text_layer"
    OCR_PAGES = "ocr_pages"
    LAYOUT_TABLES = "layout_tables"
    CLASSIFY = "classify"
    EXTRACT_DOMAIN = "extract_domain"
    CHUNK = "chunk"
    EMBED_INDEX = "embed_index"
    CROSS_VALIDATE = "cross_validate"


class StageStatus(StrEnum):
    """Status of one ingestion-stage checkpoint."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class FindingKind(StrEnum):
    """What produced a finding (ARCHITECTURE §5/§7)."""

    EXTRACTION = "extraction"
    RESEARCH = "research"
    FRAUD = "fraud"
    VALIDATION = "validation"
    PRIMARY = "primary"  # credit-officer insight via HITL — source tier 1.0
    GAP = "gap"  # a non-required node failed; the report must say so
    DERIVED = "derived"


class RiskCategory(StrEnum):
    """The six-category risk taxonomy reports print (ARCHITECTURE §9)."""

    BUSINESS = "business"
    FINANCIAL = "financial"
    OPERATIONAL = "operational"
    LEGAL = "legal"
    REPUTATIONAL = "reputational"
    MACRO = "macro"


class Severity(StrEnum):
    """Severity scale shared by findings and fraud signals."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
