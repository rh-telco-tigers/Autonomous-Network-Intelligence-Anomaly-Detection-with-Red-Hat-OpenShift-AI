"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useKnowledgeArticleQuery } from "@/lib/api";
import { titleize } from "@/lib/utils";

export default function KnowledgeArticlePage() {
  const params = useParams<{ incidentId: string; reference: string[] }>();
  const incidentId = params.incidentId;
  const reference = Array.isArray(params.reference) ? params.reference.join("/") : params.reference ?? "";
  const { data, isLoading, error } = useKnowledgeArticleQuery(reference);

  if (isLoading) {
    return <div className="text-sm text-[var(--text-muted)]">Loading knowledge article...</div>;
  }

  if (error || !data?.article) {
    return <div className="text-sm text-[var(--danger-fg)]">Could not load this knowledge article.</div>;
  }

  const article = data.article;
  const articleBody = stripLeadingHeading(article.title, article.content);

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Knowledge article"
        title={article.title}
        description="Category-matched operational guidance retrieved from the Milvus knowledge base for this incident."
        actions={
          <Button asChild variant="secondary">
            <Link href={`/incidents/${encodeURIComponent(incidentId)}`}>Back to incident workflow</Link>
          </Button>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Article metadata</CardTitle>
            <CardDescription>Milvus reference and retrieval context for the selected knowledge article.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <SummaryItem label="Category" value={titleize(article.category ?? "knowledge")} />
            <SummaryItem label="Collection" value={titleize(article.collection)} />
            <SummaryItem label="Document type" value={titleize(article.doc_type.replace(/_/g, " "))} />
            <SummaryItem label="Similarity score" value={article.score > 0 ? article.score.toFixed(2) : "Opened by reference"} />
            <SummaryItem label="Reference" value={article.reference} fullWidth />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Operator usage</CardTitle>
            <CardDescription>These articles are grounded by the incident category so operators can open deeper guidance without leaving the workflow.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm leading-7 text-[var(--text-secondary)]">
            <p>Use this article to compare the current incident to a known operational pattern before approving or executing remediation.</p>
            <p>If the article matches what you see in the timeline and evidence, keep the incident note focused on the exact signals that made the guidance relevant.</p>
            <p>If the article does not match, return to the workflow and review the other knowledge articles for the same incident category.</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Article content</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="whitespace-pre-wrap text-sm leading-7 text-[var(--text-strong)]">{articleBody}</div>
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

function stripLeadingHeading(title: string, content: string) {
  const lines = String(content ?? "").split("\n");
  const firstLine = (lines[0] ?? "").trim();
  if (firstLine.replace(/^#\s*/, "") === title.trim()) {
    return lines.slice(2).join("\n").trim();
  }
  return String(content ?? "").trim();
}
