"use client";

import * as React from "react";

import { KeyValue, SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { HBar, ScoreGauge } from "@/components/shared/viz";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { fmtNum, fmtRatioPct, titleCase } from "@/lib/format";
import type { Assessment, RiskFactor } from "@/lib/types";

const PILLAR_LABELS: Record<string, string> = {
  financial_strength: "Financial Strength",
  ml_default_risk: "ML Default Risk",
  external_intelligence: "External Intelligence",
  fraud_integrity: "Fraud & Integrity",
  qualitative: "Management / Qualitative",
};

function tone(n: number | null) {
  if (n === null) return "muted" as const;
  if (n >= 70) return "success" as const;
  if (n >= 45) return "warning" as const;
  return "destructive" as const;
}

function FactorRow({ f }: { f: RiskFactor }) {
  return (
    <AccordionItem value={`${f.pillar_id}.${f.factor_id}`} className="border-border/60">
      <AccordionTrigger className="py-2.5 hover:no-underline">
        <div className="flex flex-1 items-center gap-3 pr-2">
          <span className="w-40 shrink-0 truncate text-left text-sm font-medium">
            {titleCase(f.factor_id)}
          </span>
          {f.admitted ? (
            <>
              <div className="hidden flex-1 sm:block">
                <HBar value={f.normalized} tone={tone(f.normalized)} />
              </div>
              <span className="w-12 text-right font-mono text-xs tabular-nums text-muted-foreground">
                {fmtNum(f.normalized, 0)}
              </span>
              <span className="w-14 text-right font-mono text-xs tabular-nums">
                {fmtNum(f.contribution, 1)}
              </span>
            </>
          ) : (
            <Badge variant="outline" className="ml-auto text-[10px]">
              excluded
            </Badge>
          )}
        </div>
      </AccordionTrigger>
      <AccordionContent>
        <div className="ml-1 grid grid-cols-2 gap-x-6 gap-y-1 rounded-md bg-muted/40 p-3 text-xs sm:grid-cols-4">
          <Meta label="Formula" value={f.explanation || "—"} span />
          <Meta label="Input" value={f.raw_value !== null ? fmtNum(f.raw_value, 3) : "—"} />
          <Meta label="Normalized" value={f.normalized !== null ? `${fmtNum(f.normalized, 1)}/100` : "—"} />
          <Meta label="Weight" value={fmtNum(f.weight, 2)} />
          <Meta label="Contribution" value={fmtNum(f.contribution, 2)} />
          {f.exclusion_reason ? <Meta label="Excluded" value={f.exclusion_reason} /> : null}
        </div>
      </AccordionContent>
    </AccordionItem>
  );
}

function Meta({ label, value, span }: { label: string; value: string; span?: boolean }) {
  return (
    <div className={span ? "col-span-2 sm:col-span-4" : ""}>
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="font-mono">{value}</p>
    </div>
  );
}

export function ScorecardPanel({ assessment }: { assessment: Assessment | null }) {
  const pillars = React.useMemo(() => {
    const map = new Map<string, RiskFactor[]>();
    for (const f of assessment?.factors ?? []) {
      if (!map.has(f.pillar_id)) map.set(f.pillar_id, []);
      map.get(f.pillar_id)!.push(f);
    }
    return [...map.entries()].map(([id, factors]) => {
      const admitted = factors.filter((f) => f.admitted);
      const score = admitted.reduce((s, f) => s + (f.contribution ?? 0), 0);
      return { id, factors, admitted: admitted.length, total: factors.length, score };
    });
  }, [assessment]);

  if (!assessment) {
    return (
      <EmptyState
        title="No scorecard yet"
        description="The deterministic scorecard runs after review, producing the weighted breakdown."
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="flex flex-col items-center p-5">
        <ScoreGauge
          value={assessment.final_score}
          tone={tone(assessment.final_score)}
          label="Final score"
        />
        <dl className="mt-3 w-full">
          <KeyValue label="Base score" mono>
            {fmtNum(assessment.base_score, 1)}
          </KeyValue>
          <KeyValue label="Final score" mono>
            {fmtNum(assessment.final_score, 1)}
          </KeyValue>
          <KeyValue label="Coverage" mono>
            {fmtRatioPct(assessment.coverage)}
          </KeyValue>
          <KeyValue label="Scorecard" mono>
            {assessment.scorecard_key} v{assessment.scorecard_version}
          </KeyValue>
        </dl>
      </Card>

      <Card className="p-5 lg:col-span-2">
        <SectionTitle
          title="Score breakdown"
          description="S = Σ pillar · Σ factor. Expand a factor for its formula, input, and contribution."
        />
        <div className="flex flex-col gap-4">
          {pillars.map((p) => (
            <div key={p.id}>
              <div className="mb-1 flex items-center gap-3">
                <span className="w-44 shrink-0 text-sm font-semibold">
                  {PILLAR_LABELS[p.id] ?? titleCase(p.id)}
                </span>
                <div className="flex-1">
                  <HBar value={p.admitted ? p.score : null} tone={tone(p.admitted ? p.score : null)} />
                </div>
                <span className="w-16 text-right font-mono text-xs tabular-nums">
                  {p.admitted ? fmtNum(p.score, 1) : "excl."}
                </span>
              </div>
              <Accordion type="multiple" className="ml-1 border-l pl-3">
                {p.factors.map((f) => (
                  <FactorRow key={f.factor_id} f={f} />
                ))}
              </Accordion>
            </div>
          ))}
        </div>

        {assessment.overlays.length ? (
          <div className="mt-5">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Overlays applied
            </p>
            <div className="flex flex-col gap-1.5">
              {assessment.overlays
                .filter((o) => o.fired)
                .map((o, i) => (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <Badge variant="warning" className="font-mono text-[10px]">
                      {o.effect}
                    </Badge>
                    <span className="font-medium">{titleCase(o.rule_id)}</span>
                    {o.detail ? <span className="text-muted-foreground">— {o.detail}</span> : null}
                  </div>
                ))}
            </div>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
