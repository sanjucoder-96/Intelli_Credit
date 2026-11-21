"use client";

import { Globe } from "lucide-react";
import * as React from "react";

import { SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { HBar } from "@/components/shared/viz";
import { FindingCard } from "@/components/workspace/finding-card";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { fmtNum } from "@/lib/format";
import type { Assessment, CaseFindings } from "@/lib/types";

export function ResearchPanel({
  findings,
  assessment,
}: {
  findings: CaseFindings | undefined;
  assessment: Assessment | null;
}) {
  const items = (findings?.findings ?? []).filter(
    (f) => f.kind === "research" || f.kind === "gap",
  );
  const extFactor = assessment?.factors.find((f) => f.pillar_id === "external_intelligence");

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="p-5 lg:col-span-2">
        <SectionTitle
          title="External research"
          description="Web/registry signals on promoters, litigation, and sector — snapshotted at claim time."
          right={
            <div className="hidden items-center gap-1.5 text-[11px] sm:flex">
              <Badge variant="secondary">Evidence</Badge>
              <Badge variant="outline">Inference</Badge>
              <Badge variant="warning">Risk</Badge>
            </div>
          }
        />
        {items.length ? (
          <div className="flex flex-col gap-2.5">
            {items.map((f) => (
              <FindingCard key={f.id} finding={f} />
            ))}
          </div>
        ) : (
          <EmptyState
            icon={Globe}
            title="No external research on file"
            description="External web research is not configured for this environment, so no adverse signals were recorded — an honest gap, never fabricated citations."
          />
        )}
      </Card>

      <Card className="p-5">
        <SectionTitle title="Score impact" description="External intelligence pillar." />
        {extFactor ? (
          <div className="flex flex-col gap-3">
            <div>
              <div className="mb-1 flex items-center justify-between text-sm">
                <span>External signal</span>
                <span className="font-mono tabular-nums">{fmtNum(extFactor.normalized, 0)}/100</span>
              </div>
              <HBar
                value={extFactor.normalized}
                tone={(extFactor.normalized ?? 0) >= 70 ? "success" : "warning"}
              />
            </div>
            <p className="text-xs text-muted-foreground">{extFactor.explanation}</p>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Pillar not yet scored.</p>
        )}
        <p className="mt-4 rounded-md bg-muted/50 p-2.5 text-xs text-muted-foreground">
          LLM-written research prose is a summary, not a source. Every material claim links to a
          snapshotted citation in the Evidence tab.
        </p>
      </Card>
    </div>
  );
}
