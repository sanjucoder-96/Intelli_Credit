import { describe, expect, it } from "vitest";

import {
  decisionTone,
  fmtInr,
  fmtPct,
  fmtRatioPct,
  gradeTone,
  pdTone,
  severityTone,
  statusLabel,
  statusTone,
  toNumber,
} from "@/lib/format";

describe("indian money formatting", () => {
  it("formats crore / lakh / grouped", () => {
    expect(fmtInr(18750000)).toBe("₹1.88 Cr");
    expect(fmtInr("672400")).toBe("₹6.72 L");
    expect(fmtInr(5000)).toBe("₹5,000");
  });
  it("returns an em dash for missing values (never fabricates)", () => {
    expect(fmtInr(null)).toBe("—");
    expect(fmtInr(undefined)).toBe("—");
    expect(fmtPct(null)).toBe("—");
    expect(fmtRatioPct(null)).toBe("—");
  });
  it("formats percentages", () => {
    expect(fmtPct(10.5)).toBe("10.5%");
    expect(fmtRatioPct(0.0278, 1)).toBe("2.8%");
    expect(fmtRatioPct(1)).toBe("100%");
  });
  it("coerces numbers safely", () => {
    expect(toNumber("42")).toBe(42);
    expect(toNumber("nope")).toBeNull();
    expect(toNumber(null)).toBeNull();
  });
});

describe("risk-state tone system", () => {
  it("maps grades to green / amber / red", () => {
    expect(gradeTone("IC1")).toBe("success");
    expect(gradeTone("IC3")).toBe("success");
    expect(gradeTone("IC5")).toBe("warning");
    expect(gradeTone("IC8")).toBe("destructive");
    expect(gradeTone(null)).toBe("muted");
  });
  it("maps decisions", () => {
    expect(decisionTone("approve")).toBe("success");
    expect(decisionTone("refer")).toBe("warning");
    expect(decisionTone("decline")).toBe("destructive");
    expect(decisionTone(null)).toBe("muted");
  });
  it("maps severities", () => {
    expect(severityTone("critical")).toBe("destructive");
    expect(severityTone("high")).toBe("destructive");
    expect(severityTone("medium")).toBe("warning");
    expect(severityTone("low")).toBe("muted");
  });
  it("maps PD around the ~5% base rate", () => {
    expect(pdTone(0.03)).toBe("success");
    expect(pdTone(0.1)).toBe("warning");
    expect(pdTone(0.3)).toBe("destructive");
    expect(pdTone(null)).toBe("muted");
  });
  it("maps run status", () => {
    expect(statusTone("completed")).toBe("success");
    expect(statusTone("paused_hitl")).toBe("warning");
    expect(statusTone("failed")).toBe("destructive");
    expect(statusLabel("paused_hitl")).toBe("Paused Hitl");
  });
});
