"use client";

import type { RelatedDocument } from "@/lib/types";

type SymptomProfile = {
  primary_signals?: string[];
  supporting_signals?: string[];
  signals_that_do_not_fit?: string[];
};

type DifferentialDiagnosis = {
  condition?: string;
  what_to_check?: string[];
  why_it_matters?: string;
};

type EvidenceSignal = {
  signal?: string;
  expected_if_true?: string;
};

type OperatorAction = {
  step?: number;
  action?: string;
  expected_signal?: string;
};

type ReferenceIncidentPattern = {
  timeline?: string[];
  why_this_pattern_is_realistic?: string;
};

export type StructuredKnowledgeArticle = {
  schema_version?: string;
  slug?: string;
  doc_type?: string;
  title?: string;
  summary?: string;
  category?: string;
  anomaly_types?: string[];
  keywords?: string[];
  service_scope?: string[];
  symptom_profile?: SymptomProfile;
  differential_diagnosis?: DifferentialDiagnosis[];
  evidence_to_collect?: EvidenceSignal[];
  recommended_rca?: {
    root_cause?: string;
    explanation?: string;
    recommendation?: string;
  };
  operator_actions?: OperatorAction[];
  safe_actions?: string[];
  escalation_signals?: string[];
  pitfalls?: string[];
  telemetry_queries?: string[];
  reference_incident_pattern?: ReferenceIncidentPattern;
  guidance?: string[];
};

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

export function parseKnowledgeArticleContent(content: string): StructuredKnowledgeArticle | null {
  const raw = String(content ?? "").trim();
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    return parsed as StructuredKnowledgeArticle;
  } catch {
    return null;
  }
}

export function knowledgeArticleSummary(article: RelatedDocument): string {
  const parsed = parseKnowledgeArticleContent(article.content);
  return String(parsed?.summary || article.summary || article.content || "").trim();
}

export function knowledgeArticlePrimarySignals(article: RelatedDocument): string[] {
  const parsed = parseKnowledgeArticleContent(article.content);
  return normalizeStringList(parsed?.symptom_profile?.primary_signals);
}

export function knowledgeArticleAnomalyTypes(article: RelatedDocument): string[] {
  const parsed = parseKnowledgeArticleContent(article.content);
  return normalizeStringList(parsed?.anomaly_types ?? article.anomaly_types);
}
