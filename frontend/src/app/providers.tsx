"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import * as React from "react";

/**
 * Client-side provider tree: TanStack Query (all server state, ADR-0014) and
 * next-themes (class-strategy dark mode, which will also drive the ECharts
 * theme pair in a later phase).
 *
 * The QueryClient is created once per browser session inside useState so a
 * re-render never rebuilds the cache. Defaults are deliberately conservative
 * for a dashboard app: short staleTime to keep status data fresh-ish without
 * hammering the API, a single retry, and no refetch-on-focus storms.
 * SSE-driven cache invalidation (run finished -> refetch findings) is wired
 * here when the run timeline lands in Phase 5.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
      >
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
