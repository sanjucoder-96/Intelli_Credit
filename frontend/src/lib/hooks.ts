"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import { apiGet, apiPost, apiUpload } from "@/lib/api";
import type {
  Case,
  CaseFindings,
  Company,
  DashboardSummary,
  DocumentRow,
  ExtractedFieldView,
  FinancialsAssembly,
  RunDetail,
  RunReport,
  RunStatusResponse,
  RunSummary,
} from "@/lib/types";

const P = "/api/v1";

// --- queries --------------------------------------------------------------
export function useDashboard() {
  return useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiGet<DashboardSummary>(`${P}/dashboard/summary`),
    refetchInterval: 20_000,
  });
}

export function useCases() {
  return useQuery({
    queryKey: ["cases"],
    queryFn: () => apiGet<Case[]>(`${P}/cases?limit=200`),
  });
}

export function useCase(caseId: string | undefined) {
  return useQuery({
    queryKey: ["case", caseId],
    queryFn: () => apiGet<Case>(`${P}/cases/${caseId}`),
    enabled: !!caseId,
  });
}

export function useCompanies() {
  return useQuery({
    queryKey: ["companies"],
    queryFn: () => apiGet<Company[]>(`${P}/companies?limit=200`),
  });
}

export function useCompanyMap() {
  const q = useCompanies();
  const map = new Map<string, Company>();
  (q.data ?? []).forEach((c) => map.set(c.id, c));
  return { ...q, map };
}

export function useCaseRuns(caseId: string | undefined) {
  return useQuery({
    queryKey: ["case-runs", caseId],
    queryFn: () => apiGet<RunSummary[]>(`${P}/cases/${caseId}/runs`),
    enabled: !!caseId,
    refetchInterval: (query) => {
      const runs = query.state.data as RunSummary[] | undefined;
      const active = runs?.some((r) => r.status === "running" || r.status === "pending");
      return active ? 3_000 : false;
    },
  });
}

/** Live run detail: polls while the run is executing or paused for review. */
export function useRunDetail(runId: string | undefined, opts?: { poll?: boolean }) {
  return useQuery({
    queryKey: ["run-detail", runId],
    queryFn: () => apiGet<RunDetail>(`${P}/runs/${runId}/detail`),
    enabled: !!runId,
    refetchInterval: (query) => {
      if (opts?.poll === false) return false;
      const d = query.state.data as RunDetail | undefined;
      if (!d) return 3_000;
      return d.status === "running" || d.status === "pending" ? 3_000 : false;
    },
  });
}

export function useRunStatus(runId: string | undefined) {
  return useQuery({
    queryKey: ["run-status", runId],
    queryFn: () => apiGet<RunStatusResponse>(`${P}/runs/${runId}`),
    enabled: !!runId,
  });
}

export function useReport(
  runId: string | undefined,
  options?: Partial<UseQueryOptions<RunReport>>,
) {
  return useQuery({
    queryKey: ["report", runId],
    queryFn: () => apiGet<RunReport>(`${P}/runs/${runId}/report`),
    enabled: !!runId,
    retry: false,
    ...options,
  });
}

export function useCaseFindings(caseId: string | undefined) {
  return useQuery({
    queryKey: ["case-findings", caseId],
    queryFn: () => apiGet<CaseFindings>(`${P}/cases/${caseId}/findings`),
    enabled: !!caseId,
  });
}

export function useDocuments(caseId: string | undefined) {
  return useQuery({
    queryKey: ["documents", caseId],
    queryFn: () => apiGet<DocumentRow[]>(`${P}/cases/${caseId}/documents`),
    enabled: !!caseId,
    refetchInterval: (query) => {
      const docs = query.state.data as DocumentRow[] | undefined;
      const busy = docs?.some(
        (d) =>
          d.status === "processing" ||
          d.status === "uploaded" ||
          d.stages.some((s) => s.status === "running" || s.status === "pending"),
      );
      return busy ? 2_500 : false;
    },
  });
}

export function useExtractedFields(caseId: string | undefined, documentId: string | undefined) {
  return useQuery({
    queryKey: ["fields", caseId, documentId],
    queryFn: () =>
      apiGet<ExtractedFieldView[]>(`${P}/cases/${caseId}/documents/${documentId}/fields`),
    enabled: !!caseId && !!documentId,
  });
}

export function useCaseFinancials(caseId: string | undefined) {
  return useQuery({
    queryKey: ["financials", caseId],
    queryFn: () => apiGet<FinancialsAssembly>(`${P}/cases/${caseId}/financials`),
    enabled: !!caseId,
  });
}

export function useUploadDocuments(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      return apiUpload<{ documents: DocumentRow[] }>(`${P}/cases/${caseId}/documents`, fd);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents", caseId] });
    },
  });
}

// --- mutations ------------------------------------------------------------
export interface StartRunBody {
  workflow_key?: string;
  financials?: Record<string, unknown> | null;
  scratch?: Record<string, unknown> | null;
}

export function useStartRun(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: StartRunBody) =>
      apiPost<{ run_id: string; workflow_key: string; status: string }>(
        `${P}/cases/${caseId}/runs`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["case-runs", caseId] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export interface HitlBody {
  notes: { label: string; notch: number }[];
  knockouts?: string[];
}

export function useSubmitHitl(runId: string, caseId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: HitlBody) =>
      apiPost<{ run_id: string; status: string }>(`${P}/runs/${runId}/hitl`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["run-detail", runId] });
      if (caseId) qc.invalidateQueries({ queryKey: ["case-runs", caseId] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function useCreateCompany() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { legal_name: string; sector?: string; gstin?: string; cin?: string }) =>
      apiPost<Company>(`${P}/companies`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["companies"] }),
  });
}

export function useCreateCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      company_id: string;
      title: string;
      requested_amount?: string | null;
      currency?: string;
    }) => apiPost<Case>(`${P}/cases`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cases"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
