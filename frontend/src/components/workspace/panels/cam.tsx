"use client";

import { Download, FileDown, FileText } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { DecisionBadge, GradeBadge, SeverityBadge } from "@/components/shared/risk";
import { KeyValue } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { ApiError, apiBlob } from "@/lib/api";
import { fmtInrFull, fmtNum, fmtPct, fmtRatioPct, titleCase } from "@/lib/format";
import type { CreditC, RunReport } from "@/lib/types";

function Section({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="scroll-mt-20">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold tracking-tight">
        <span className="font-mono text-xs text-muted-foreground">{String(n).padStart(2, "0")}</span>
        {title}
      </h3>
      <div className="text-sm leading-relaxed">{children}</div>
    </section>
  );
}

export function CamPanel({
  report,
  runId,
}: {
  report: RunReport | undefined;
  runId: string | undefined;
}) {
  const [downloading, setDownloading] = React.useState(false);

  async function downloadPdf() {
    if (!runId) return;
    setDownloading(true);
    try {
      const blob = await apiBlob(`/api/v1/runs/${runId}/report/export?format=pdf`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `CAM-${report?.ir.company.legal_name.replace(/\s+/g, "-") ?? "report"}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Could not generate the PDF.");
    } finally {
      setDownloading(false);
    }
  }

  if (!report) {
    return (
      <EmptyState
        icon={FileText}
        title="Credit Appraisal Memo not generated"
        description="The CAM is assembled deterministically once the appraisal completes. Numbers come from the risk engine — the LLM only writes the executive summary."
      />
    );
  }

  const ir = report.ir;
  const fiveCs = Object.values(ir.five_cs ?? {}).filter(
    (c): c is CreditC => !!c && typeof c === "object" && "name" in c,
  );
  const ratios = Object.entries(ir.ratios ?? {}).filter(([, v]) => v.available && v.value !== null);

  function download() {
    const blob = new Blob([report!.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `CAM-${ir.company.legal_name.replace(/\s+/g, "-")}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Card className="p-6 md:p-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
            Credit Appraisal Memo
          </p>
          <h2 className="mt-1 text-xl font-semibold tracking-tight">{ir.company.legal_name}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {[ir.company.sector, ir.company.gstin, ir.company.cin].filter(Boolean).join(" · ")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <GradeBadge grade={ir.grade} />
          <DecisionBadge decision={ir.decision} />
          <Button variant="outline" size="sm" onClick={download}>
            <Download /> Markdown
          </Button>
          <Button size="sm" onClick={downloadPdf} disabled={downloading || !runId}>
            <FileDown /> {downloading ? "Generating…" : "Download PDF"}
          </Button>
        </div>
      </div>

      <Separator className="my-5" />

      <div className="grid grid-cols-1 gap-x-10 gap-y-6 lg:grid-cols-2">
        <Section n={1} title="Executive summary">
          <p className="text-muted-foreground">
            {ir.executive_summary || "—"}
          </p>
        </Section>

        <Section n={2} title="Recommendation">
          <dl className="rounded-lg border p-3">
            <KeyValue label="Decision">
              <DecisionBadge decision={ir.decision} />
            </KeyValue>
            <KeyValue label="Grade">
              <GradeBadge grade={ir.grade} />
            </KeyValue>
            <KeyValue label="Score" mono>
              {fmtNum(ir.final_score, 1)} · coverage {fmtRatioPct(ir.coverage)}
            </KeyValue>
            <KeyValue label="Recommended limit" mono>
              {fmtInrFull(ir.recommended_limit)}
            </KeyValue>
            <KeyValue label="Indicative rate" mono>
              {ir.interest_rate_pct !== null ? fmtPct(ir.interest_rate_pct) : "—"}
            </KeyValue>
          </dl>
          {ir.rate_build_up ? (
            <p className="mt-2 font-mono text-[11px] text-muted-foreground">{ir.rate_build_up}</p>
          ) : null}
        </Section>

        <Section n={3} title="Five Cs of credit">
          {fiveCs.length ? (
            <div className="flex flex-col gap-2">
              {fiveCs.map((c) => (
                <div key={c.name} className="flex items-baseline justify-between gap-3 border-b py-1.5 last:border-0">
                  <div>
                    <span className="font-medium">{titleCase(c.name)}</span>
                    {c.drivers?.length ? (
                      <span className="ml-2 text-xs text-muted-foreground">
                        {c.drivers.join(", ")}
                      </span>
                    ) : null}
                  </div>
                  <span className="font-mono text-sm tabular-nums">
                    {c.score !== null ? `${fmtNum(c.score, 0)}/100` : "n/a"}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground">Not assessed.</p>
          )}
        </Section>

        <Section n={4} title="Financial ratios">
          {ratios.length ? (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              {ratios.map(([name, v]) => (
                <div key={name} className="flex justify-between gap-2 text-sm">
                  <span className="text-muted-foreground">{titleCase(name)}</span>
                  <span className="font-mono tabular-nums">{fmtNum(v.value, 2)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground">No ratios available.</p>
          )}
        </Section>

        <Section n={5} title="Key findings & risks">
          {ir.findings?.length ? (
            <ul className="flex flex-col gap-1.5">
              {ir.findings.map((f, i) => (
                <li key={i} className="flex items-start gap-2">
                  <SeverityBadge severity={f.severity} />
                  <span>
                    {f.title}
                    {f.narrative ? (
                      <span className="text-muted-foreground"> — {f.narrative}</span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground">No adverse findings recorded.</p>
          )}
        </Section>

        <Section n={6} title="ML default-risk & overlays">
          <div className="flex flex-col gap-1.5">
            {ir.overlay_log?.filter((o) => o.fired).length ? (
              ir.overlay_log
                .filter((o) => o.fired)
                .map((o, i) => (
                  <div key={i} className="flex items-center gap-2 text-sm">
                    <span className="font-mono text-xs text-muted-foreground">{o.effect}</span>
                    <span>{titleCase(o.rule_id)}</span>
                    {o.detail ? <span className="text-muted-foreground">— {o.detail}</span> : null}
                  </div>
                ))
            ) : (
              <p className="text-muted-foreground">No overlays applied.</p>
            )}
          </div>
        </Section>
      </div>

      {ir.notes?.length ? (
        <>
          <Separator className="my-5" />
          <div className="text-xs text-muted-foreground">
            {ir.notes.map((n, i) => (
              <p key={i}>· {n}</p>
            ))}
          </div>
        </>
      ) : null}

      <p className="mt-5 rounded-md bg-muted/50 p-2.5 text-[11px] text-muted-foreground">
        This memo is assembled from the deterministic risk engine (ADR-0009/0010). Every figure is
        persisted, not recomputed in the browser; the LLM writes only the executive-summary prose.
        PDF/DOCX export via the report renderer is a backend follow-up.
      </p>
    </Card>
  );
}
