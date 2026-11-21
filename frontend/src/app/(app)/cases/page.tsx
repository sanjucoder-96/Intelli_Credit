"use client";

import { FolderKanban } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { CreateCaseDialog } from "@/components/create-case-dialog";
import { PageHeader } from "@/components/shared/stat";
import { CardSkeleton, EmptyState, ErrorState } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAuth, canWrite } from "@/lib/auth";
import { fmtDate, fmtInr, titleCase } from "@/lib/format";
import { useCases, useCompanyMap } from "@/lib/hooks";

export default function CasesPage() {
  const role = useAuth((s) => s.user?.role);
  const cases = useCases();
  const { map } = useCompanyMap();

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 p-4 md:p-6">
      <PageHeader
        title="Cases"
        description="Corporate credit appraisals across the book."
        actions={canWrite(role) ? <CreateCaseDialog /> : null}
      />

      {cases.error ? <ErrorState error={cases.error} /> : null}

      <Card className="p-1">
        {cases.isLoading ? (
          <div className="p-4">
            <CardSkeleton rows={5} />
          </div>
        ) : !cases.data?.length ? (
          <EmptyState
            icon={FolderKanban}
            title="No cases yet"
            description="Create a case to begin a corporate credit appraisal."
            action={canWrite(role) ? <CreateCaseDialog /> : undefined}
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Case</TableHead>
                <TableHead>Company</TableHead>
                <TableHead className="text-right">Requested</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Opened</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cases.data.map((c) => (
                <TableRow key={c.id} className="cursor-pointer">
                  <TableCell>
                    <Link href={`/cases/${c.id}`} className="font-medium hover:underline">
                      {c.title}
                    </Link>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {map.get(c.company_id)?.legal_name ?? "—"}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {fmtInr(c.requested_amount)}
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{titleCase(c.status)}</Badge>
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground">
                    {fmtDate(c.created_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
