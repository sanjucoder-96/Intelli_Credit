"use client";

import { FileText, Link2, ScanSearch } from "lucide-react";
import * as React from "react";

import { SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import { DocumentViewer } from "@/components/workspace/document-viewer";
import { FindingCard } from "@/components/workspace/finding-card";
import type { Box } from "@/components/workspace/pdf-highlight-viewer";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtRatioPct } from "@/lib/format";
import type { CaseFindings, EvidenceItem } from "@/lib/types";

function boxesOf(e: EvidenceItem): Box[] {
  const raw = e.bboxes?.bboxes;
  return Array.isArray(raw) ? raw : [];
}

export function EvidencePanel({
  findings,
  caseId,
}: {
  findings: CaseFindings | undefined;
  caseId: string;
}) {
  const evidence = findings?.evidence ?? [];
  const items = findings?.findings ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Card className="p-5">
        <SectionTitle
          title="Evidence ledger"
          description="Every material value traces to its source document, page, and cited region."
        />
        {evidence.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Claim</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Cited text</TableHead>
                <TableHead className="text-right">Confidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {evidence.map((e) => {
                const hasRegion = e.bboxes && Object.keys(e.bboxes).length > 0;
                return (
                  <TableRow key={e.id}>
                    <TableCell className="max-w-[16rem]">
                      <p className="truncate font-medium">{e.claim}</p>
                      <Badge variant="outline" className="mt-1 text-[10px] capitalize">
                        {e.kind}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {e.url ? (
                        <a
                          href={e.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-sm hover:underline"
                        >
                          <Link2 className="size-3.5" /> web
                        </a>
                      ) : e.document_id ? (
                        <DocumentViewer
                          caseId={caseId}
                          documentId={e.document_id}
                          page={e.page}
                          boxes={boxesOf(e)}
                          quote={e.quote}
                          title={e.claim}
                        >
                          <button className="inline-flex items-center gap-1.5 text-sm hover:underline">
                            <FileText className="size-3.5 text-muted-foreground" />
                            {e.page !== null ? `p.${e.page}` : "document"}
                            {hasRegion ? (
                              <Badge variant="secondary" className="gap-1 text-[10px]">
                                <ScanSearch className="size-3" /> region
                              </Badge>
                            ) : null}
                          </button>
                        </DocumentViewer>
                      ) : (
                        <span className="text-muted-foreground">derived</span>
                      )}
                    </TableCell>
                    <TableCell className="max-w-[18rem]">
                      <p className="truncate font-mono text-xs text-muted-foreground">
                        {e.quote ?? "—"}
                      </p>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {fmtRatioPct(e.confidence)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        ) : (
          <EmptyState
            icon={ScanSearch}
            title="No evidence ledger yet"
            description="Evidence is captured when documents are ingested and validators run. Upload statements to populate it."
          />
        )}
      </Card>

      <Card className="p-5">
        <SectionTitle
          title="Findings"
          description="Append-only analytical claims across the six-category risk taxonomy."
        />
        {items.length ? (
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
            {items.map((f) => (
              <FindingCard key={f.id} finding={f} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No findings recorded for this case yet.
          </p>
        )}
      </Card>
    </div>
  );
}
