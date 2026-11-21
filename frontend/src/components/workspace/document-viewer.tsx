"use client";

import { Loader2 } from "lucide-react";
import * as React from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { PdfHighlightViewer, type Box } from "@/components/workspace/pdf-highlight-viewer";
import { ApiError, apiBlob } from "@/lib/api";

export function DocumentViewer({
  caseId,
  documentId,
  page,
  boxes,
  quote,
  title,
  children,
}: {
  caseId: string;
  documentId: string;
  page: number | null;
  boxes: Box[];
  quote?: string | null;
  title?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  const [src, setSrc] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const pageNo = page ?? 1;

  React.useEffect(() => {
    if (!open || src) return;
    let cancelled = false;
    let url: string | null = null;
    (async () => {
      try {
        const blob = await apiBlob(
          `/api/v1/cases/${caseId}/documents/${documentId}/page/${pageNo}.png`,
        );
        url = URL.createObjectURL(blob);
        if (!cancelled) setSrc(url);
        else URL.revokeObjectURL(url);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof ApiError ? err.message : "Could not load the document page.");
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [open, src, caseId, documentId, pageNo]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-base">{title ?? "Source document"}</DialogTitle>
        </DialogHeader>
        {quote ? (
          <p className="rounded-md bg-warning/10 p-2.5 font-mono text-xs text-warning-foreground/90">
            &ldquo;{quote}&rdquo;{page !== null ? ` — page ${page}` : ""}
          </p>
        ) : null}
        {error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : src ? (
          <div className="flex justify-center">
            <PdfHighlightViewer src={src} boxes={boxes} />
          </div>
        ) : (
          <div className="grid h-64 place-items-center">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
