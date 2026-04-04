"use client";

import Link from "next/link";
import { useMemo } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { useIncidentsQuery } from "@/lib/api";
import type { IncidentRecord } from "@/lib/types";
import { formatRelativeNumber, formatTime, titleize } from "@/lib/utils";

type Filters = {
  status?: string;
  severity?: string;
  q?: string;
};

export function IncidentsPageClient({ initialFilters }: { initialFilters: Filters }) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const filters = {
    status: searchParams.get("status") ?? initialFilters.status ?? "",
    severity: searchParams.get("severity") ?? initialFilters.severity ?? "",
    q: searchParams.get("q") ?? initialFilters.q ?? "",
  };

  const { data, isLoading, error } = useIncidentsQuery(filters);

  const columns = useMemo<ColumnDef<IncidentRecord>[]>(
    () => [
      {
        accessorKey: "anomaly_type",
        header: "Incident",
        cell: ({ row }) => (
          <div>
            <div className="font-medium text-[var(--text-strong)]">{titleize(row.original.anomaly_type)}</div>
            <div className="text-xs text-[var(--text-subtle)]">{row.original.id}</div>
          </div>
        ),
      },
      {
        accessorKey: "severity",
        header: "Severity",
        cell: ({ row }) => <StatusBadge value={row.original.severity} />,
      },
      {
        accessorKey: "status",
        header: "Workflow state",
        cell: ({ row }) => <StatusBadge value={row.original.status} />,
      },
      {
        accessorKey: "anomaly_score",
        header: "Score",
        cell: ({ row }) => formatRelativeNumber(row.original.anomaly_score),
      },
      {
        accessorKey: "updated_at",
        header: "Updated",
        cell: ({ row }) => formatTime(row.original.updated_at),
      },
      {
        id: "open",
        header: "",
        cell: ({ row }) => (
          <Link href={`/incidents/${row.original.id}`} className="text-sm font-medium text-[var(--accent)]">
            Open
          </Link>
        ),
      },
    ],
    [],
  );

  const table = useReactTable({
    data: data ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams.toString());
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    const queryString = next.toString();
    router.replace(queryString ? `${pathname}?${queryString}` : pathname);
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Incident operations"
        title="Incidents"
        description="This page is the queue and entry point. Open an incident to review RCA, change workflow state, execute actions, sync tickets, and verify resolution."
      />

      <Card>
        <CardHeader>
          <CardTitle>Queue filters</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <Input
            placeholder="Search by id, anomaly, summary..."
            value={filters.q}
            onChange={(event) => updateParam("q", event.target.value)}
          />
          <Select value={filters.status} onChange={(event) => updateParam("status", event.target.value)}>
            <option value="">All workflow states</option>
            <option value="NEW">NEW</option>
            <option value="RCA_GENERATED">RCA_GENERATED</option>
            <option value="REMEDIATION_SUGGESTED">REMEDIATION_SUGGESTED</option>
            <option value="AWAITING_APPROVAL">AWAITING_APPROVAL</option>
            <option value="APPROVED">APPROVED</option>
            <option value="EXECUTING">EXECUTING</option>
            <option value="EXECUTED">EXECUTED</option>
            <option value="VERIFIED">VERIFIED</option>
            <option value="CLOSED">CLOSED</option>
            <option value="EXECUTION_FAILED">EXECUTION_FAILED</option>
            <option value="VERIFICATION_FAILED">VERIFICATION_FAILED</option>
            <option value="FALSE_POSITIVE">FALSE_POSITIVE</option>
            <option value="ESCALATED">ESCALATED</option>
          </Select>
          <Select value={filters.severity} onChange={(event) => updateParam("severity", event.target.value)}>
            <option value="">All severities</option>
            <option value="Critical">Critical</option>
            <option value="Warning">Warning</option>
            <option value="Medium">Medium</option>
          </Select>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Incident queue</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="text-sm text-[var(--text-muted)]">Loading incidents...</div>
          ) : error ? (
            <div className="text-sm text-[var(--danger-fg)]">Could not load incidents.</div>
          ) : !data?.length ? (
            <EmptyState title="No incidents match these filters" description="Adjust the queue filters or wait for new incidents." />
          ) : (
            <div className="overflow-hidden rounded-2xl border border-[var(--border-subtle)]">
              <table className="w-full text-left text-sm">
                <thead className="bg-[var(--surface-raised)] text-[var(--text-muted)]">
                  {table.getHeaderGroups().map((headerGroup) => (
                    <tr key={headerGroup.id}>
                      {headerGroup.headers.map((header) => (
                        <th key={header.id} className="px-4 py-3 font-medium">
                          {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => (
                    <tr
                      key={row.id}
                      className="border-t border-[var(--border-subtle)] bg-[var(--surface-subtle)] hover:bg-[var(--surface-hover)]"
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-4 py-3 align-top">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
