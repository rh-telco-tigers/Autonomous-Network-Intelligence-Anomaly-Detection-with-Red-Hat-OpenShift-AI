"use client";

import type { RelatedDocument } from "@/lib/types";
import { parseKnowledgeArticleContent } from "@/lib/knowledge";
import { titleize } from "@/lib/utils";

export function KnowledgeArticleView({
  article,
  compact = false,
}: {
  article: RelatedDocument;
  compact?: boolean;
}) {
  const parsed = parseKnowledgeArticleContent(article.content);
  if (!parsed) {
    return <div className="whitespace-pre-wrap text-sm leading-7 text-[var(--text-strong)]">{article.content}</div>;
  }

  const anomalyTypes = Array.isArray(parsed.anomaly_types) ? parsed.anomaly_types : [];
  const keywords = Array.isArray(parsed.keywords) ? parsed.keywords : [];
  const serviceScope = Array.isArray(parsed.service_scope) ? parsed.service_scope : [];
  const primarySignals = Array.isArray(parsed.symptom_profile?.primary_signals) ? parsed.symptom_profile?.primary_signals : [];
  const supportingSignals = Array.isArray(parsed.symptom_profile?.supporting_signals)
    ? parsed.symptom_profile?.supporting_signals
    : [];
  const signalsThatDoNotFit = Array.isArray(parsed.symptom_profile?.signals_that_do_not_fit)
    ? parsed.symptom_profile?.signals_that_do_not_fit
    : [];
  const differentialDiagnosis = Array.isArray(parsed.differential_diagnosis) ? parsed.differential_diagnosis : [];
  const evidenceToCollect = Array.isArray(parsed.evidence_to_collect) ? parsed.evidence_to_collect : [];
  const operatorActions = Array.isArray(parsed.operator_actions) ? parsed.operator_actions : [];
  const safeActions = Array.isArray(parsed.safe_actions) ? parsed.safe_actions : [];
  const escalationSignals = Array.isArray(parsed.escalation_signals) ? parsed.escalation_signals : [];
  const pitfalls = Array.isArray(parsed.pitfalls) ? parsed.pitfalls : [];
  const telemetryQueries = Array.isArray(parsed.telemetry_queries) ? parsed.telemetry_queries : [];
  const referenceTimeline = Array.isArray(parsed.reference_incident_pattern?.timeline)
    ? parsed.reference_incident_pattern?.timeline
    : [];
  const guidance = Array.isArray(parsed.guidance) ? parsed.guidance : [];

  return (
    <div className="space-y-5">
      {parsed.summary ? (
        <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm leading-7 text-[var(--text-secondary)]">
          {parsed.summary}
        </div>
      ) : null}

      <TagStrip
        items={[
          parsed.category ? `Category: ${titleize(parsed.category)}` : "",
          ...anomalyTypes.map((value) => value.replace(/_/g, " ")),
          ...serviceScope.slice(0, compact ? 3 : 6).map((value) => `Scope: ${value}`),
        ]}
      />

      {article.match_reasons?.length ? (
        <SimpleBulletSection title="Why this matched" items={article.match_reasons} />
      ) : null}

      {parsed.recommended_rca?.root_cause || parsed.recommended_rca?.recommendation ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {parsed.recommended_rca?.root_cause ? (
            <TextCard title="Root cause guidance" body={parsed.recommended_rca.root_cause} />
          ) : null}
          {parsed.recommended_rca?.recommendation ? (
            <TextCard title="Recommended response" body={parsed.recommended_rca.recommendation} />
          ) : null}
        </div>
      ) : null}

      {primarySignals.length ? <SimpleBulletSection title="Primary signals" items={primarySignals} /> : null}
      {operatorActions.length ? <ActionSection title="Operator actions" items={operatorActions} compact={compact} /> : null}
      {safeActions.length ? <SimpleBulletSection title="Safe actions" items={safeActions} /> : null}

      {!compact && parsed.recommended_rca?.explanation ? (
        <TextCard title="Why this RCA fits" body={parsed.recommended_rca.explanation} />
      ) : null}
      {!compact && supportingSignals.length ? <SimpleBulletSection title="Supporting signals" items={supportingSignals} /> : null}
      {!compact && signalsThatDoNotFit.length ? (
        <SimpleBulletSection title="Signals that do not fit" items={signalsThatDoNotFit} />
      ) : null}
      {!compact && differentialDiagnosis.length ? (
        <DiagnosisSection title="Differential diagnosis" items={differentialDiagnosis} />
      ) : null}
      {!compact && evidenceToCollect.length ? <EvidenceSection title="Evidence to collect" items={evidenceToCollect} /> : null}
      {!compact && escalationSignals.length ? (
        <SimpleBulletSection title="Escalation signals" items={escalationSignals} />
      ) : null}
      {!compact && telemetryQueries.length ? (
        <SimpleBulletSection title="Telemetry queries" items={telemetryQueries} monospace />
      ) : null}
      {!compact && referenceTimeline.length ? (
        <SimpleBulletSection title="Reference incident timeline" items={referenceTimeline} />
      ) : null}
      {!compact && parsed.reference_incident_pattern?.why_this_pattern_is_realistic ? (
        <TextCard title="Why this pattern is realistic" body={parsed.reference_incident_pattern.why_this_pattern_is_realistic} />
      ) : null}
      {!compact && pitfalls.length ? <SimpleBulletSection title="Pitfalls" items={pitfalls} /> : null}
      {!compact && keywords.length ? <TagStrip items={keywords.map((value) => `Keyword: ${value}`)} /> : null}
      {!compact && guidance.length ? <SimpleBulletSection title="Operational guidance" items={guidance} /> : null}
    </div>
  );
}

