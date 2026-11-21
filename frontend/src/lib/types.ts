/**
 * TypeScript projections of the backend API contracts.
 *
 * Mirrors the FastAPI response schemas (app/api/schemas/*). Every quantitative
 * field originates in a backend row — the frontend renders these, never
 * recomputes them (ARCHITECTURE invariants #2/#3).
 */

export type RunStatus =
  | "pending"
  | "running"
  | "paused_hitl"
  | "needs_attention"
  | "completed"
  | "failed"
  | "cancelled";

export type Decision = "approve" | "refer" | "decline";
export type Severity = "info" | "low" | "medium" | "high" | "critical";

export interface Company {
  id: string;
  legal_name: string;
  cin: string | null;
  gstin: string | null;
  pan: string | null;
  sector: string | null;
  created_at: string;
}

export interface Case {
  id: string;
  company_id: string;
  title: string;
  requested_amount: string | null;
  currency: string;
  status: string;
  created_at: string;
}

export interface StageStatus {
  stage: string;
  status: string;
  attempt: number;
  error: string | null;
}

export interface DocumentRow {
  id: string;
  filename: string;
  media_type: string | null;
  doc_type: string | null;
  doc_type_confidence: number | null;
  status: string;
  page_count: number | null;
  sha256: string | null;
  created_at: string;
  stages: StageStatus[];
}

// --- workflow / risk ------------------------------------------------------
export interface RunSummary {
  run_id: string;
  workflow_key: string;
  status: RunStatus;
  started_at: string | null;
  finished_at: string | null;
  cost_usd: number;
  grade: string | null;
  decision: Decision | null;
  final_score: number | null;
  coverage: number | null;
  recommended_limit: string | null;
  interest_rate_pct: number | null;
  pd: number | null;
  non_production_ml: boolean;
  report_id: string | null;
  execution_mode?: string; // LIVE | REPLAY | DEGRADED
}

export interface RiskFactor {
  pillar_id: string;
  factor_id: string;
  raw_value: number | null;
  normalized: number | null;
  weight: number;
  contribution: number;
  admitted: boolean;
  exclusion_reason: string | null;
  explanation: string;
}

export interface Overlay {
  rule_id: string;
  fired: boolean;
  effect: string;
  detail: string;
}

export interface ReasonCode {
  feature?: string;
  label: string;
  value?: number | null;
  present?: boolean;
  shap_log_odds: number;
  direction: "increases" | "decreases";
}

export interface MlPrediction {
  available: boolean;
  pd: number | null;
  pd_raw: number | null;
  coverage: number;
  model_name: string | null;
  model_version: string | null;
  algorithm: string | null;
  feature_schema_version: string | null;
  non_production: boolean;
  top_risk_factors: ReasonCode[];
  top_mitigants: ReasonCode[];
  note: string;
}

export interface Assessment {
  scorecard_key: string;
  scorecard_version: string;
  base_score: number | null;
  final_score: number | null;
  grade: string | null;
  decision: Decision | null;
  coverage: number | null;
  factors: RiskFactor[];
  overlays: Overlay[];
  limit: {
    amount?: string | null;
    binding_constraint?: string | null;
    arithmetic?: string;
  };
  pricing: {
    rate_pct?: number | null;
    base_rate_pct?: number;
    grade_premium_pct?: number;
    adjustments?: [string, number][];
  };
  notes: string[];
  ml: MlPrediction;
}

export interface NodeStatus {
  node: string;
  agent: string | null;
  status: "ok" | "error" | "running" | "pending";
  seq: number | null;
}

export interface RunDetail {
  run_id: string;
  workflow_key: string;
  status: RunStatus;
  cost_usd: number;
  started_at: string | null;
  finished_at: string | null;
  error: Record<string, unknown> | null;
  report_id: string | null;
  assessment: Assessment | null;
  nodes: NodeStatus[];
}

export interface RunStatusResponse {
  run_id: string;
  workflow_key: string;
  status: RunStatus;
  cost_usd: number;
  grade: string | null;
  decision: Decision | null;
  report_id: string | null;
  error: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
}

// --- findings / evidence --------------------------------------------------
export interface Finding {
  id: string;
  agent: string | null;
  kind: string;
  category: string | null;
  severity: Severity;
  title: string;
  narrative: string | null;
  data: Record<string, unknown> | null;
  confidence: number | null;
  source_tier: number | null;
  created_at: string;
}

export interface EvidenceItem {
  id: string;
  kind: string;
  claim: string;
  document_id: string | null;
  page: number | null;
  bboxes: Record<string, unknown> | null;
  url: string | null;
  quote: string | null;
  source_tier: number | null;
  confidence: number | null;
}

export interface CaseFindings {
  findings: Finding[];
  evidence: EvidenceItem[];
}

export interface ExtractedFieldView {
  id: string;
  document_id: string;
  schema_id: string;
  field_path: string;
  value: unknown;
  confidence: number | null;
  page: number | null;
  bbox: {
    page?: number | null;
    bboxes?: { x0: number; y0: number; x1: number; y1: number }[];
    quote?: string | null;
  } | null;
}

export interface FinancialSource {
  field: string;
  value: string;
  document_id: string;
  filename: string | null;
  page: number | null;
  confidence: number | null;
}

export interface FinancialsAssembly {
  available: boolean;
  fiscal_year: string | null;
  financials: Record<string, string>;
  sources: FinancialSource[];
}

// --- CAM report -----------------------------------------------------------
export interface CamFinding {
  title: string;
  severity: string;
  category: string | null;
  narrative: string | null;
  evidence_ids: string[];
}

export interface CreditC {
  name: string;
  score: number | null;
  drivers: string[];
}

export interface CamReport {
  company: { legal_name: string; cin?: string | null; gstin?: string | null; sector?: string | null };
  requested_amount: string | null;
  currency: string;
  executive_summary: string;
  grade: string;
  decision: string;
  final_score: number;
  coverage: number;
  recommended_limit: string | null;
  limit_basis: string | null;
  interest_rate_pct: number | null;
  rate_build_up: string;
  five_cs: { character?: CreditC; capacity?: CreditC; capital?: CreditC; collateral?: CreditC; conditions?: CreditC } & Record<string, CreditC>;
  ratios: Record<string, { value: number | null; explanation: string; available: boolean }>;
  findings: CamFinding[];
  score_breakdown: RiskFactor[];
  overlay_log: Overlay[];
  notes: string[];
}

export interface RunReport {
  report_id: string;
  report_type: string;
  grade: string | null;
  decision: string | null;
  markdown: string;
  ir: CamReport;
}

// --- dashboard ------------------------------------------------------------
export interface RecentDecision {
  case_id: string;
  company_legal_name: string;
  run_id: string;
  grade: string | null;
  decision: Decision | null;
  final_score: number | null;
  finished_at: string | null;
}

export interface DashboardSummary {
  active_cases: number;
  awaiting_review: number;
  completed_runs: number;
  running: number;
  failed: number;
  recent_decisions: RecentDecision[];
  grade_distribution: { grade: string; count: number }[];
  decision_distribution: Record<string, number>;
}
