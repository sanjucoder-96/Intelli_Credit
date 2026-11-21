"use client";

import { AlertCircle, Check, Loader2, PauseCircle } from "lucide-react";
import * as React from "react";

import { titleCase } from "@/lib/format";
import type { NodeStatus, RunStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const NODE_LABELS: Record<string, string> = {
  financial_analysis: "Financial",
  research: "Research",
  fraud_detection: "Fraud",
  human_review: "HITL Review",
  ml_default_risk: "ML Risk",
  risk_assessment: "Scoring",
  recommendation: "Recommend",
  cam_generator: "CAM",
};

function StepIcon({
  status,
  isGate,
  paused,
}: {
  status: NodeStatus["status"];
  isGate: boolean;
  paused: boolean;
}) {
  if (isGate && paused) {
    return <PauseCircle className="size-4 text-warning" />;
  }
  switch (status) {
    case "ok":
      return <Check className="size-4 text-success" />;
    case "error":
      return <AlertCircle className="size-4 text-destructive" />;
    case "running":
      return <Loader2 className="size-4 animate-spin text-foreground" />;
    default:
      return <span className="size-2 rounded-full bg-muted-foreground/40" />;
  }
}

export function WorkflowProgress({
  nodes,
  status,
}: {
  nodes: NodeStatus[];
  status: RunStatus;
}) {
  const paused = status === "paused_hitl";
  if (!nodes.length) return null;

  return (
    <div className="overflow-x-auto rounded-xl border bg-background px-4 py-3">
      <div className="flex min-w-max items-center">
        {nodes.map((n, i) => {
          const isGate = n.node === "human_review";
          const label = NODE_LABELS[n.node] ?? titleCase(n.node);
          const done = n.status === "ok";
          return (
            <React.Fragment key={n.node}>
              <div className="flex flex-col items-center gap-1.5 px-1">
                <div
                  className={cn(
                    "grid size-8 place-items-center rounded-full border",
                    done && "border-success/40 bg-success/10",
                    n.status === "error" && "border-destructive/40 bg-destructive/10",
                    isGate && paused && "border-warning/50 bg-warning/10",
                    n.status === "pending" && !(isGate && paused) && "bg-muted/40",
                  )}
                >
                  <StepIcon status={n.status} isGate={isGate} paused={paused} />
                </div>
                <span
                  className={cn(
                    "whitespace-nowrap text-[11px] font-medium",
                    done ? "text-foreground" : "text-muted-foreground",
                    isGate && paused && "text-warning",
                  )}
                >
                  {label}
                </span>
              </div>
              {i < nodes.length - 1 ? (
                <div
                  className={cn(
                    "mb-4 h-px w-8 flex-1 md:w-12",
                    done ? "bg-success/40" : "bg-border",
                  )}
                />
              ) : null}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
