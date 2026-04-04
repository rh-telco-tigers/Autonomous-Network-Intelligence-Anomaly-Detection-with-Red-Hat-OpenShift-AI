"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useTicketLookupQuery } from "@/lib/api";
import { resolveDirectTicketUrl } from "@/lib/ticket-links";
import { formatTime, titleize } from "@/lib/utils";

export default function TicketDetailPage() {
  const params = useParams<{ provider: string; externalId: string }>();
  const provider = params.provider;
  const externalId = params.externalId;
  const { data, isLoading, error } = useTicketLookupQuery(provider, externalId);

  if (isLoading) {
    return <div className="text-sm text-slate-400">Loading ticket view...</div>;
  }
  if (error || !data) {
    return <div className="text-sm text-rose-300">Could not load this ticket reference.</div>;
  }

  const ticket = data.ticket;
  const incident = data.workflow.incident;
  const externalTicketUrl = resolveDirectTicketUrl(ticket);

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Ticket detail"
        title={ticket.title ?? `${titleize(ticket.provider)} ticket`}
        description="OpenShift-hosted ticket detail for demo-relay mode. When a live provider URL exists, you can jump to it from here."
        actions={<StatusBadge value={ticket.sync_state ?? "synced"} />}
      />

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Ticket summary</CardTitle>
            <CardDescription>Provider state and the linked incident lineage.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <SummaryItem label="Provider" value={ticket.provider.toUpperCase()} />
            <SummaryItem label="External key" value={ticket.external_key ?? ticket.external_id ?? "Not assigned"} />
            <SummaryItem label="External id" value={ticket.external_id ?? "Not assigned"} />
            <SummaryItem label="Sync state" value={ticket.sync_state ?? "unknown"} />
            <SummaryItem label="Last synced" value={formatTime(ticket.last_synced_at)} />
            <SummaryItem label="Incident id" value={incident.id} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Incident context</CardTitle>
            <CardDescription>This keeps the ticket page focused without duplicating the full incident workspace.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge value={incident.status} />
              <StatusBadge value={incident.severity} />
            </div>
            <div className="text-sm text-slate-300">{incident.subtitle ?? incident.narrative ?? "No additional incident summary available."}</div>
            <div className="flex flex-wrap gap-2">
              <Button asChild>
                <Link href={`/incidents/${encodeURIComponent(incident.id)}`}>Open incident workflow</Link>
              </Button>
              {externalTicketUrl ? (
                <Button asChild variant="secondary">
                  <a href={externalTicketUrl} target="_blank" rel="noreferrer">
                    Open live ticket
                  </a>
                </Button>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ListCard
          title="Sync events"
          items={(ticket.sync_events ?? []).map((event) => ({
            title: `${titleize(event.direction)} · ${titleize(event.status)}`,
            description: `${event.event_type} · ${formatTime(event.created_at)}`,
          }))}
          emptyTitle="No sync events yet"
          emptyDescription="Manual syncs, webhook events, and outbound updates will appear here."
        />
        <ListCard
          title="Comments"
          items={(ticket.comments ?? []).map((comment) => ({
            title: `${comment.author ?? "Unknown author"} · ${formatTime(comment.created_at)}`,
            description: comment.body ?? "Empty comment body",
          }))}
          emptyTitle="No comments yet"
          emptyDescription="Ticket comments will appear here once the provider syncs them back."
        />
      </div>
    </div>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
      <div className="text-xs uppercase tracking-[0.2em] text-slate-500">{label}</div>
      <div className="mt-2 text-sm text-slate-100">{value}</div>
    </div>
  );
}

function ListCard({
  title,
  items,
  emptyTitle,
  emptyDescription,
}: {
  title: string;
  items: Array<{ title: string; description: string }>;
  emptyTitle: string;
  emptyDescription: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {items.length ? (
          items.map((item) => (
            <div key={`${item.title}-${item.description}`} className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
              <div className="font-medium text-slate-50">{item.title}</div>
              <div className="mt-1 text-sm text-slate-400">{item.description}</div>
            </div>
          ))
        ) : (
          <EmptyState title={emptyTitle} description={emptyDescription} />
        )}
      </CardContent>
    </Card>
  );
}
