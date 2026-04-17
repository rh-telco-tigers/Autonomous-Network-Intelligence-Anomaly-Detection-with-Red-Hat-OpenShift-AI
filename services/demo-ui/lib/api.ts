"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiToken } from "@/components/providers/app-providers";
import type {
  ConsoleState,
  DocumentResponse,
  GuardrailsDemoExample,
  GuardrailsDemoResponse,
  IncidentDebugTraceResponse,
  IncidentRecord,
  IncidentWorkflow,
  KnowledgeArticleResponse,
  RelatedRecords,
  SafetyControlsStatus,
  SafetyProbeResponse,
  ScenarioRunResponse,
  TicketLookupResponse,
} from "@/lib/types";

const defaultProject = process.env.NEXT_PUBLIC_IMS_PROJECT ?? "ani-demo";
const DEFAULT_REQUEST_TIMEOUT_MS = 12_000;
export const LONG_RUNNING_REQUEST_TIMEOUT_MS = 45_000;
const CONSOLE_STALE_TIME_MS = 30_000;
const INCIDENT_LIST_STALE_TIME_MS = 20_000;
const INCIDENT_WORKFLOW_STALE_TIME_MS = 15_000;
const DEBUG_TRACE_STALE_TIME_MS = 60_000;
const RELATED_RECORDS_STALE_TIME_MS = 45_000;

type PlaybookInstructionPreviewResponse = {
  instruction: string;
  correlation_id: string;
  draft: boolean;
};

export type RequestOptions = RequestInit & {
  timeoutMs?: number;
};

export async function request<T>(path: string, token: string, init?: RequestOptions): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = init?.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      ...init,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "x-api-key": token,
        ...(init?.headers ?? {}),
      },
    });
    const raw = await response.text();
    let payload: unknown = null;
    if (raw) {
      try {
        payload = JSON.parse(raw) as unknown;
      } catch {
        payload = raw;
      }
    }
    if (!response.ok) {
      throw new Error(
        typeof payload === "string"
          ? payload || `Request failed with status ${response.status}`
          : JSON.stringify(payload),
      );
    }
    return payload as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

export function useConsoleStateQuery(refetchInterval = 15_000) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["console-state", defaultProject, token],
    queryFn: () => request<ConsoleState>(`/api/console/state?project=${encodeURIComponent(defaultProject)}`, token),
    placeholderData: (previousData) => previousData,
    refetchInterval,
    refetchIntervalInBackground: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: Math.max(CONSOLE_STALE_TIME_MS, Math.floor(refetchInterval * 0.75)),
    retry: 2,
    retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
  });
}

export function useSafetyControlsStatusQuery(refetchInterval = 30_000) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["safety-controls-status", defaultProject, token],
    queryFn: () =>
      request<SafetyControlsStatus>(`/api/safety-controls/status?project=${encodeURIComponent(defaultProject)}`, token),
    placeholderData: (previousData) => previousData,
    refetchInterval,
    refetchIntervalInBackground: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: 20_000,
    retry: 2,
    retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
  });
}

export function useIncidentsQuery(filters: { status?: string; severity?: string; q?: string }) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["incidents", defaultProject, filters, token],
    queryFn: () => {
      const params = new URLSearchParams({ project: defaultProject });
      if (filters.status) {
        params.set("status", filters.status);
      }
      if (filters.severity) {
        params.set("severity", filters.severity);
      }
      if (filters.q?.trim()) {
        params.set("q", filters.q.trim());
      }
      return request<IncidentRecord[]>(`/api/incidents?${params.toString()}`, token);
    },
    placeholderData: (previousData) => previousData,
    refetchInterval: 45_000,
    refetchIntervalInBackground: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: INCIDENT_LIST_STALE_TIME_MS,
    retry: 2,
    retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
  });
}

export function useIncidentWorkflowQuery(incidentId: string) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["incident-workflow", incidentId, token],
    queryFn: () => request<IncidentWorkflow>(`/api/incidents/${encodeURIComponent(incidentId)}`, token),
    enabled: Boolean(incidentId),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: INCIDENT_WORKFLOW_STALE_TIME_MS,
    retry: 2,
  });
}

export function useIncidentDebugTraceQuery(incidentId: string) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["incident-debug-trace", incidentId, token],
    queryFn: () => request<IncidentDebugTraceResponse>(`/api/incidents/${encodeURIComponent(incidentId)}/debug-trace`, token),
    enabled: Boolean(incidentId),
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: DEBUG_TRACE_STALE_TIME_MS,
    retry: 2,
  });
}

export function useTicketLookupQuery(provider: string, externalId: string) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["ticket-lookup", provider, externalId, token],
    queryFn: () => request<TicketLookupResponse>(`/api/tickets/${encodeURIComponent(provider)}/${encodeURIComponent(externalId)}`, token),
    enabled: Boolean(provider) && Boolean(externalId),
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: DEBUG_TRACE_STALE_TIME_MS,
    retry: 2,
  });
}

