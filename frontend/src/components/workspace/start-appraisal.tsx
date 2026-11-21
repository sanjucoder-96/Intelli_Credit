"use client";

import { FileCheck2, Play, Sparkles } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { useCaseFinancials, useStartRun } from "@/lib/hooks";

const GROUPS: { title: string; fields: { key: string; label: string }[] }[] = [
  {
    title: "Profit & loss",
    fields: [
      { key: "revenue", label: "Revenue" },
      { key: "ebitda", label: "EBITDA" },
      { key: "net_profit", label: "Net profit" },
      { key: "interest_expense", label: "Interest expense" },
    ],
  },
  {
    title: "Balance sheet",
    fields: [
      { key: "total_assets", label: "Total assets" },
      { key: "total_equity", label: "Total equity" },
      { key: "total_debt", label: "Total debt" },
      { key: "current_assets", label: "Current assets" },
      { key: "current_liabilities", label: "Current liabilities" },
      { key: "inventory", label: "Inventory" },
      { key: "receivables", label: "Receivables" },
    ],
  },
  {
    title: "Cash flow",
    fields: [{ key: "cash_flow_from_operations", label: "CFO" }],
  },
];

const DEMO: Record<string, string> = {
  revenue: "100000000",
  ebitda: "18000000",
  net_profit: "9000000",
  interest_expense: "3000000",
  total_assets: "120000000",
  total_equity: "60000000",
  total_debt: "30000000",
  current_assets: "50000000",
  current_liabilities: "25000000",
  inventory: "15000000",
  receivables: "20000000",
  cash_flow_from_operations: "15000000",
};

export function StartAppraisalDialog({ caseId }: { caseId: string }) {
  const [open, setOpen] = React.useState(false);
  const [values, setValues] = React.useState<Record<string, string>>({});
  const startRun = useStartRun(caseId);
  const extracted = useCaseFinancials(caseId);

  function useExtracted() {
    const f = extracted.data;
    if (!f?.available) return;
    setValues((prev) => ({ ...prev, ...f.financials }));
    toast.success(`Loaded figures extracted from documents${f.fiscal_year ? ` (${f.fiscal_year})` : ""}.`);
  }

  function set(key: string, v: string) {
    setValues((prev) => ({ ...prev, [key]: v.replace(/[^0-9.]/g, "") }));
  }

  async function onStart() {
    const financials: Record<string, string> = { fiscal_year: "2025-26" };
    for (const [k, v] of Object.entries(values)) {
      if (v.trim()) financials[k] = v.trim();
    }
    if (Object.keys(financials).length <= 1) {
      toast.error("Enter the borrower's financials (or load the demo figures).");
      return;
    }
    try {
      await startRun.mutateAsync({ workflow_key: "credit_appraisal", financials });
      toast.success("Appraisal started — the workflow is running.");
      setOpen(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not start the appraisal.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Play /> Start appraisal
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Start credit appraisal</DialogTitle>
          <DialogDescription>
            Enter the borrower&apos;s financial line items. The deterministic engine computes ratios,
            the calibrated PD model runs, and the workflow pauses for your review.
          </DialogDescription>
        </DialogHeader>

        {extracted.data?.available ? (
          <button
            type="button"
            onClick={useExtracted}
            className="flex items-center gap-2.5 rounded-lg border border-success/40 bg-success/5 p-3 text-left transition-colors hover:bg-success/10"
          >
            <FileCheck2 className="size-4 shrink-0 text-success" />
            <div className="flex-1 text-sm">
              <p className="font-medium">
                Financials extracted from {extracted.data.sources.length} documented figure
                {extracted.data.sources.length > 1 ? "s" : ""}
                {extracted.data.fiscal_year ? ` · ${extracted.data.fiscal_year}` : ""}
              </p>
              <p className="text-xs text-muted-foreground">Click to pre-fill and review.</p>
            </div>
            <span className="text-sm font-medium text-success">Use →</span>
          </button>
        ) : null}

        <div className="flex flex-col gap-4">
          {GROUPS.map((g) => (
            <div key={g.title}>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {g.title}
              </p>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {g.fields.map((f) => (
                  <div key={f.key} className="flex flex-col gap-1">
                    <Label htmlFor={f.key} className="text-xs">
                      {f.label}
                    </Label>
                    <Input
                      id={f.key}
                      inputMode="numeric"
                      value={values[f.key] ?? ""}
                      onChange={(e) => set(f.key, e.target.value)}
                      className="font-mono"
                      placeholder="₹"
                    />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        <DialogFooter className="sm:justify-between">
          <Button type="button" variant="outline" onClick={() => setValues(DEMO)}>
            <Sparkles /> Load demo figures
          </Button>
          <Button onClick={onStart} disabled={startRun.isPending}>
            {startRun.isPending ? "Starting…" : "Start appraisal"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
