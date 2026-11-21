"use client";

import * as React from "react";

import { DecisionBadge, GradeBadge, SeverityBadge } from "@/components/shared/risk";
import { KeyValue, SectionTitle } from "@/components/shared/stat";
import { ScoreGauge } from "@/components/shared/viz";
import { Card } from "@/components/ui/card";
import { fmtInrFull, fmtPct, fmtRatioPct, pdTone, toneStyle } from "@/lib/format";
import type { Assessment, CaseFindings } from "@/lib/types";

function gaugeTone(score: number | null) {
  if (score === null) return "muted" as const;
  if (score >= 71) return "success" as const;
  if (score >= 50) return "warning" as const;
  return "destructive" as const;
}

export function OverviewPanel({
  assessment,
  findings,
}: {
  assessment: Assessment | null;
  findings: CaseFindings | undefined;
}) {
  const sevCounts = React.useMemo(() => {
    const c: Record<string, number> = {};
    (findings?.findings ?? []).forEach((f) => {
      c[f.severity] = (c[f.severity] ?? 0) + 1;
    });
    return c;
  }, [findings]);

  if (!assessment) {
    return (
      <Card className="p-6">
        <p className="text-sm text-muted-foreground">
          No appraisal has run yet. Start one to compute the score, PD, and decision.
        </p>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="flex flex-col items-center justify-center p-5">
        <ScoreGauge
          value={assessment.final_score}
          tone={gaugeTone(assessment.final_score)}
          label="Final score"
        />
        <div className="mt-2 flex items-center gap-2">
          <GradeBadge grade={assessment.grade} />
          <DecisionBadge decision={assessment.decision} />
        </div>
      </Card>

      <Card className="p-5">
        <SectionTitle title="Outcome" />
        <dl>
          <KeyValue label="Recommended limit" mono>
            {fmtInrFull(assessment.limit.amount)}
          </KeyValue>
          <KeyValue label="Indicative rate" mono>
            {fmtPct(assessment.pricing.rate_pct)}
          </KeyValue>
          <KeyValue label="Coverage" mono>
            {fmtRatioPct(assessment.coverage)}
          </KeyValue>
          <KeyValue label="ML PD (relative)" mono>
            <span className={toneStyle(pdTone(assessment.ml.pd)).text}>
              {assessment.ml.available ? fmtRatioPct(assessment.ml.pd, 1) : "excluded"}
            </span>
          </KeyValue>
        </dl>
      </Card>

      <Card className="p-5">
        <SectionTitle title="Signals" description="Findings by severity." />
        {Object.keys(sevCounts).length ? (
          <div className="flex flex-col gap-2">
            {["critical", "high", "medium", "low", "info"]
              .filter((s) => sevCounts[s])
              .map((s) => (
                <div key={s} className="flex items-center justify-between">
                  <SeverityBadge severity={s} />
                  <span className="font-mono text-sm tabular-nums">{sevCounts[s]}</span>
                </div>
              ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No findings recorded.</p>
        )}
      </Card>
    </div>
  );
}
