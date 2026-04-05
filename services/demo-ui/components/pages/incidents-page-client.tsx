"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";

import { EmptyState } from "@/components/empty-state";
import { PaginationControls } from "@/components/pagination-controls";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { TransientDataWarning } from "@/components/transient-data-warning";
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
  const [searchValue, setSearchValue] = useState(filters.q);
  const page = parsePositiveInt(searchParams.get("page"), 1);
  const pageSize = normalizePageSize(searchParams.get("pageSize"), 10);

  const { data, isLoading, error } = useIncidentsQuery(filters);
  const totalItems = data?.length ?? 0;
  const showRefreshWarning = Boolean(error) && Boolean(data);
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(page, totalPages);
  const paginatedData = useMemo(() => {
    const rows = data ?? [];
    const start = (safePage - 1) * pageSize;
    return rows.slice(start, start + pageSize);
  }, [data, pageSize, safePage]);

  useEffect(() => {
    setSearchValue(filters.q);
  }, [filters.q]);

  useEffect(() => {
    if (searchValue === filters.q) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      updateFilterParam("q", searchValue);
    }, 350);
    return () => window.clearTimeout(timeoutId);
  }, [filters.q, searchValue]);

  const columns = useMemo<ColumnDef<IncidentRecord>[]>(
    () => [
      {
        accessorKey: "anomaly_type",
        header: "Incident",
        cell: ({ row }) => (
          <div>
            <div className="font-medium text-[var(--text-strong)]">{titleize(row.original.anomaly_type)}</div>
            <div className="text-xs text-[var(--text-subtle)]">{row.original.id}</div>
            {row.original.current_ticket_summary ? (
              <div className="mt-1 text-xs text-[var(--text-subtle)]">
                {row.original.current_ticket_summary.provider.toUpperCase()} ·{" "}
                {row.original.current_ticket_summary.external_key ||
                  row.original.current_ticket_summary.external_id ||
                  row.original.current_ticket_summary.title ||
                  "Linked ticket"}
              </div>
            ) : null}
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
    data: paginatedData,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  function replaceQuery(next: URLSearchParams) {
    const queryString = next.toString();
    router.replace(queryString ? `${pathname}?${queryString}` : pathname);
  }

  function updateFilterParam(key: string, value: string) {
    const next = new URLSearchParams(searchParams.toString());
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.delete("page");
    replaceQuery(next);
  }

  function updatePage(nextPage: number) {
    const next = new URLSearchParams(searchParams.toString());
    if (nextPage <= 1) {
      next.delete("page");
    } else {
      next.set("page", String(nextPage));
    }
    replaceQuery(next);
  }

  function updatePageSize(nextPageSize: number) {
    const next = new URLSearchParams(searchParams.toString());
    next.set("pageSize", String(nextPageSize));
    next.delete("page");
    replaceQuery(next);
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
            placeholder="Search incidents, tickets, comments..."
            value={searchValue}
            onChange={(event) => setSearchValue(event.target.value)}
          />
          <Select value={filters.status} onChange={(event) => updateFilterParam("status", event.target.value)}>
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
          <Select value={filters.severity} onChange={(event) => updateFilterParam("severity", event.target.value)}>
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
          {isLoading && !data ? (
            <div className="text-sm text-[var(--text-muted)]">Loading incidents...</div>
          ) : !data ? (
            <div className="text-sm text-[var(--danger-fg)]">Could not load incidents.</div>
          ) : !totalItems ? (
            <EmptyState title="No incidents match these filters" description="Adjust the queue filters or wait for new incidents." />
          ) : (
            <div className="space-y-4">
              {showRefreshWarning ? (
                <TransientDataWarning>
                  Showing the last successful incident queue while the background refresh is retried.
                </TransientDataWarning>
              ) : null}
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
              <PaginationControls
                page={safePage}
                pageSize={pageSize}
                totalItems={totalItems}
                itemLabel="incidents"
                pageSizeOptions={[5, 10, 20, 50]}
                onPageChange={updatePage}
                onPageSizeChange={updatePageSize}
              />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function parsePositiveInt(value: string | null, fallback: number) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizePageSize(value: string | null, fallback: number) {
  const allowed = new Set([5, 10, 20, 50]);
  const parsed = parsePositiveInt(value, fallback);
  return allowed.has(parsed) ? parsed : fallback;
}
