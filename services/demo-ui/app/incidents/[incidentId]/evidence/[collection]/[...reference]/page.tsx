"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useDocumentQuery } from "@/lib/api";
import { titleize } from "@/lib/utils";

export default function EvidenceDocumentPage() {
  const params = useParams<{ incidentId: string; collection: string; reference: string[] }>();
  const incidentId = params.incidentId;
  const collection = params.collection;
  const reference = Array.isArray(params.reference) ? params.reference.join("/") : params.reference ?? "";
  const { data, isLoading, error } = useDocumentQuery(collection, reference);

  if (isLoading) {
    return <div className="text-sm text-[var(--text-muted)]">Loading evidence reference...</div>;
  }

  if (error || !data?.document) {
    return <div className="text-sm text-[var(--danger-fg)]">Could not load this evidence reference.</div>;
  }

  const document = data.document;
  const formattedContent = formatDocumentContent(document.content);

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Evidence reference"
        title={document.title}
        description="Full retrieved document content referenced by the RCA workflow."
        actions={
          <Button asChild variant="secondary">
            <Link href={`/incidents/${encodeURIComponent(incidentId)}`}>Back to incident workflow</Link>
          </Button>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Document metadata</CardTitle>
            <CardDescription>Stored retrieval metadata for the selected evidence reference.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <SummaryItem label="Collection" value={titleize(document.collection)} />
            <SummaryItem label="Document type" value={titleize(document.doc_type.replace(/_/g, " "))} />
            <SummaryItem label="Stage" value={titleize(document.stage ?? "unknown")} />
            <SummaryItem label="Category" value={titleize(document.category ?? "n/a")} />
            <SummaryItem label="Similarity score" value={document.score > 0 ? document.score.toFixed(2) : "Opened by reference"} />
            <SummaryItem label="Reference" value={document.reference} fullWidth />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>How to use it</CardTitle>
            <CardDescription>Read the exact supporting document before approving or rejecting the RCA.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm leading-7 text-[var(--text-secondary)]">
            <p>Use this reader to inspect the source material the RCA cited instead of relying only on the short evidence label in the workflow.</p>
            <p>If the evidence does not support the AI summary, return to the incident workflow and regenerate or challenge the RCA before taking action.</p>
            <p>When the content does match the incident, use the same terminology from this document in operator notes and ticket updates.</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Document content</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm leading-7 text-[var(--text-strong)]">
            {formattedContent}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryItem({ label, value, fullWidth = false }: { label: string; value: string; fullWidth?: boolean }) {
  return (
    <div
      className={[
        "rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4",
        fullWidth ? "md:col-span-2" : "",
      ].join(" ")}
    >
      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-subtle)]">{label}</div>
      <div className="mt-2 break-words text-sm text-[var(--text-strong)]">{value}</div>
    </div>
  );
}

function formatDocumentContent(value: string) {
  const content = String(value ?? "").trim();
  if (!content) {
    return "No content available.";
  }
  try {
    const parsed = JSON.parse(content);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return content;
  }
}
