"use client";

import * as React from "react";

import { SeverityBadge } from "@/components/shared/risk";
import { Badge } from "@/components/ui/badge";
import { fmtRatioPct, titleCase } from "@/lib/format";
import type { Finding } from "@/lib/types";

export function FindingCard({ finding }: { finding: Finding }) {
  const data = finding.data ?? {};
  const entries = Object.entries(data).filter(
    ([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean",
  );
  return (
    <div className="rounded-lg border p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">{finding.title}</p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {finding.agent ? <span className="font-mono">{finding.agent}</span> : null}
            {finding.category ? <span>· {titleCase(finding.category)}</span> : null}
            {finding.confidence !== null ? (
              <span>· confidence {fmtRatioPct(finding.confidence)}</span>
            ) : null}
          </div>
        </div>
        <SeverityBadge severity={finding.severity} />
      </div>
      {finding.narrative ? (
        <p className="mt-2 text-sm text-muted-foreground">{finding.narrative}</p>
      ) : null}
      {entries.length ? (
        <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1">
          {entries.slice(0, 6).map(([k, v]) => (
            <span key={k} className="text-xs">
              <span className="text-muted-foreground">{titleCase(k)}: </span>
              <span className="font-mono">{String(v)}</span>
            </span>
          ))}
        </div>
      ) : null}
      <Badge variant="outline" className="mt-2.5 text-[10px] capitalize">
        {finding.kind}
      </Badge>
    </div>
  );
}