function TagStrip({ items }: { items: string[] }) {
  const filtered = items.map((item) => String(item || "").trim()).filter(Boolean);
  if (!filtered.length) {
    return null;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {filtered.map((item) => (
        <div
          key={item}
          className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-subtle)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]"
        >
          {item}
        </div>
      ))}
    </div>
  );
}

function TextCard({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{title}</div>
      <div className="mt-2 text-sm leading-7 text-[var(--text-strong)]">{body}</div>
    </div>
  );
}

function SimpleBulletSection({
  title,
  items,
  monospace = false,
}: {
  title: string;
  items: string[];
  monospace?: boolean;
}) {
  const filtered = items.map((item) => String(item || "").trim()).filter(Boolean);
  if (!filtered.length) {
    return null;
  }
  return (
    <div className="space-y-3">
      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{title}</div>
      <div className="space-y-2">
        {filtered.map((item) => (
          <div key={item} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
            <div className={monospace ? "font-mono text-sm text-[var(--text-strong)]" : "text-sm leading-7 text-[var(--text-strong)]"}>
              {item}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DiagnosisSection({
  title,
  items,
}: {
  title: string;
  items: Array<{ condition?: string; what_to_check?: string[]; why_it_matters?: string }>;
}) {
  const filtered = items.filter((item) => item.condition || item.why_it_matters || item.what_to_check?.length);
  if (!filtered.length) {
    return null;
  }
  return (
    <div className="space-y-3">
      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{title}</div>
      <div className="grid gap-3 lg:grid-cols-2">
        {filtered.map((item, index) => (
          <div key={`${item.condition ?? "diagnosis"}-${index}`} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
            {item.condition ? <div className="text-sm font-semibold text-[var(--text-strong)]">{item.condition}</div> : null}
            {item.what_to_check?.length ? (
              <div className="mt-2 space-y-2">
                {item.what_to_check.map((check) => (
                  <div key={check} className="text-sm leading-7 text-[var(--text-secondary)]">
                    {check}
                  </div>
                ))}
              </div>
            ) : null}
            {item.why_it_matters ? <div className="mt-3 text-sm leading-7 text-[var(--text-strong)]">{item.why_it_matters}</div> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function EvidenceSection({
  title,
  items,
}: {
  title: string;
  items: Array<{ signal?: string; expected_if_true?: string }>;
}) {
  const filtered = items.filter((item) => item.signal || item.expected_if_true);
  if (!filtered.length) {
    return null;
  }
  return (
    <div className="space-y-3">
      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{title}</div>
      <div className="space-y-3">
        {filtered.map((item, index) => (
          <div key={`${item.signal ?? "evidence"}-${index}`} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
            {item.signal ? <div className="text-sm font-semibold text-[var(--text-strong)]">{item.signal}</div> : null}
            {item.expected_if_true ? <div className="mt-2 text-sm leading-7 text-[var(--text-secondary)]">{item.expected_if_true}</div> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function ActionSection({
  title,
  items,
  compact,
}: {
  title: string;
  items: Array<{ step?: number; action?: string; expected_signal?: string }>;
  compact: boolean;
}) {
  const filtered = items.filter((item) => item.action || item.expected_signal).slice(0, compact ? 3 : items.length);
  if (!filtered.length) {
    return null;
  }
  return (
    <div className="space-y-3">
      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{title}</div>
      <div className="space-y-3">
        {filtered.map((item, index) => (
          <div key={`${item.action ?? "action"}-${index}`} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
            <div className="text-sm font-semibold text-[var(--text-strong)]">
              {item.step ? `Step ${item.step}` : `Step ${index + 1}`}
              {item.action ? ` · ${item.action}` : ""}
            </div>
            {item.expected_signal ? <div className="mt-2 text-sm leading-7 text-[var(--text-secondary)]">{item.expected_signal}</div> : null}
          </div>
        ))}
      </div>
    </div>
  );
}
