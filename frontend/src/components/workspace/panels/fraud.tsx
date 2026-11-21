"use client";

import { ShieldAlert } from "lucide-react";
import * as React from "react";

import { SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { HBar } from "@/components/shared/viz";
import { FindingCard } from "@/components/workspace/finding-card";
import { Card } from "@/components/ui/card";
import { fmtNum, titleCase } from "@/lib/format";
import type { Assessment, CaseFindings } from "@/lib/types";

export function FraudPanel({
  findings,
  assessment,
}: {
  findings: CaseFindings | undefined;
  assessment: Assessment | null;
}) {
  const items = (findings?.findings ?? []).filter(
    (f) => f.kind === "fraud" || f.kind === "validation",
  );
  const fraudFactor = assessment?.factors.find((f) => f.pillar_id === "fraud_integrity");

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="p-5 lg:col-span-2">
        <SectionTitle
          title="Fraud & early-warning signals"
          description="Deterministic forensic checks — GST↔bank consistency, ITC mismatch, cheque returns. No LLM verdicts."
        />
        {items.length ? (
          <div className="flex flex-col gap-2.5">
            {items.map((f) => (
              <FindingCard key={f.id} finding={f} />
            ))}
          </div>
        ) : (
          <EmptyState
            icon={ShieldAlert}
            title="No fraud signals raised"
            description="The forensic validators found no material inconsistencies for this case."
          />
        )}
      </Card>

      <Card className="p-5">
        <SectionTitle title="Score impact" description="Fraud & integrity pillar." />
        {fraudFactor ? (
          <div className="flex flex-col gap-3">
            <div>
              <div className="mb-1 flex items-center justify-between text-sm">
                <span>{titleCase(fraudFactor.factor_id)}</span>
                <span className="font-mono tabular-nums">{fmtNum(fraudFactor.normalized, 0)}/100</span>
              </div>
              <HBar
                value={fraudFactor.normalized}
                tone={
                  (fraudFactor.normalized ?? 0) >= 70
                    ? "success"
                    : (fraudFactor.normalized ?? 0) >= 45
                      ? "warning"
                      : "destructive"
                }
              />
            </div>
            <p className="text-xs text-muted-foreground">{fraudFactor.explanation}</p>
            <p className="rounded-md bg-muted/50 p-2.5 text-xs text-muted-foreground">
              A critical fraud signal caps the score at 45; a high signal caps it at 55. The cap is a
              hard ceiling — a positive officer note cannot lift the score above it.
            </p>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Pillar not yet scored.</p>
        )}
      </Card>
    </div>
  );
}
