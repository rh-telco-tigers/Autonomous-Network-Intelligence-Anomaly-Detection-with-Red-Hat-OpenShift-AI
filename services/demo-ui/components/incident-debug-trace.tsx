"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useIncidentDebugTraceQuery } from "@/lib/api";
import type { DebugTracePacket } from "@/lib/types";
import { cn, formatTime, titleize } from "@/lib/utils";

const CATEGORY_BADGE_CLASSES: Record<string, string> = {
  api: "bg-sky-500/10 text-sky-200 ring-1 ring-sky-400/20",
  model: "bg-violet-500/10 text-violet-200 ring-1 ring-violet-400/20",
  llm: "bg-fuchsia-500/10 text-fuchsia-200 ring-1 ring-fuchsia-400/20",
  workflow: "bg-emerald-500/10 text-emerald-200 ring-1 ring-emerald-400/20",
  action: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-400/20",
  ticket: "bg-slate-500/10 text-slate-200 ring-1 ring-slate-400/20",
};

export function IncidentDebugTrace() {
  const params = useParams<{ incidentId: string }>();
  const incidentId = params.incidentId;
  const { data, isLoading, error } = useIncidentDebugTraceQuery(incidentId);

  if (isLoading) {
    return <div className="text-sm text-[var(--text-muted)]">Loading detailed execution trace...</div>;
  }

  if (error || !data) {
    return <div className="text-sm text-[var(--danger-fg)]">Could not load this incident debug trace.</div>;
  }

  const incident = data.incident;
  const tracePackets = data.trace_packets ?? [];

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Incident deep dive"
        title={`${titleize(incident.anomaly_type)} detailed execution trace`}
        description="Raw, timestamped packets across API calls, model inference, RCA generation, workflow updates, and ticket activity."
        actions={
          <Button asChild variant="secondary">
            <Link href={`/incidents/${encodeURIComponent(incidentId)}`}>Back to incident workflow</Link>
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SummaryItem label="Incident ID" value={incident.id} />
        <SummaryItem label="Workflow state" value={<StatusBadge value={incident.status} />} />
        <SummaryItem label="Model version" value={incident.model_version} />
        <SummaryItem label="Trace packets" value={String(tracePackets.length)} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Ordered execution trace</CardTitle>
          <CardDescription>Every available packet is shown in timestamp order without summarization.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {tracePackets.length ? (
            tracePackets.map((packet) => <TracePacketCard key={`${packet.sequence}-${packet.timestamp}-${packet.title}`} packet={packet} />)
          ) : (
            <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm text-[var(--text-secondary)]">
              No detailed trace packets were captured for this incident yet.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function TracePacketCard({ packet }: { packet: DebugTracePacket }) {
  const categoryLabel = titleize(packet.category.replace(/_/g, " "));
  const phaseLabel = titleize(packet.phase.replace(/_/g, " "));
  const endpointLabel = [packet.method, packet.endpoint].filter(Boolean).join(" ").trim();

  return (
    <div className="rounded-3xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-[var(--surface-raised)] px-3 py-1 text-xs font-semibold tracking-[0.2em] text-[var(--text-muted)]">
              #{packet.sequence}
            </span>
            <span
              className={cn(
                "rounded-full px-3 py-1 text-xs font-semibold tracking-[0.2em]",
                CATEGORY_BADGE_CLASSES[packet.category] ?? "bg-[var(--surface-raised)] text-[var(--text-secondary)] ring-1 ring-[var(--border-subtle)]",
              )}
            >
              {categoryLabel}
            </span>
            <span className="rounded-full bg-[var(--surface-raised)] px-3 py-1 text-xs font-semibold tracking-[0.2em] text-[var(--text-secondary)]">
              {phaseLabel}
            </span>
          </div>
          <div>
            <div className="text-lg font-semibold text-[var(--text-strong)]">{packet.title}</div>
            <div className="mt-1 text-sm text-[var(--text-secondary)]">
              {endpointLabel || "Raw event packet"}
              {packet.service ? ` · service ${packet.service}` : ""}
              {packet.target ? ` · target ${packet.target}` : ""}
            </div>
          </div>
        </div>
        <div className="text-sm text-[var(--text-muted)]">{formatTime(packet.timestamp)}</div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <RawJsonBlock label="Payload" value={packet.payload} />
        <RawJsonBlock label="Metadata" value={packet.metadata} />
      </div>
    </div>
  );
}

function RawJsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{label}</div>
      <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all text-xs leading-6 text-[var(--text-strong)]">
        {formatRawJson(value)}
      </pre>
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-subtle)]">{label}</div>
      <div className="mt-2 break-words text-sm text-[var(--text-strong)]">{value}</div>
    </div>
  );
}

function formatRawJson(value: unknown) {
  if (typeof value === "string") {
    return value;
  }
  if (value === undefined) {
    return "undefined";
  }
  try {
    return JSON.stringify(value ?? null, null, 2);
  } catch {
    return String(value);
  }
}
