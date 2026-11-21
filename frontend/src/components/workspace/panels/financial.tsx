"use client";

import * as React from "react";

import { SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { HBar } from "@/components/shared/viz";
import { Card } from "@/components/ui/card";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { fmtNum, titleCase } from "@/lib/format";
import type { Assessment, RunReport } from "@/lib/types";

const CATEGORIES: { title: string; ratios: string[] }[] = [
  { title: "Liquidity", ratios: ["current_ratio", "quick_ratio"] },
  { title: "Leverage", ratios: ["debt_to_equity", "tol_tnw", "debt_to_ebitda"] },
  { title: "Coverage", ratios: ["interest_coverage", "dscr"] },
  { title: "Profitability", ratios: ["ebitda_margin", "net_margin"] },
  { title: "Efficiency", ratios: ["receivable_days", "inventory_days"] },
  { title: "Cash flow", ratios: ["cfo_to_ebitda"] },
];

export function FinancialPanel({
  report,
  assessment,
}: {
  report: RunReport | undefined;
  assessment: Assessment | null;
}) {
  const ratios = report?.ir?.ratios;

  if (!ratios) {
    // Fall back to the scored financial factors from the scorecard.
    const factors = (assessment?.factors ?? []).filter(
      (f) => f.pillar_id === "financial_strength",
    );
    if (!factors.length) {
      return (
        <EmptyState
          title="No financial analysis yet"
          description="Ratios are computed deterministically from the borrower's statements when the appraisal runs."
        />
      );
    }
    return (
      <Card className="p-5">
        <SectionTitle title="Financial ratios" description="Scored factors from the scorecard." />
        <div className="flex flex-col gap-2.5">
          {factors.map((f) => (
            <div key={f.factor_id} className="flex items-center gap-3">
              <span className="w-40 text-sm">{titleCase(f.factor_id)}</span>
              <span className="w-16 text-right font-mono text-sm tabular-nums">
                {fmtNum(f.raw_value, 2)}
              </span>
              <div className="flex-1">
                <HBar value={f.normalized} tone={scoreTone(f.normalized)} />
              </div>
            </div>
          ))}
        </div>
      </Card>
    );
  }

  const available = Object.entries(ratios).filter(([, v]) => v.available && v.value !== null);
  if (!available.length) {
    return (
      <EmptyState
        title="No ratios available"
        description="The statements did not contain enough line items to compute ratios."
      />
    );
  }

  return (
    <Card className="p-5">
      <SectionTitle
        title="Financial ratios"
        description="Computed deterministically from the extracted statements — hover a value for its formula."
      />
      <div className="grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2 lg:grid-cols-3">
        {CATEGORIES.map((cat) => {
          const items = cat.ratios
            .map((name) => [name, ratios[name]] as const)
            .filter(([, v]) => v && v.available && v.value !== null);
          if (!items.length) return null;
          return (
            <div key={cat.title}>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {cat.title}
              </p>
              <dl className="flex flex-col gap-1">
                {items.map(([name, v]) => (
                  <div key={name} className="flex items-baseline justify-between gap-3 text-sm">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <dt className="cursor-help text-muted-foreground underline-offset-2 hover:underline">
                          {ratioLabel(name)}
                        </dt>
                      </TooltipTrigger>
                      <TooltipContent>{v.explanation}</TooltipContent>
                    </Tooltip>
                    <dd className="font-mono tabular-nums">{formatRatio(name, v.value!)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function ratioLabel(name: string): string {
  const labels: Record<string, string> = {
    current_ratio: "Current ratio",
    quick_ratio: "Quick ratio",
    debt_to_equity: "Debt / equity",
    tol_tnw: "TOL / TNW",
    debt_to_ebitda: "Debt / EBITDA",
    interest_coverage: "Interest coverage",
    dscr: "DSCR",
    ebitda_margin: "EBITDA margin",
    net_margin: "Net margin",
    receivable_days: "Receivable days",
    inventory_days: "Inventory days",
    cfo_to_ebitda: "CFO / EBITDA",
  };
  return labels[name] ?? titleCase(name);
}

function formatRatio(name: string, value: number): string {
  if (name.endsWith("_margin")) return `${(value * 100).toFixed(1)}%`;
  if (name.endsWith("_days")) return `${value.toFixed(0)}d`;
  return value.toFixed(2);
}

function scoreTone(n: number | null) {
  if (n === null) return "muted" as const;
  if (n >= 70) return "success" as const;
  if (n >= 45) return "warning" as const;
  return "destructive" as const;
}
