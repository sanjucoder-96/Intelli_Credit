"use client";

import { Building2 } from "lucide-react";
import * as React from "react";

import { DecisionBadge, GradeBadge } from "@/components/shared/risk";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { fmtInr, fmtPct, fmtRatioPct, pdTone, titleCase, toneStyle } from "@/lib/format";
import type { Case, Company, RunSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const MODE_STYLE: Record<string, string> = {
  LIVE: "border-success/40 bg-success/10 text-success",
  REPLAY: "border-primary/40 bg-primary/10 text-primary",
  DEGRADED: "border-warning/40 bg-warning/10 text-warning",
};

const MODE_TITLE: Record<string, string> = {
  LIVE: "Live: model calls hit the real provider.",
  REPLAY: "Replay: served from recorded fixtures (offline demo) — no live call made.",
  DEGRADED: "Degraded: a live call fell back to a recording. The answer is not fabricated.",
};

/** Truthful demo-reliability badge — LIVE / REPLAY / DEGRADED. */
function ExecutionModeBadge({ mode }: { mode?: string }) {
  const m = (mode ?? "LIVE").toUpperCase();
  return (
    <span
      title={MODE_TITLE[m] ?? m}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        MODE_STYLE[m] ?? "border-border bg-muted text-muted-foreground",
      )}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {m}
    </span>
  );
}

function Outcome({
  label,
  value,
  tone = "default",
  mono = true,
}: {
  label: string;
  value: React.ReactNode;
  tone?: Parameters<typeof toneStyle>[0];
  mono?: boolean;
}) {
  return (
    <div className="min-w-[92px]">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className={cn("mt-0.5 text-sm font-semibold", mono && "font-mono tabular-nums", toneStyle(tone).text)}>
        {value}
      </p>
    </div>
  );
}

export function CompanyHeader({
  caseData,
  company,
  run,
  right,
}: {
  caseData: Case;
  company: Company | undefined;
  run: RunSummary | null;
  right?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border bg-background p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <div className="grid size-11 shrink-0 place-items-center rounded-lg bg-secondary">
            <Building2 className="size-5 text-secondary-foreground" />
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold tracking-tight">
              {company?.legal_name ?? "Company"}
            </h1>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {company?.sector ? <span>{company.sector}</span> : null}
              {company?.gstin ? (
                <span className="font-mono">GSTIN {company.gstin}</span>
              ) : null}
              {company?.cin ? <span className="font-mono">CIN {company.cin}</span> : null}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">{caseData.title}</span>
              <Badge variant="secondary">{titleCase(caseData.status)}</Badge>
              {caseData.requested_amount ? (
                <span className="text-muted-foreground">
                  · requested{" "}
                  <span className="font-mono text-foreground">
                    {fmtInr(caseData.requested_amount)}
                  </span>
                </span>
              ) : null}
            </div>
          </div>
        </div>
        {right}
      </div>

      {run && run.grade ? (
        <>
          <Separator className="my-4" />
          <div className="flex flex-wrap items-end gap-6">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Grade
              </p>
              <div className="mt-1">
                <GradeBadge grade={run.grade} className="text-sm" />
              </div>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Decision
              </p>
              <div className="mt-1">
                <DecisionBadge decision={run.decision} />
              </div>
            </div>
            <div>
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Run mode
              </p>
              <div className="mt-1">
                <ExecutionModeBadge mode={run.execution_mode} />
              </div>
            </div>
            <Outcome label="Score" value={run.final_score?.toFixed(1) ?? "—"} tone={toneOf(run)} />
            <Outcome
              label="PD (relative)"
              value={run.pd !== null ? fmtRatioPct(run.pd, 1) : "—"}
              tone={pdTone(run.pd)}
            />
            <Outcome label="Rec. limit" value={fmtInr(run.recommended_limit)} />
            <Outcome
              label="Indicative rate"
              value={run.interest_rate_pct !== null ? fmtPct(run.interest_rate_pct) : "—"}
            />
            <Outcome
              label="Coverage"
              value={run.coverage !== null ? fmtRatioPct(run.coverage) : "—"}
            />
          </div>
        </>
      ) : null}
    </div>
  );
}

function toneOf(run: RunSummary): Parameters<typeof toneStyle>[0] {
  const s = run.final_score ?? 0;
  if (s >= 71) return "success";
  if (s >= 50) return "warning";
  return "destructive";
}
