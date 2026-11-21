/**
 * Presentation helpers: Indian money formatting, ratios, dates, and the
 * risk-state tone system (grade / decision / severity → colour + label).
 * Display only — no business logic.
 */

import type { Decision, Severity } from "@/lib/types";

export function toNumber(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? n : null;
}

/** Indian corporate money: crore / lakh with ₹, or plain grouped for small values. */
export function fmtInr(v: string | number | null | undefined, currency = "₹"): string {
  const n = toNumber(v);
  if (n === null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e7) return `${currency}${(n / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `${currency}${(n / 1e5).toFixed(2)} L`;
  return `${currency}${n.toLocaleString("en-IN")}`;
}

export function fmtInrFull(v: string | number | null | undefined, currency = "₹"): string {
  const n = toNumber(v);
  if (n === null) return "—";
  return `${currency}${n.toLocaleString("en-IN")}`;
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

/** A 0..1 fraction rendered as a percentage. */
export function fmtRatioPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return fmtDate(iso);
}

// --- tone system ----------------------------------------------------------
export type Tone = "success" | "warning" | "destructive" | "muted" | "default";

export interface ToneStyle {
  tone: Tone;
  badge: "success" | "warning" | "destructive" | "secondary" | "outline";
  /** text/border colour class */
  text: string;
  bg: string;
}

const TONE_STYLE: Record<Tone, ToneStyle> = {
  success: { tone: "success", badge: "success", text: "text-success", bg: "bg-success" },
  warning: { tone: "warning", badge: "warning", text: "text-warning", bg: "bg-warning" },
  destructive: {
    tone: "destructive",
    badge: "destructive",
    text: "text-destructive",
    bg: "bg-destructive",
  },
  muted: { tone: "muted", badge: "secondary", text: "text-muted-foreground", bg: "bg-muted" },
  default: { tone: "default", badge: "outline", text: "text-foreground", bg: "bg-foreground" },
};

export function toneStyle(tone: Tone): ToneStyle {
  return TONE_STYLE[tone];
}

/** IC1–IC3 strong (green), IC4–IC6 watch (amber), IC7–IC10 weak (red). */
export function gradeTone(grade: string | null | undefined): Tone {
  if (!grade) return "muted";
  const n = Number(grade.replace(/[^0-9]/g, ""));
  if (!Number.isFinite(n)) return "muted";
  if (n <= 3) return "success";
  if (n <= 6) return "warning";
  return "destructive";
}

export function decisionTone(decision: Decision | string | null | undefined): Tone {
  switch (decision) {
    case "approve":
      return "success";
    case "refer":
      return "warning";
    case "decline":
      return "destructive";
    default:
      return "muted";
  }
}

export function severityTone(sev: Severity | string | null | undefined): Tone {
  switch (sev) {
    case "critical":
    case "high":
      return "destructive";
    case "medium":
      return "warning";
    case "low":
      return "muted";
    default:
      return "muted";
  }
}

export function statusTone(status: string | null | undefined): Tone {
  switch (status) {
    case "completed":
      return "success";
    case "paused_hitl":
    case "needs_attention":
      return "warning";
    case "failed":
    case "cancelled":
      return "destructive";
    case "running":
    case "pending":
      return "default";
    default:
      return "muted";
  }
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "—";
  return status
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** A PD (0..1) → risk tone, anchored on the model's ~5% base rate. */
export function pdTone(pd: number | null | undefined): Tone {
  if (pd === null || pd === undefined) return "muted";
  if (pd < 0.06) return "success";
  if (pd < 0.15) return "warning";
  return "destructive";
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
