import * as React from "react";

import { toneStyle, type Tone } from "@/lib/format";
import { cn } from "@/lib/utils";

const TONE_STROKE: Record<Tone, string> = {
  success: "var(--color-success)",
  warning: "var(--color-warning)",
  destructive: "var(--color-destructive)",
  muted: "var(--color-muted-foreground)",
  default: "var(--color-foreground)",
};

function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
  const rad = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy - r * Math.sin(rad)];
}

function arcPath(cx: number, cy: number, r: number, startDeg: number, endDeg: number): string {
  const [sx, sy] = polar(cx, cy, r, startDeg);
  const [ex, ey] = polar(cx, cy, r, endDeg);
  return `M ${sx} ${sy} A ${r} ${r} 0 0 1 ${ex} ${ey}`;
}

/** Semicircular 0–100 score gauge; arc coloured by tone. */
export function ScoreGauge({
  value,
  tone = "default",
  label,
  sublabel,
}: {
  value: number | null;
  tone?: Tone;
  label?: string;
  sublabel?: string;
}) {
  const cx = 100;
  const cy = 100;
  const r = 82;
  const f = value === null ? 0 : Math.max(0, Math.min(100, value)) / 100;
  const endDeg = 180 - f * 180;
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 200 116" className="w-full max-w-[220px]" role="img">
        <path
          d={arcPath(cx, cy, r, 180, 0)}
          fill="none"
          stroke="var(--color-muted)"
          strokeWidth={14}
          strokeLinecap="round"
        />
        {value !== null ? (
          <path
            d={arcPath(cx, cy, r, 180, endDeg)}
            fill="none"
            stroke={TONE_STROKE[tone]}
            strokeWidth={14}
            strokeLinecap="round"
          />
        ) : null}
        <text
          x={cx}
          y={cy - 6}
          textAnchor="middle"
          className="fill-foreground font-mono"
          style={{ fontSize: 30, fontWeight: 600 }}
        >
          {value === null ? "—" : value.toFixed(1)}
        </text>
        {label ? (
          <text
            x={cx}
            y={cy + 12}
            textAnchor="middle"
            className="fill-muted-foreground"
            style={{ fontSize: 11 }}
          >
            {label}
          </text>
        ) : null}
      </svg>
      {sublabel ? <p className="-mt-1 text-xs text-muted-foreground">{sublabel}</p> : null}
    </div>
  );
}

/** Horizontal 0–100 bar with a tone fill (factor normalized scores). */
export function HBar({
  value,
  tone = "default",
  className,
}: {
  value: number | null;
  tone?: Tone;
  className?: string;
}) {
  const f = value === null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}>
      <div
        className={cn("h-full rounded-full transition-all", toneStyle(tone).bg)}
        style={{ width: `${f}%` }}
      />
    </div>
  );
}

/** Bipolar SHAP contribution bar: right = raises risk, left = lowers risk. */
export function ContributionBar({ value, max }: { value: number; max: number }) {
  const denom = max <= 0 ? 1 : max;
  const frac = Math.max(-1, Math.min(1, value / denom));
  const pct = Math.abs(frac) * 50;
  const raises = value > 0;
  return (
    <div className="relative h-2.5 w-full rounded-full bg-muted">
      <div className="absolute left-1/2 top-0 h-full w-px bg-border" />
      <div
        className={cn(
          "absolute top-0 h-full rounded-full",
          raises ? "bg-destructive" : "bg-success",
        )}
        style={
          raises
            ? { left: "50%", width: `${pct}%` }
            : { right: "50%", width: `${pct}%` }
        }
      />
    </div>
  );
}

/** A labelled stacked distribution bar (grade / decision mix). */
export function StackBar({
  segments,
}: {
  segments: { label: string; count: number; tone: Tone }[];
}) {
  const total = segments.reduce((s, x) => s + x.count, 0) || 1;
  return (
    <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted">
      {segments
        .filter((s) => s.count > 0)
        .map((s) => (
          <div
            key={s.label}
            className={toneStyle(s.tone).bg}
            style={{ width: `${(s.count / total) * 100}%` }}
            title={`${s.label}: ${s.count}`}
          />
        ))}
    </div>
  );
}
