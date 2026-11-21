import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

export function LoadingRow({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2 py-8 text-sm text-muted-foreground", className)}>
      <Loader2 className="size-4 animate-spin" /> Loading…
    </div>
  );
}

export function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2 p-1">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-4 w-full" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ error, className }: { error: unknown; className?: string }) {
  const message =
    error instanceof ApiError
      ? error.status === 0
        ? "The API is unreachable. Is the backend running?"
        : error.message
      : "Something went wrong.";
  return (
    <div
      className={cn(
        "flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive",
        className,
      )}
      role="alert"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div>
        <p className="font-medium">Could not load this data.</p>
        <p className="text-destructive/80">{message}</p>
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  icon: Icon = Inbox,
  action,
  className,
}: {
  title: string;
  description?: string;
  icon?: React.ComponentType<{ className?: string }>;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-muted/20 px-6 py-12 text-center",
        className,
      )}
    >
      <Icon className="size-6 text-muted-foreground/70" />
      <p className="text-sm font-medium">{title}</p>
      {description ? (
        <p className="max-w-sm text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

/** A compact "not available" marker for a single missing value — never faked. */
export function Unavailable({ label = "Not available" }: { label?: string }) {
  return <span className="text-muted-foreground">{label}</span>;
}