export function useKnowledgeArticleQuery(reference: string) {
  const { token } = useApiToken();
  const normalizedReference = reference
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return useQuery({
    queryKey: ["knowledge-article", reference, token],
    queryFn: () => request<KnowledgeArticleResponse>(`/api/knowledge/articles/${normalizedReference}`, token),
    enabled: Boolean(reference),
  });
}

export function useRelatedRecordsQuery(incidentId: string, options?: { limit?: number; knowledgeLimit?: number }) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["related-records", incidentId, options?.limit ?? 6, options?.knowledgeLimit ?? 4, token],
    queryFn: () =>
      request<RelatedRecords>(`/api/incidents/${encodeURIComponent(incidentId)}/related`, token, {
        method: "POST",
        body: JSON.stringify({
          limit: options?.limit ?? 6,
          knowledge_limit: options?.knowledgeLimit ?? 4,
        }),
      }),
    enabled: Boolean(incidentId),
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: RELATED_RECORDS_STALE_TIME_MS,
    retry: 2,
  });
}

export function usePlaybookInstructionPreviewQuery(
  incidentId: string,
  remediationId: number | null | undefined,
  options: {
    requestedBy: string;
    notes?: string;
    sourceUrl?: string;
    enabled?: boolean;
  },
) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: [
      "playbook-instruction-preview",
      incidentId,
      remediationId ?? 0,
      options.requestedBy,
      options.notes ?? "",
      options.sourceUrl ?? "",
      token,
    ],
    queryFn: () =>
      request<PlaybookInstructionPreviewResponse>(
        `/api/incidents/${encodeURIComponent(incidentId)}/remediation/${remediationId}/playbook-instruction-preview`,
        token,
        {
          method: "POST",
          body: JSON.stringify({
            requested_by: options.requestedBy,
            notes: options.notes ?? "",
            source_url: options.sourceUrl ?? "",
          }),
        },
      ),
    enabled: Boolean(incidentId) && Boolean(remediationId) && Boolean(options.enabled ?? true),
    placeholderData: (previousData) => previousData,
    refetchInterval: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    staleTime: 5_000,
    retry: 1,
  });
}

export function useDocumentQuery(collection: string, reference: string) {
  const { token } = useApiToken();
  const normalizedReference = reference
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return useQuery({
    queryKey: ["document", collection, reference, token],
    queryFn: () => request<DocumentResponse>(`/api/documents/${encodeURIComponent(collection)}/${normalizedReference}`, token),
    enabled: Boolean(collection) && Boolean(reference),
  });
}

export function useScenarioRunner() {
  const { token } = useApiToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (scenario: string) =>
      request<ScenarioRunResponse>("/api/console/run-scenario", token, {
        method: "POST",
        body: JSON.stringify({ scenario, project: defaultProject }),
        timeoutMs: LONG_RUNNING_REQUEST_TIMEOUT_MS,
      }),
    onSuccess: (payload) => {
      queryClient.setQueryData(["console-state", defaultProject, token], payload.state);
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
    },
  });
}

export function useGuardrailsDemoRunner() {
  const { token } = useApiToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (example: GuardrailsDemoExample) =>
      request<GuardrailsDemoResponse>("/api/console/guardrails-demo", token, {
        method: "POST",
        body: JSON.stringify({ example, project: defaultProject }),
        timeoutMs: LONG_RUNNING_REQUEST_TIMEOUT_MS,
      }),
    onSuccess: (payload) => {
      queryClient.setQueryData(["console-state", defaultProject, token], payload.state);
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
      if (payload.incident?.id) {
        queryClient.invalidateQueries({ queryKey: ["incident-workflow", payload.incident.id, token] });
      }
    },
  });
}

export function useSafetyProbeRunner() {
  const { token } = useApiToken();
  return useMutation({
    mutationFn: (prompt: string) =>
      request<SafetyProbeResponse>("/api/safety-controls/probe", token, {
        method: "POST",
        body: JSON.stringify({ prompt, project: defaultProject }),
        timeoutMs: LONG_RUNNING_REQUEST_TIMEOUT_MS,
      }),
  });
}

export function useWorkflowMutation<TBody extends object, TResult = unknown>(
  pathBuilder: (incidentId: string, extra?: string | number) => string,
  options?: {
    extra?: string | number;
  },
) {
  const { token } = useApiToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { incidentId: string; body: TBody }) => {
      return request<TResult>(pathBuilder(input.incidentId, options?.extra), token, {
        method: "POST",
        body: JSON.stringify(input.body),
        timeoutMs: LONG_RUNNING_REQUEST_TIMEOUT_MS,
      });
    },
    onSuccess: (_payload, variables) => {
      queryClient.invalidateQueries({ queryKey: ["incident-workflow", variables.incidentId, token] });
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
      queryClient.invalidateQueries({ queryKey: ["console-state"] });
    },
  });
}
