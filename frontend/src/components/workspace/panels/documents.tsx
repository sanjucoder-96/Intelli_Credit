"use client";

import {
  AlertCircle,
  Check,
  FileText,
  Loader2,
  Lock,
  Upload,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { SectionTitle } from "@/components/shared/stat";
import { EmptyState } from "@/components/shared/states";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import { titleCase, toneStyle } from "@/lib/format";
import { useDocuments, useExtractedFields, useUploadDocuments } from "@/lib/hooks";
import type { DocumentRow, StageStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.csv,.xlsx,.xls,.docx,.zip,.eml,.txt";

function docTone(status: string) {
  if (status === "ready") return "success" as const;
  if (status === "failed") return "destructive" as const;
  if (status === "needs_password") return "warning" as const;
  return "muted" as const;
}

function StagePill({ stage }: { stage: StageStatus }) {
  const done = stage.status === "done" || stage.status === "ok";
  const failed = stage.status === "failed" || stage.status === "error";
  const running = stage.status === "running";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
        done && "border-success/40 text-success",
        failed && "border-destructive/40 text-destructive",
        running && "border-foreground/30",
        !done && !failed && !running && "text-muted-foreground",
      )}
      title={stage.error ?? undefined}
    >
      {done ? (
        <Check className="size-3" />
      ) : failed ? (
        <AlertCircle className="size-3" />
      ) : running ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <span className="size-1.5 rounded-full bg-muted-foreground/40" />
      )}
      {titleCase(stage.stage)}
    </span>
  );
}

function FieldsTable({ caseId, documentId }: { caseId: string; documentId: string }) {
  const { data, isLoading } = useExtractedFields(caseId, documentId);
  if (isLoading) return <p className="text-xs text-muted-foreground">Loading extracted fields…</p>;
  if (!data?.length)
    return <p className="text-xs text-muted-foreground">No fields extracted yet.</p>;
  return (
    <div className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
      {data.map((f) => (
        <div key={f.id} className="flex items-baseline justify-between gap-2 text-xs">
          <span className="text-muted-foreground">{f.field_path}</span>
          <span className="flex items-center gap-1.5 font-mono">
            {String(f.value ?? "—")}
            {f.page !== null ? (
              <Badge variant="secondary" className="text-[9px]">
                p.{f.page}
              </Badge>
            ) : null}
          </span>
        </div>
      ))}
    </div>
  );
}

function DocCard({ caseId, doc }: { caseId: string; doc: DocumentRow }) {
  return (
    <AccordionItem value={doc.id} className="rounded-lg border px-3">
      <AccordionTrigger className="hover:no-underline">
        <div className="flex flex-1 items-center gap-3 pr-2 text-left">
          <FileText className="size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{doc.filename}</p>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {doc.doc_type && doc.doc_type !== "unclassified" ? (
                <Badge variant="outline" className="text-[10px]">
                  {titleCase(doc.doc_type)}
                </Badge>
              ) : null}
              {doc.stages.map((s, i) => (
                <StagePill key={i} stage={s} />
              ))}
            </div>
          </div>
          {doc.status === "needs_password" ? (
            <Lock className="size-4 text-warning" />
          ) : null}
          <Badge variant={toneStyle(docTone(doc.status)).badge} className="shrink-0">
            {titleCase(doc.status)}
          </Badge>
        </div>
      </AccordionTrigger>
      <AccordionContent>
        <div className="rounded-md bg-muted/40 p-3">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Extracted fields
          </p>
          <FieldsTable caseId={caseId} documentId={doc.id} />
        </div>
      </AccordionContent>
    </AccordionItem>
  );
}

export function DocumentsPanel({ caseId, canUpload }: { caseId: string; canUpload: boolean }) {
  const docs = useDocuments(caseId);
  const upload = useUploadDocuments(caseId);
  const [dragging, setDragging] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  async function send(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    try {
      await upload.mutateAsync(list);
      toast.success(`Uploaded ${list.length} document${list.length > 1 ? "s" : ""} — ingesting…`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Upload failed.");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {canUpload ? (
        <Card
          className={cn(
            "border-2 border-dashed p-6 transition-colors",
            dragging && "border-primary bg-primary/5",
          )}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            void send(e.dataTransfer.files);
          }}
        >
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="flex w-full flex-col items-center gap-1.5 text-center"
          >
            {upload.isPending ? (
              <Loader2 className="size-6 animate-spin text-muted-foreground" />
            ) : (
              <Upload className="size-6 text-muted-foreground" />
            )}
            <span className="text-sm font-medium">
              {upload.isPending ? "Uploading…" : "Upload borrower documents"}
            </span>
            <span className="text-xs text-muted-foreground">
              GST returns, ITR, bank statements, annual reports, legal notices — PDF, images, XLSX, CSV
            </span>
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              if (e.target.files) void send(e.target.files);
              e.target.value = "";
            }}
          />
        </Card>
      ) : null}

      <Card className="p-4">
        <SectionTitle
          title="Documents"
          description="Each upload runs the ingestion pipeline: classify → OCR → extract → validate."
        />
        {docs.isLoading ? (
          <p className="py-6 text-sm text-muted-foreground">Loading documents…</p>
        ) : !docs.data?.length ? (
          <EmptyState
            icon={FileText}
            title="No documents uploaded"
            description={
              canUpload
                ? "Upload the borrower's financial and compliance documents to begin ingestion."
                : "An analyst can upload documents for this case."
            }
          />
        ) : (
          <Accordion type="multiple" className="flex flex-col gap-2">
            {docs.data.map((doc) => (
              <DocCard key={doc.id} caseId={caseId} doc={doc} />
            ))}
          </Accordion>
        )}
      </Card>
    </div>
  );
}
