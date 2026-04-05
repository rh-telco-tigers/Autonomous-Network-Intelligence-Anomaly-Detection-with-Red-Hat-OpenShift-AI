"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiToken } from "@/components/providers/app-providers";
import type {
  ConsoleState,
  IncidentRecord,
  IncidentWorkflow,
  KnowledgeArticleResponse,
  ScenarioRunResponse,
  TicketLookupResponse,
} from "@/lib/types";

const defaultProject = "ims-demo";

export async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "x-api-key": token,
      ...(init?.headers ?? {}),
    },
  });
  const raw = await response.text();
  const payload = raw ? (JSON.parse(raw) as unknown) : null;
  if (!response.ok) {
    throw new Error(typeof payload === "string" ? payload : JSON.stringify(payload));
  }
  return payload as T;
}

export function useConsoleStateQuery(refetchInterval = 10_000) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["console-state", defaultProject, token],
    queryFn: () => request<ConsoleState>(`/api/console/state?project=${encodeURIComponent(defaultProject)}`, token),
    refetchInterval,
  });
}

export function useIncidentsQuery(filters: { status?: string; severity?: string; q?: string }) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["incidents", defaultProject, filters, token],
    queryFn: () => request<IncidentRecord[]>(`/api/incidents?project=${encodeURIComponent(defaultProject)}`, token),
    select: (rows) => {
      return rows.filter((row) => {
        const statusMatch = filters.status ? row.status === filters.status : true;
        const severityMatch = filters.severity ? row.severity === filters.severity : true;
        const q = (filters.q ?? "").trim().toLowerCase();
        const searchMatch = q
          ? [row.id, row.anomaly_type, row.severity, row.status, row.subtitle ?? "", row.recommendation ?? ""]
              .join(" ")
              .toLowerCase()
              .includes(q)
          : true;
        return statusMatch && severityMatch && searchMatch;
      });
    },
    refetchInterval: 10_000,
  });
}

export function useIncidentWorkflowQuery(incidentId: string) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["incident-workflow", incidentId, token],
    queryFn: () => request<IncidentWorkflow>(`/api/incidents/${encodeURIComponent(incidentId)}`, token),
    enabled: Boolean(incidentId),
    refetchInterval: 8_000,
  });
}

export function useTicketLookupQuery(provider: string, externalId: string) {
  const { token } = useApiToken();
  return useQuery({
    queryKey: ["ticket-lookup", provider, externalId, token],
    queryFn: () => request<TicketLookupResponse>(`/api/tickets/${encodeURIComponent(provider)}/${encodeURIComponent(externalId)}`, token),
    enabled: Boolean(provider) && Boolean(externalId),
    refetchInterval: 8_000,
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

export function useScenarioRunner() {
  const { token } = useApiToken();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (scenario: string) =>
      request<ScenarioRunResponse>("/api/console/run-scenario", token, {
        method: "POST",
        body: JSON.stringify({ scenario, project: defaultProject }),
      }),
    onSuccess: (payload) => {
      queryClient.setQueryData(["console-state", defaultProject, token], payload.state);
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
    },
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
      });
    },
    onSuccess: (_payload, variables) => {
      queryClient.invalidateQueries({ queryKey: ["incident-workflow", variables.incidentId, token] });
      queryClient.invalidateQueries({ queryKey: ["incidents"] });
      queryClient.invalidateQueries({ queryKey: ["console-state"] });
    },
  });
}
