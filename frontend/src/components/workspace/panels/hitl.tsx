"use client";

import { ArrowRight, Plus, UserCheck, X } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { canReview, useAuth } from "@/lib/auth";
import { useSubmitHitl } from "@/lib/hooks";
import type { Assessment, RunStatus } from "@/lib/types";

const IMPACTS: { label: string; notch: number }[] = [
  { label: "Strong positive (+8)", notch: 8 },
  { label: "Positive (+4)", notch: 4 },
  { label: "Neutral (0)", notch: 0 },
  { label: "Concern (−4)", notch: -4 },
  { label: "Serious concern (−8)", notch: -8 },
];

const KNOCKOUTS = [
  { id: "wilful_defaulter", label: "Wilful defaulter" },
  { id: "fraud_confirmed", label: "Confirmed fraud" },
  { id: "sanctions_hit", label: "Sanctions / adverse list" },
];

interface ObsRow {
  label: string;
  notch: number;
}

export function HitlPanel({
  runId,
  caseId,
  status,
  assessment,
}: {
  runId: string;
  caseId: string;
  status: RunStatus;
  assessment: Assessment | null;
}) {
  const role = useAuth((s) => s.user?.role);
  const submit = useSubmitHitl(runId, caseId);
  const [rows, setRows] = React.useState<ObsRow[]>([{ label: "", notch: 0 }]);
  const [kos, setKos] = React.useState<Set<string>>(new Set());

  const paused = status === "paused_hitl";

  if (!paused) {
    const applied = assessment?.overlays.find((o) => o.rule_id === "qualitative_notch" && o.fired);
    return (
      <EmptyState
        icon={UserCheck}
        title="Not awaiting review"
        description={
          applied
            ? `Officer observations were applied: ${applied.detail} (${applied.effect}).`
            : "This run is not paused for human review."
        }
      />
    );
  }

  if (!canReview(role)) {
    return (
      <EmptyState
        icon={UserCheck}
        title="Awaiting Credit Officer review"
        description="This appraisal is paused for a Credit Officer. Your role can view it but not submit the review."
      />
    );
  }

  async function onSubmit() {
    const notes = rows
      .filter((r) => r.label.trim())
      .map((r) => ({ label: r.label.trim(), notch: r.notch }));
    try {
      await submit.mutateAsync({ notes, knockouts: [...kos] });
      toast.success("Review submitted — the workflow is resuming and will re-score.");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not submit the review.");
    }
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="border-warning/40 bg-warning/5 p-5 lg:col-span-2">
        <SectionTitle
          title="Review required"
          description="Record your primary due-diligence observations. Each maps to a bounded scorecard notch — you never set the score yourself."
        />

        <div className="flex flex-col gap-2.5">
          {rows.map((row, i) => (
            <div key={i} className="flex items-end gap-2">
              <div className="flex-1">
                {i === 0 ? <Label className="mb-1 text-xs">Observation</Label> : null}
                <Input
                  value={row.label}
                  placeholder="e.g. Factory operating at 40% capacity"
                  onChange={(e) =>
                    setRows((r) => r.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))
                  }
                />
              </div>
              <div className="w-44">
                {i === 0 ? <Label className="mb-1 text-xs">Impact</Label> : null}
                <select
                  value={row.notch}
                  onChange={(e) =>
                    setRows((r) =>
                      r.map((x, j) => (j === i ? { ...x, notch: Number(e.target.value) } : x)),
                    )
                  }
                  className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  {IMPACTS.map((imp) => (
                    <option key={imp.notch} value={imp.notch}>
                      {imp.label}
                    </option>
                  ))}
                </select>
              </div>
              {rows.length > 1 ? (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setRows((r) => r.filter((_, j) => j !== i))}
                >
                  <X />
                </Button>
              ) : null}
            </div>
          ))}
          <Button
            variant="ghost"
            size="sm"
            className="self-start gap-1.5"
            onClick={() => setRows((r) => [...r, { label: "", notch: 0 }])}
          >
            <Plus className="size-3.5" /> Add observation
          </Button>
        </div>

        <div className="mt-4">
          <Label className="mb-1.5 block text-xs">Knockouts (force decline)</Label>
          <div className="flex flex-wrap gap-2">
            {KNOCKOUTS.map((k) => {
              const on = kos.has(k.id);
              return (
                <button
                  key={k.id}
                  type="button"
                  onClick={() =>
                    setKos((s) => {
                      const next = new Set(s);
                      if (next.has(k.id)) next.delete(k.id);
                      else next.add(k.id);
                      return next;
                    })
                  }
                  className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                    on
                      ? "border-destructive bg-destructive/10 text-destructive"
                      : "border-input text-muted-foreground hover:bg-muted"
                  }`}
                >
                  {k.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="mt-5 flex justify-end">
          <Button onClick={onSubmit} disabled={submit.isPending}>
            {submit.isPending ? "Submitting…" : "Submit review"}
          </Button>
        </div>
      </Card>

      <Card className="p-5">
        <SectionTitle title="What happens next" />
        <ol className="flex flex-col gap-3 text-sm">
          {[
            "Your observations are recorded as evidence",
            "The workflow resumes from the checkpoint",
            "The scorecard re-scores with a bounded notch",
            "The decision, limit, and rate update",
          ].map((step, i) => (
            <li key={i} className="flex items-start gap-2.5">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-secondary font-mono text-[11px]">
                {i + 1}
              </span>
              <span>{step}</span>
              {i < 3 ? <ArrowRight className="ml-auto size-3.5 text-muted-foreground" /> : null}
            </li>
          ))}
        </ol>
        <p className="mt-4 rounded-md bg-muted/50 p-2.5 text-xs text-muted-foreground">
          The notch is clamped to ±12 and a fraud cap is a hard ceiling — human input shapes the
          score within policy, never overrides it.
        </p>
      </Card>
    </div>
  );
}
