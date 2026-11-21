"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, Moon, RefreshCw, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  apiBaseUrl,
  apiGet,
  type HealthResponse,
  type ReadyResponse,
} from "@/lib/api";

/**
 * Platform status board - the Phase-2 proof that the FE <-> BE contract
 * works: TanStack Query polls GET /api/v1/health and GET /api/v1/ready and
 * renders the results as status cards, plus the next-themes dark-mode toggle.
 * The live run timeline (SSE) replaces polling for run data in Phase 5.
 */

const POLL_INTERVAL_MS = 15_000;

function useHealthQuery() {
  return useQuery({
    queryKey: ["platform", "health"],
    queryFn: () => apiGet<HealthResponse>("/api/v1/health"),
    refetchInterval: POLL_INTERVAL_MS,
  });
}

function useReadyQuery() {
  return useQuery({
    queryKey: ["platform", "ready"],
    queryFn: () => apiGet<ReadyResponse>("/api/v1/ready"),
    refetchInterval: POLL_INTERVAL_MS,
    retry: false,
  });
}

function StatusBadge({ ok, label }: { ok: boolean; label: string }) {
  return <Badge variant={ok ? "success" : "destructive"}>{label}</Badge>;
}

function errorSummary(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status === 0
      ? error.message
      : `${error.code} (HTTP ${error.status}): ${error.message}`;
  }
  return error instanceof Error ? error.message : "Unknown error";
}

/** Pull per-check booleans out of a 503 error envelope's details, if any. */
function checksFromError(error: unknown): Record<string, boolean> | null {
  if (!(error instanceof ApiError) || error.details === null) {
    return null;
  }
  const checks = error.details["checks"];
  if (typeof checks !== "object" || checks === null) {
    return null;
  }
  const result: Record<string, boolean> = {};
  for (const [key, value] of Object.entries(checks)) {
    if (typeof value === "boolean") {
      result[key] = value;
    }
  }
  return Object.keys(result).length > 0 ? result : null;
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  // next-themes resolves the theme only on the client; render a stable
  // placeholder until mounted to avoid a hydration mismatch.
  React.useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <Button variant="outline" size="icon" aria-label="Toggle theme" disabled>
        <Sun aria-hidden="true" />
      </Button>
    );
  }

  const isDark = resolvedTheme === "dark";
  return (
    <Button
      variant="outline"
      size="icon"
      aria-label="Toggle theme"
      onClick={() => setTheme(isDark ? "light" : "dark")}
    >
      {isDark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
    </Button>
  );
}

function HealthCard() {
  const health = useHealthQuery();

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <Activity className="size-4" aria-hidden="true" />
            API health
          </CardTitle>
          {health.isPending ? (
            <Badge variant="secondary">checking…</Badge>
          ) : (
            <StatusBadge
              ok={health.isSuccess}
              label={health.isSuccess ? "up" : "down"}
            />
          )}
        </div>
        <CardDescription>GET /api/v1/health (liveness)</CardDescription>
      </CardHeader>
      <CardContent>
        {health.isSuccess ? (
          <dl className="grid grid-cols-3 gap-2 text-sm">
            <div>
              <dt className="text-muted-foreground">Status</dt>
              <dd className="font-medium">{health.data.status}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Version</dt>
              <dd className="font-medium">{health.data.version}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Environment</dt>
              <dd className="font-medium">{health.data.environment}</dd>
            </div>
          </dl>
        ) : health.isError ? (
          <p className="text-sm text-destructive">
            {errorSummary(health.error)}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">Contacting API…</p>
        )}
      </CardContent>
    </Card>
  );
}

function ReadyCard() {
  const ready = useReadyQuery();
  const failureChecks = ready.isError ? checksFromError(ready.error) : null;
  const checks = ready.isSuccess ? ready.data.checks : failureChecks;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2">
            <Activity className="size-4" aria-hidden="true" />
            API readiness
          </CardTitle>
          {ready.isPending ? (
            <Badge variant="secondary">checking…</Badge>
          ) : (
            <StatusBadge
              ok={ready.isSuccess}
              label={ready.isSuccess ? "ready" : "not ready"}
            />
          )}
        </div>
        <CardDescription>
          GET /api/v1/ready (database + redis checks)
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {checks !== null && (
          <ul className="flex flex-wrap gap-2">
            {Object.entries(checks).map(([name, ok]) => (
              <li key={name} className="flex items-center gap-1.5 text-sm">
                <span className="text-muted-foreground">{name}</span>
                <StatusBadge ok={ok} label={ok ? "ok" : "failing"} />
              </li>
            ))}
          </ul>
        )}
        {ready.isError && checks === null && (
          <p className="text-sm text-destructive">
            {errorSummary(ready.error)}
          </p>
        )}
        {ready.isPending && (
          <p className="text-sm text-muted-foreground">Contacting API…</p>
        )}
      </CardContent>
    </Card>
  );
}

export function PlatformStatus() {
  const health = useHealthQuery();
  const ready = useReadyQuery();
  const refetching = health.isFetching || ready.isFetching;

  return (
    <section className="flex flex-col gap-4" aria-label="Platform status">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          API base:{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
            {apiBaseUrl()}
          </code>
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void health.refetch();
              void ready.refetch();
            }}
            disabled={refetching}
          >
            <RefreshCw
              className={refetching ? "animate-spin" : undefined}
              aria-hidden="true"
            />
            Refresh
          </Button>
          <ThemeToggle />
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <HealthCard />
        <ReadyCard />
      </div>
    </section>
  );
}
