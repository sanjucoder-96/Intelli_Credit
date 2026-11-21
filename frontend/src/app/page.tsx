import { PlatformStatus } from "@/components/platform-status";

/**
 * Landing page (server component shell).
 *
 * Phase 2 renders only the platform status board - the proof that the
 * FE <-> BE contract works end to end. The case list, run timeline, and
 * server-driven workflow dashboards replace this page from Phase 5 onward.
 */
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-4xl flex-col gap-8 px-6 py-12">
      <header className="flex flex-col gap-2">
        <p className="text-sm font-medium tracking-widest text-muted-foreground uppercase">
          IntelliCredit
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">
          Platform status
        </h1>
        <p className="text-sm text-muted-foreground">
          Live health and readiness of the IntelliCredit API - the Phase-2
          frontend/backend contract check.
        </p>
      </header>
      <PlatformStatus />
    </main>
  );
}
