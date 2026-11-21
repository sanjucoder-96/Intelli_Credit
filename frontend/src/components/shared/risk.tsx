import * as React from "react";

import { Badge } from "@/components/ui/badge";
import {
  decisionTone,
  gradeTone,
  severityTone,
  statusLabel,
  statusTone,
  toneStyle,
  type Tone,
} from "@/lib/format";
import type { Decision, Severity } from "@/lib/types";
import { cn } from "@/lib/utils";

export function GradeBadge({ grade, className }: { grade: string | null; className?: string }) {
  if (!grade) return <Badge variant="outline" className={className}>Ungraded</Badge>;
  return (
    <Badge variant={toneStyle(gradeTone(grade)).badge} className={cn("font-mono", className)}>
      {grade}
    </Badge>
  );
}

export function DecisionBadge({
  decision,
  className,
}: {
  decision: Decision | string | null;
  className?: string;
}) {
  if (!decision) return <Badge variant="secondary" className={className}>Pending</Badge>;
  return (
    <Badge variant={toneStyle(decisionTone(decision)).badge} className={cn("uppercase", className)}>
      {decision}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: Severity | string }) {
  return (
    <Badge variant={toneStyle(severityTone(severity)).badge} className="capitalize">
      {severity}
    </Badge>
  );
}

export function StatusBadge({ status }: { status: string | null }) {
  return (
    <Badge variant={toneStyle(statusTone(status)).badge}>{statusLabel(status)}</Badge>
  );
}

/** A coloured dot for compact status columns. */
export function ToneDot({ tone, className }: { tone: Tone; className?: string }) {
  return <span className={cn("inline-block size-2 rounded-full", toneStyle(tone).bg, className)} />;
}

export function DirectionArrow({ direction }: { direction: "increases" | "decreases" }) {
  const up = direction === "increases";
  return (
    <span className={cn("font-mono", up ? "text-destructive" : "text-success")} aria-hidden>
      {up ? "↑" : "↓"}
    </span>
  );
}
