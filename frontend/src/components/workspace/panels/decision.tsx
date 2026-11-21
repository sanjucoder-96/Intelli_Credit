"use client";

import { AlertTriangle, CheckCircle2, ShieldAlert } from "lucide-react";
import * as React from "react";

import { DecisionBadge, GradeBadge } from "@/components/shared/risk";
import { KeyValue, SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { decisionTone, fmtInrFull, fmtPct, titleCase, toneStyle } from "@/lib/format";
import type { Assessment } from "@/lib/types";

function factorLabel(id: string): string {
  return titleCase(id);
}

export function DecisionPanel({ assessment }: { assessment: Assessment | null }) {
  if (!assessment || !assessment.decision) {
    return (
      <EmptyState
        title="No decision yet"
        description="The deterministic policy grid produces the decision once scoring completes."
      />
    );
  }

  const positives: string[] = [];
  const negatives: string[] = [];

  for (const f of assessment.factors) {
    if (!f.admitted || f.normalized === null) continue;
    if (f.normalized >= 70) positives.push(`${factorLabel(f.factor_id)} (${f.normalized.toFixed(0)}/100)`);
    else if (f.normalized < 50)
      negatives.push(`${factorLabel(f.factor_id)} (${f.normalized.toFixed(0)}/100)`);
  }
  for (const o of assessment.overlays) {
    if (!o.fired) continue;
    const text = `${titleCase(o.rule_id)} ${o.effect}${o.detail ? ` — ${o.detail}` : ""}`;
    // A positive qualitative notch is a strength; caps / knockouts / negative
    // notches are concerns.
    const positive = o.rule_id === "qualitative_notch" && !o.effect.trim().startsWith("-");
    (positive ? positives : negatives).push(text);
  }
  assessment.ml.top_mitigants.slice(0, 2).forEach((r) => positives.push(`${r.label} (ML)`));
  assessment.ml.top_risk_factors.slice(0, 2).forEach((r) => negatives.push(`${r.label} (ML)`));
  if ((assessment.coverage ?? 1) < 0.7) negatives.push("Evidence coverage below 70%");

  const dTone = decisionTone(assessment.decision);
  const premium = assessment.pricing.grade_premium_pct;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className={`border-l-4 p-5 lg:col-span-1 ${toneStyle(dTone).text.replace("text-", "border-l-")}`}>
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Recommendation
        </p>
        <div className="mt-2 flex items-center gap-3">
          <DecisionBadge decision={assessment.decision} className="text-base" />
          <GradeBadge grade={assessment.grade} className="text-sm" />
        </div>
        <Separator className="my-4" />
        <dl>
          <KeyValue label="Recommended limit" mono>
            {fmtInrFull(assessment.limit.amount)}
          </KeyValue>
          <KeyValue label="Binding constraint">
            <span className="font-mono text-xs">{assessment.limit.binding_constraint ?? "—"}</span>
          </KeyValue>
          <KeyValue label="Indicative rate" mono>
            {fmtPct(assessment.pricing.rate_pct)}
          </KeyValue>
          <KeyValue label="Risk premium" mono>
            {premium !== undefined ? fmtPct(premium) : "—"}
          </KeyValue>
          <KeyValue label="Base rate" mono>
            {assessment.pricing.base_rate_pct !== undefined
              ? fmtPct(assessment.pricing.base_rate_pct)
              : "—"}
          </KeyValue>
        </dl>
        {assessment.limit.arithmetic ? (
          <p className="mt-3 rounded-md bg-muted/50 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
            {assessment.limit.arithmetic}
          </p>
        ) : null}
      </Card>

      <Card className="p-5 lg:col-span-2">
        <SectionTitle
          title="Why"
          description="Structured rationale from the persisted scorecard factors and overlays — not LLM prose."
        />
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <div>
            <p className="mb-2 flex items-center gap-1.5 text-sm font-medium text-success">
              <CheckCircle2 className="size-4" /> Supporting
            </p>
            {positives.length ? (
              <ul className="flex flex-col gap-1.5">
                {positives.map((p, i) => (
                  <li key={i} className="flex gap-2 text-sm">
                    <span className="text-success">✓</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No standout strengths.</p>
            )}
          </div>
          <div>
            <p className="mb-2 flex items-center gap-1.5 text-sm font-medium text-destructive">
              <AlertTriangle className="size-4" /> Concerns
            </p>
            {negatives.length ? (
              <ul className="flex flex-col gap-1.5">
                {negatives.map((n, i) => (
                  <li key={i} className="flex gap-2 text-sm">
                    <span className="text-destructive">⚠</span>
                    <span>{n}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No material concerns.</p>
            )}
          </div>
        </div>

        {assessment.notes.length ? (
          <>
            <Separator className="my-4" />
            <div className="flex items-start gap-2 text-xs text-muted-foreground">
              <ShieldAlert className="mt-0.5 size-3.5 shrink-0" />
              <div className="flex flex-col gap-1">
                {assessment.notes.map((note, i) => (
                  <p key={i}>{note}</p>
                ))}
              </div>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}
