"use client";

import { ArrowLeft, Loader2, PauseCircle } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { StatusBadge } from "@/components/shared/risk";
import { ErrorState } from "@/components/shared/states";
import { CompanyHeader } from "@/components/workspace/company-header";
import { CamPanel } from "@/components/workspace/panels/cam";
import { DecisionPanel } from "@/components/workspace/panels/decision";
import { DocumentsPanel } from "@/components/workspace/panels/documents";
import { EvidencePanel } from "@/components/workspace/panels/evidence";
import { FinancialPanel } from "@/components/workspace/panels/financial";
import { FraudPanel } from "@/components/workspace/panels/fraud";
import { HitlPanel } from "@/components/workspace/panels/hitl";
import { MlRiskPanel } from "@/components/workspace/panels/ml-risk";
import { OverviewPanel } from "@/components/workspace/panels/overview";
import { ResearchPanel } from "@/components/workspace/panels/research";
import { ScorecardPanel } from "@/components/workspace/panels/scorecard";
import { StartAppraisalDialog } from "@/components/workspace/start-appraisal";
import { WorkflowProgress } from "@/components/workspace/workflow-progress";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { canWrite, useAuth } from "@/lib/auth";
import {
  useCase,
  useCaseFindings,
  useCaseRuns,
  useCompanyMap,
  useReport,
  useRunDetail,
} from "@/lib/hooks";

const TABS = [
  "documents",
  "overview",
  "evidence",
  "financial",
  "research",
  "fraud",
  "ml",
  "scorecard",
  "decision",
  "cam",
] as const;

const TAB_LABELS: Record<string, string> = {
  documents: "Documents",
  overview: "Overview",
  evidence: "Evidence",
  financial: "Financial",
  research: "Research",
  fraud: "Fraud",
  ml: "ML Risk",
  scorecard: "Scorecard",
  decision: "Decision",
  cam: "CAM",
  review: "Review",
};

export default function CaseWorkspace({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = React.use(params);
  const role = useAuth((s) => s.user?.role);

  const caseQ = useCase(caseId);
  const { map } = useCompanyMap();
  const runsQ = useCaseRuns(caseId);
  const latestRun = runsQ.data?.[0] ?? null;
  const detailQ = useRunDetail(latestRun?.run_id);
  const findingsQ = useCaseFindings(caseId);
  const reportQ = useReport(latestRun?.report_id ? latestRun.run_id : undefined);

  const detail = detailQ.data;
  const assessment = detail?.assessment ?? null;
  const status = latestRun?.status ?? null;
  const paused = status === "paused_hitl";

  const [tab, setTab] = React.useState<string>("documents");
  const [touched, setTouched] = React.useState(false);
  React.useEffect(() => {
    if (touched) return;
    if (paused) setTab("review");
    else if (latestRun) setTab("overview");
  }, [paused, latestRun, touched]);

  function onTab(v: string) {
    setTouched(true);
    setTab(v);
  }

  const company = caseQ.data ? map.get(caseQ.data.company_id) : undefined;

  if (caseQ.isLoading) {
    return (
      <div className="grid min-h-[60vh] place-items-center">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (caseQ.error || !caseQ.data) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <ErrorState error={caseQ.error ?? new Error("Case not found")} />
      </div>
    );
  }

  const running = status === "running" || status === "pending";

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4 p-4 md:p-6">
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" asChild className="-ml-2 gap-1.5 text-muted-foreground">
          <Link href="/cases">
            <ArrowLeft className="size-4" /> Cases
          </Link>
        </Button>
        <div className="flex items-center gap-2">
          {status ? <StatusBadge status={status} /> : null}
          {running ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> : null}
        </div>
      </div>

      <CompanyHeader
        caseData={caseQ.data}
        company={company}
        run={latestRun}
        right={
          canWrite(role) ? (
            <StartAppraisalDialog caseId={caseId} />
          ) : !latestRun ? (
            <span className="text-sm text-muted-foreground">No appraisal yet</span>
          ) : null
        }
      />

      {detail?.nodes?.length ? (
        <WorkflowProgress nodes={detail.nodes} status={status ?? "pending"} />
      ) : null}

      {paused ? (
        <button
          onClick={() => setTab("review")}
          className="flex items-center gap-3 rounded-xl border border-warning/40 bg-warning/10 p-3.5 text-left transition-colors hover:bg-warning/15"
        >
          <PauseCircle className="size-5 shrink-0 text-warning" />
          <div className="flex-1">
            <p className="text-sm font-medium">Paused for Credit Officer review</p>
            <p className="text-xs text-muted-foreground">
              The workflow is waiting on your primary due-diligence observations before scoring.
            </p>
          </div>
          <span className="text-sm font-medium text-warning">Review →</span>
        </button>
      ) : null}

      {runsQ.data && runsQ.data.length === 0 ? (
        <div className="rounded-xl border border-dashed bg-muted/20 px-6 py-4 text-center text-sm">
          <span className="text-muted-foreground">
            {canWrite(role)
              ? "No appraisal yet — upload documents below, then start the credit-appraisal workflow."
              : "No appraisal yet. An analyst can upload documents and start the workflow."}
          </span>
        </div>
      ) : null}

      <Tabs value={tab} onValueChange={onTab} className="mt-1">
          <div className="overflow-x-auto pb-1">
            <TabsList className="w-max">
              {paused ? (
                <TabsTrigger value="review" className="text-warning data-[state=active]:text-warning">
                  {TAB_LABELS.review}
                </TabsTrigger>
              ) : null}
              {TABS.map((t) => (
                <TabsTrigger key={t} value={t}>
                  {TAB_LABELS[t]}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>

          <div className="mt-4">
            {paused ? (
              <TabsContent value="review">
                <HitlPanel
                  runId={latestRun!.run_id}
                  caseId={caseId}
                  status={status ?? "pending"}
                  assessment={assessment}
                />
              </TabsContent>
            ) : null}
            <TabsContent value="documents">
              <DocumentsPanel caseId={caseId} canUpload={canWrite(role)} />
            </TabsContent>
            <TabsContent value="overview">
              <OverviewPanel assessment={assessment} findings={findingsQ.data} />
            </TabsContent>
            <TabsContent value="evidence">
              <EvidencePanel findings={findingsQ.data} caseId={caseId} />
            </TabsContent>
            <TabsContent value="financial">
              <FinancialPanel report={reportQ.data} assessment={assessment} />
            </TabsContent>
            <TabsContent value="research">
              <ResearchPanel findings={findingsQ.data} assessment={assessment} />
            </TabsContent>
            <TabsContent value="fraud">
              <FraudPanel findings={findingsQ.data} assessment={assessment} />
            </TabsContent>
            <TabsContent value="ml">
              <MlRiskPanel ml={assessment?.ml ?? null} />
            </TabsContent>
            <TabsContent value="scorecard">
              <ScorecardPanel assessment={assessment} />
            </TabsContent>
            <TabsContent value="decision">
              <DecisionPanel assessment={assessment} />
            </TabsContent>
            <TabsContent value="cam">
              <CamPanel report={reportQ.data} runId={latestRun?.run_id} />
            </TabsContent>
          </div>
        </Tabs>
    </div>
  );
}
