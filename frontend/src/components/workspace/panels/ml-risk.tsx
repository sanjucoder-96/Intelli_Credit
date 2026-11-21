"use client";

import { Cpu, Info } from "lucide-react";
import * as React from "react";

import { DirectionArrow } from "@/components/shared/risk";
import { KeyValue, SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { ContributionBar } from "@/components/shared/viz";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { fmtRatioPct, pdTone, toneStyle } from "@/lib/format";
import type { MlPrediction, ReasonCode } from "@/lib/types";
import { cn } from "@/lib/utils";

function ReasonList({ codes, max }: { codes: ReasonCode[]; max: number }) {
  if (!codes.length) return <p className="text-sm text-muted-foreground">None.</p>;
  return (
    <ul className="flex flex-col gap-2.5">
      {codes.map((c, i) => (
        <li key={i} className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-sm">
            <DirectionArrow direction={c.direction} />
            <span className="flex-1">{c.label}</span>
            {c.present === false ? (
              <Badge variant="outline" className="text-[10px]">
                not available
              </Badge>
            ) : null}
          </div>
          <ContributionBar value={c.shap_log_odds} max={max} />
        </li>
      ))}
    </ul>
  );
}

export function MlRiskPanel({ ml }: { ml: MlPrediction | null }) {
  if (!ml || !ml.available) {
    return (
      <EmptyState
        icon={Cpu}
        title="ML default-risk not available"
        description={
          ml?.note ||
          "No champion PD model is registered, or feature coverage is below the minimum — the pillar is excluded and coverage reflects it."
        }
      />
    );
  }

  const allCodes = [...ml.top_risk_factors, ...ml.top_mitigants];
  const max = Math.max(0.01, ...allCodes.map((c) => Math.abs(c.shap_log_odds)));
  const tone = pdTone(ml.pd);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="p-5">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Probability of Default
        </p>
        <p className={cn("mt-1 font-mono text-4xl font-semibold tabular-nums", toneStyle(tone).text)}>
          {fmtRatioPct(ml.pd, 1)}
        </p>
        <p className="text-xs text-muted-foreground">calibrated · relative signal</p>

        <div className="mt-4 flex flex-col gap-1.5">
          <dl>
            <KeyValue label="Uncalibrated" mono>
              {fmtRatioPct(ml.pd_raw, 1)}
            </KeyValue>
            <KeyValue label="Feature coverage" mono>
              {fmtRatioPct(ml.coverage)}
            </KeyValue>
          </dl>
        </div>

        <div className="mt-4 rounded-lg border bg-muted/40 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Model
            </p>
            {ml.non_production ? (
              <Badge variant="warning" className="text-[10px]">
                NON-PRODUCTION
              </Badge>
            ) : (
              <Badge variant="secondary" className="text-[10px]">
                champion
              </Badge>
            )}
          </div>
          <dl className="text-xs">
            <KeyValue label="Name" mono className="py-1">
              {ml.model_name} v{ml.model_version}
            </KeyValue>
            <KeyValue label="Algorithm" mono className="py-1">
              {ml.algorithm}
            </KeyValue>
            <KeyValue label="Feature schema" mono className="py-1">
              {ml.feature_schema_version}
            </KeyValue>
          </dl>
        </div>

        <div className="mt-3 flex items-start gap-2 rounded-md bg-warning/10 p-2.5 text-[11px] leading-relaxed text-warning-foreground/90">
          <Info className="mt-0.5 size-3.5 shrink-0" />
          <p>
            {ml.note ||
              "Relative default-risk signal trained on foreign public bankruptcy data, re-normalised inside the deterministic scorecard. NOT an absolute Indian PD."}
          </p>
        </div>
      </Card>

      <Card className="p-5 lg:col-span-2">
        <SectionTitle
          title="Why this PD"
          description="Exact TreeSHAP contributions (log-odds) from the model — structural, not prompted."
        />
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
          <div>
            <p className="mb-2.5 text-sm font-medium text-destructive">Raises default risk</p>
            <ReasonList codes={ml.top_risk_factors} max={max} />
          </div>
          <div>
            <p className="mb-2.5 text-sm font-medium text-success">Lowers default risk</p>
            <ReasonList codes={ml.top_mitigants} max={max} />
          </div>
        </div>
      </Card>
    </div>
  );
}
