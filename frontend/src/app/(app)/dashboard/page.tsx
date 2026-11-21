"use client";

import { Activity, CheckCircle2, Clock, FolderKanban } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { DecisionBadge, GradeBadge } from "@/components/shared/risk";
import { PageHeader, SectionTitle, StatCard } from "@/components/shared/stat";
import { CardSkeleton, EmptyState, ErrorState } from "@/components/shared/states";
import { StackBar } from "@/components/shared/viz";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtNum, fmtRelative, gradeTone, toneStyle } from "@/lib/format";
import { useDashboard } from "@/lib/hooks";

export default function DashboardPage() {
  const { data, isLoading, error } = useDashboard();

  const decisionSegments = React.useMemo(() => {
    const d = data?.decision_distribution ?? {};
    return [
      { label: "Approve", count: d.approve ?? 0, tone: "success" as const },
      { label: "Refer", count: d.refer ?? 0, tone: "warning" as const },
      { label: "Decline", count: d.decline ?? 0, tone: "destructive" as const },
    ];
  }, [data]);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 p-4 md:p-6">
      <PageHeader
        title="Portfolio"
        description="Live snapshot of the credit book — cases, reviews, and decisions."
      />

      {error ? <ErrorState error={error} /> : null}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Active cases"
          value={isLoading ? "—" : data?.active_cases ?? 0}
          icon={FolderKanban}
          hint="Open loan applications"
        />
        <StatCard
          label="Awaiting review"
          value={isLoading ? "—" : data?.awaiting_review ?? 0}
          tone={(data?.awaiting_review ?? 0) > 0 ? "warning" : "default"}
          icon={Clock}
          hint="Paused for Credit Officer"
        />
        <StatCard
          label="Decisions"
          value={isLoading ? "—" : data?.completed_runs ?? 0}
          tone="success"
          icon={CheckCircle2}
          hint="Completed appraisals"
        />
        <StatCard
          label="In progress"
          value={isLoading ? "—" : (data?.running ?? 0) + (data?.failed ?? 0)}
          icon={Activity}
          hint={`${data?.running ?? 0} running · ${data?.failed ?? 0} failed`}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="p-4 lg:col-span-2">
          <SectionTitle
            title="Recent decisions"
            description="Latest deterministic credit decisions across the book."
          />
          {isLoading ? (
            <CardSkeleton rows={4} />
          ) : !data?.recent_decisions.length ? (
            <EmptyState
              title="No decisions yet"
              description="Run the credit-appraisal workflow on a case to produce a decision."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Company</TableHead>
                  <TableHead>Grade</TableHead>
                  <TableHead>Decision</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                  <TableHead className="text-right">When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.recent_decisions.map((d) => (
                  <TableRow key={d.run_id}>
                    <TableCell>
                      <Link
                        href={`/cases/${d.case_id}`}
                        className="font-medium hover:underline"
                      >
                        {d.company_legal_name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <GradeBadge grade={d.grade} />
                    </TableCell>
                    <TableCell>
                      <DecisionBadge decision={d.decision} />
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {fmtNum(d.final_score, 1)}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {fmtRelative(d.finished_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>

        <Card className="p-4">
          <SectionTitle title="Distribution" description="Grades and decisions." />
          <div className="flex flex-col gap-5">
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">Decisions</p>
              <StackBar segments={decisionSegments} />
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
                {decisionSegments.map((s) => (
                  <span key={s.label} className="flex items-center gap-1.5">
                    <span className={`size-2 rounded-full ${toneStyle(s.tone).bg}`} />
                    {s.label} · <span className="font-mono">{s.count}</span>
                  </span>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">Risk grades</p>
              {data?.grade_distribution.length ? (
                <div className="flex flex-col gap-1.5">
                  {data.grade_distribution.map((g) => (
                    <div key={g.grade} className="flex items-center gap-2 text-xs">
                      <span className="w-10 font-mono">{g.grade}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                        <div
                          className={`h-full ${toneStyle(gradeTone(g.grade)).bg}`}
                          style={{
                            width: `${
                              (g.count /
                                Math.max(...data.grade_distribution.map((x) => x.count))) *
                              100
                            }%`,
                          }}
                        />
                      </div>
                      <span className="w-6 text-right font-mono tabular-nums">{g.count}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">No graded assessments yet.</p>
              )}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
