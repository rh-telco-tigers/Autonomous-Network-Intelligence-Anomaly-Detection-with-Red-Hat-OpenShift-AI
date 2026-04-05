"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Bot, Info, Sparkles } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { useApiToken } from "@/components/providers/app-providers";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { request, useIncidentWorkflowQuery } from "@/lib/api";
import { resolveTicketHref } from "@/lib/ticket-links";
import type {
  IncidentActionRecord,
  IncidentRecord,
  IncidentWorkflow,
  RelatedRecords,
  RcaPayload,
  RcaRecord,
  RemediationRecord,
  ResolutionExtract,
  TicketRecord,
  VerificationRecord,
  WorkflowState,
} from "@/lib/types";
import { cn, formatRelativeNumber, formatTime, titleize } from "@/lib/utils";

const actionSchema = z.object({
  remediation_id: z.string().optional(),
  approved_by: z.string().min(1),
  notes: z.string().optional(),
});

const verificationSchema = z.object({
  action_id: z.string().optional(),
  verified_by: z.string().min(1),
  verification_status: z.string().min(1),
  notes: z.string().optional(),
  custom_resolution: z.string().optional(),
  metric_based: z.boolean().default(false),
  close_after_verify: z.boolean().default(false),
});

const ticketSchema = z.object({
  note: z.string().optional(),
  force: z.boolean().default(false),
});

type Notice = {
  kind: "success" | "warning" | "error";
  message: string;
} | null;

type RcaGenerationInfo = {
  sourceLabel: string;
  summary: string;
  model: string;
  runtime: string;
  retrievedDocumentCount: number;
  llmUsed: boolean;
  llmConfigured: boolean;
  provenanceLabel: string;
};

type SimulationPreview = {
  effectiveness: string;
  latencyImpact: string;
  confidence: string;
  summary: string;
};

type StepStatus = "current" | "done" | "todo" | "attention";

type GuideActionId =
  | "generateRca"
  | "generateRemediations"
  | "openEvidence"
  | "reviewRca"
  | "focusRemediation"
  | "focusVerification"
  | "focusTicket"
  | "focusTimeline"
  | "focusKnowledge"
  | "reviewExecution"
  | "executeSelected"
  | "closeIncident"
  | "none";

type GuideAction = {
  label: string;
  action: GuideActionId;
  disabled?: boolean;
};

type GuideStep = {
  number: number;
  title: string;
  description: string;
  status: StepStatus;
};

type FlowGuide = {
  tone: "info" | "warning" | "success";
  badge: string;
  title: string;
  description: string;
  subtext: string;
  helpers: Array<{ title: string; text: string }>;
  ticketHint: string;
  primary: GuideAction;
  secondary?: GuideAction;
  steps: GuideStep[];
};

const GUIDE_STEP_TEMPLATES = [
  {
    title: "Generate RCA",
    description: "Ground the incident in retrieved evidence before any remediation can be chosen.",
  },
  {
    title: "Generate remediations",
    description: "Map the RCA to ranked manual and automated actions with clear risk trade-offs.",
  },
  {
    title: "Approve exact action",
    description: "Approval must match the selected remediation and the current workflow revision.",
  },
  {
    title: "Execute approved fix",
    description: "Run automation or record a manual action outcome from the same workflow surface.",
  },
  {
    title: "Verify and publish knowledge",
    description: "Only verified outcomes should close the incident and become reusable knowledge.",
  },
] as const;

const STEP_STATUS_MAP: Record<WorkflowState, StepStatus[]> = {
  NEW: ["current", "todo", "todo", "todo", "todo"],
  RCA_GENERATED: ["done", "current", "todo", "todo", "todo"],
  REMEDIATION_SUGGESTED: ["done", "done", "current", "todo", "todo"],
  AWAITING_APPROVAL: ["done", "done", "current", "todo", "todo"],
  APPROVED: ["done", "done", "done", "current", "todo"],
  EXECUTING: ["done", "done", "done", "current", "todo"],
  EXECUTED: ["done", "done", "done", "done", "current"],
  VERIFIED: ["done", "done", "done", "done", "current"],
  CLOSED: ["done", "done", "done", "done", "done"],
  RCA_REJECTED: ["attention", "todo", "todo", "todo", "todo"],
  EXECUTION_FAILED: ["done", "done", "done", "attention", "todo"],
  VERIFICATION_FAILED: ["done", "done", "done", "done", "attention"],
  FALSE_POSITIVE: ["done", "done", "done", "done", "done"],
  ESCALATED: ["done", "done", "attention", "todo", "todo"],
};

const TONE_CARD_CLASSES = {
  info: "border-sky-400/20 bg-sky-500/5 ring-1 ring-sky-400/10",
  warning: "border-amber-400/20 bg-amber-500/5 ring-1 ring-amber-400/10",
  success: "border-emerald-400/20 bg-emerald-500/5 ring-1 ring-emerald-400/10",
} as const;

const TONE_BADGE_CLASSES = {
  info: "border-sky-400/30 bg-sky-500/10 text-sky-200",
  warning: "border-amber-400/30 bg-amber-500/10 text-amber-200",
  success: "border-emerald-400/30 bg-emerald-500/10 text-emerald-200",
} as const;

export function IncidentWorkflowDetail() {
  const params = useParams<{ incidentId: string }>();
  const incidentId = params.incidentId;
  const { token } = useApiToken();
  const queryClient = useQueryClient();
  const [notice, setNotice] = React.useState<Notice>(null);
  const [currentPageUrl, setCurrentPageUrl] = React.useState("");

  const evidenceRef = React.useRef<HTMLDivElement>(null);
  const rcaRef = React.useRef<HTMLDivElement>(null);
  const remediationRef = React.useRef<HTMLDivElement>(null);
  const verificationRef = React.useRef<HTMLDivElement>(null);
  const ticketRef = React.useRef<HTMLDivElement>(null);
  const timelineRef = React.useRef<HTMLDivElement>(null);
  const knowledgeRef = React.useRef<HTMLDivElement>(null);
  const executionRef = React.useRef<HTMLDivElement>(null);
  const simulationRef = React.useRef<HTMLDivElement>(null);

  const { data, isLoading, error } = useIncidentWorkflowQuery(incidentId);
  const incident = data?.incident;

  React.useEffect(() => {
    if (typeof window !== "undefined") {
      setCurrentPageUrl(window.location.href);
    }
  }, [incidentId]);

  const refreshWorkflow = React.useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["incident-workflow", incidentId, token] }),
      queryClient.invalidateQueries({ queryKey: ["incident-related", incidentId, token] }),
      queryClient.invalidateQueries({ queryKey: ["incidents"] }),
      queryClient.invalidateQueries({ queryKey: ["console-state"] }),
    ]);
  }, [incidentId, queryClient, token]);

  const relatedQuery = useQuery({
    queryKey: ["incident-related", incidentId, token],
    queryFn: () =>
      request<RelatedRecords>(`/api/incidents/${encodeURIComponent(incidentId)}/related`, token, {
        method: "POST",
        body: JSON.stringify({ limit: 6, knowledge_limit: 10 }),
      }),
    enabled: Boolean(incidentId),
    refetchInterval: 15_000,
  });

  const openTicket = React.useCallback((ticket?: Pick<TicketRecord, "provider" | "external_id" | "url"> | null) => {
    const href = resolveTicketHref(ticket);
    if (!href || typeof window === "undefined") {
      return false;
    }
    const openedWindow = window.open(href, "_blank", "noreferrer");
    return Boolean(openedWindow);
  }, []);

  const scrollToSection = React.useCallback((ref: React.RefObject<HTMLDivElement | null>) => {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const actionForm = useForm<z.input<typeof actionSchema>, unknown, z.output<typeof actionSchema>>({
    resolver: zodResolver(actionSchema),
    defaultValues: {
      remediation_id: "",
      approved_by: "demo-ui",
      notes: "",
    },
  });

  const verificationForm = useForm<z.input<typeof verificationSchema>, unknown, z.output<typeof verificationSchema>>({
    resolver: zodResolver(verificationSchema),
    defaultValues: {
      action_id: "",
      verified_by: "demo-ui",
      verification_status: "verified",
      notes: "",
      custom_resolution: "",
      metric_based: true,
      close_after_verify: true,
    },
  });

  const ticketForm = useForm<z.input<typeof ticketSchema>, unknown, z.output<typeof ticketSchema>>({
    resolver: zodResolver(ticketSchema),
    defaultValues: {
      note: "",
      force: false,
    },
  });

  const generateRcaMutation = useMutation({
    mutationFn: async () => {
      return request<{ rca: RcaPayload; workflow: IncidentWorkflow }>(
        `/api/incidents/${encodeURIComponent(incidentId)}/rca/generate`,
        token,
        {
          method: "POST",
        },
      );
    },
    onSuccess: refreshWorkflow,
  });

  const generateRemediationsMutation = useMutation({
    mutationFn: async () => {
      return request<{ workflow: IncidentWorkflow }>(
        `/api/incidents/${encodeURIComponent(incidentId)}/remediation/generate`,
        token,
        {
          method: "POST",
        },
      );
    },
    onSuccess: refreshWorkflow,
  });

  const actionMutation = useMutation({
    mutationFn: async (values: {
      remediationId?: number;
      actor: string;
      notes?: string;
      mode: "approve" | "execute" | "reject";
    }) => {
      if (!values.remediationId) {
        throw new Error("Select a remediation first.");
      }
      const path =
        values.mode === "reject"
          ? `/api/incidents/${encodeURIComponent(incidentId)}/remediation/${values.remediationId}/reject`
          : `/api/incidents/${encodeURIComponent(incidentId)}/remediation/${values.remediationId}/${values.mode}`;
      const body =
        values.mode === "reject"
          ? { rejected_by: values.actor, notes: values.notes }
          : { approved_by: values.actor, notes: values.notes };
      return request<{ action: IncidentActionRecord; workflow: IncidentWorkflow }>(path, token, {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
    onSuccess: refreshWorkflow,
  });

  const verificationMutation = useMutation({
    mutationFn: async (values: z.output<typeof verificationSchema>) => {
      return request<unknown>(`/api/incidents/${encodeURIComponent(incidentId)}/verify`, token, {
        method: "POST",
        body: JSON.stringify({
          action_id: values.action_id ? Number(values.action_id) : undefined,
          verified_by: values.verified_by,
          verification_status: values.verification_status,
          notes: values.notes,
          custom_resolution: values.custom_resolution,
          metric_based: values.metric_based,
          close_after_verify: values.close_after_verify,
        }),
      });
    },
    onSuccess: refreshWorkflow,
  });

  const closeIncidentMutation = useMutation({
    mutationFn: async (notes: string) => {
      return request<IncidentWorkflow>(`/api/incidents/${encodeURIComponent(incidentId)}/transition`, token, {
        method: "POST",
        body: JSON.stringify({ target_state: "CLOSED", notes }),
      });
    },
    onSuccess: refreshWorkflow,
  });

  const escalateIncidentMutation = useMutation({
    mutationFn: async (notes: string) => {
      return request<IncidentWorkflow>(`/api/incidents/${encodeURIComponent(incidentId)}/transition`, token, {
        method: "POST",
        body: JSON.stringify({ target_state: "ESCALATED", notes }),
      });
    },
    onSuccess: refreshWorkflow,
  });

  const ticketMutation = useMutation({
    mutationFn: async (values: z.output<typeof ticketSchema>) => {
      return request<{ ticket: TicketRecord }>(`/api/incidents/${encodeURIComponent(incidentId)}/tickets/plane`, token, {
        method: "POST",
        body: JSON.stringify({ ...values, source_url: currentPageUrl }),
      });
    },
    onSuccess: async (payload) => {
      await refreshWorkflow();
      const opened = openTicket(payload.ticket);
      const operationStatus = String(payload.ticket.operation?.status ?? "").trim().toLowerCase();
      const ticketAction = operationStatus === "created" ? "Plane ticket created" : "Plane ticket synced";
      ticketForm.reset({ note: "", force: false });
      setNotice({
        kind: "success",
        message: opened ? `${ticketAction} with RCA context and incident link, then opened in a new tab.` : `${ticketAction} with RCA context and incident link.`,
      });
    },
    onError: (mutationError) => {
      setNotice({ kind: "error", message: mutationError instanceof Error ? mutationError.message : "Plane ticket update failed." });
    },
  });

  const ticketSyncMutation = useMutation({
    mutationFn: async (ticketId: number) => {
      return request<{ ticket: TicketRecord }>(`/api/incidents/${encodeURIComponent(incidentId)}/tickets/${ticketId}/sync`, token, {
        method: "POST",
        body: JSON.stringify({
          note: "Manual sync triggered from the guided incident workflow.",
          source_url: currentPageUrl,
        }),
      });
    },
    onSuccess: async (payload) => {
      await refreshWorkflow();
      const opened = openTicket(payload.ticket);
      setNotice({ kind: "success", message: opened ? "Ticket resynced and opened in a new tab." : "Ticket resynced." });
    },
    onError: (mutationError) => {
      setNotice({ kind: "error", message: mutationError instanceof Error ? mutationError.message : "Ticket sync failed." });
    },
  });

  const preferredRemediation = React.useMemo(() => {
    if (!data) {
      return undefined;
    }
    return data.current_remediations.find((item) => item.status === "approved") ?? data.current_remediations[0];
  }, [data]);

  React.useEffect(() => {
    if (!data) {
      return;
    }
    actionForm.reset({
      remediation_id: String(preferredRemediation?.id ?? ""),
      approved_by: actionForm.getValues("approved_by") || "demo-ui",
      notes: "",
    });
    verificationForm.reset({
      action_id: String(data.actions[0]?.id ?? ""),
      verified_by: verificationForm.getValues("verified_by") || "demo-ui",
      verification_status: "verified",
      notes: "",
      custom_resolution: "",
      metric_based: true,
      close_after_verify: true,
    });
  }, [actionForm, data, preferredRemediation, verificationForm]);

  const selectedRemediationId = actionForm.watch("remediation_id");
  const selectedRemediation = React.useMemo(() => {
    if (!data) {
      return undefined;
    }
    return data.current_remediations.find((item) => String(item.id) === selectedRemediationId) ?? preferredRemediation;
  }, [data, preferredRemediation, selectedRemediationId]);

  const latestRca = data?.rca_history[0];
  const latestRcaGeneration = buildRcaGenerationInfo(latestRca);
  const latestRcaAnalysis = buildRcaAnalysis(latestRca, incident);
  const latestRcaRecommendation = buildRcaRecommendation(latestRca, incident?.recommendation);
  const latestAction = data?.actions[0];
  const latestVerification = data?.verifications[0];
  const currentTicket = React.useMemo(() => {
    if (!data) {
      return null;
    }
    const currentTicketId = data.current_ticket?.id;
    if (currentTicketId != null) {
      return data.tickets.find((ticket) => ticket.id === currentTicketId) ?? data.current_ticket ?? null;
    }
    return data.tickets[0] ?? data.current_ticket ?? null;
  }, [data]);
  const currentTicketHref = resolveTicketHref(currentTicket);

  const submitRemediationAction = React.useCallback(
    async (mode: "approve" | "execute" | "reject") => {
      await actionForm.handleSubmit(async (values) => {
        try {
          const payload = await actionMutation.mutateAsync({
            remediationId: values.remediation_id ? Number(values.remediation_id) : undefined,
            actor: values.approved_by,
            notes: values.notes,
            mode,
          });
          const executionStatus = payload.action.execution_status;
          setNotice({
            kind: mode === "execute" && executionStatus === "failed" ? "error" : "success",
            message:
              mode === "reject"
                ? "Remediation rejected."
                : mode === "execute"
                  ? executionStatus === "executing"
                    ? "Remediation approved and launched in AAP."
                    : executionStatus === "executed"
                      ? "Remediation approved and executed."
                      : payload.action.result_summary ?? "Remediation execution failed."
                  : "Remediation approved.",
          });
        } catch (mutationError) {
          setNotice({
            kind: "error",
            message: mutationError instanceof Error ? mutationError.message : "Remediation action failed.",
          });
        }
      })();
    },
    [actionForm, actionMutation],
  );

  const handleGuideAction = React.useCallback(
    async (action: GuideActionId) => {
      switch (action) {
        case "generateRca":
          try {
            const payload = await generateRcaMutation.mutateAsync();
            const generation = buildRcaGenerationInfo(payload.rca);
            setNotice({
              kind: generation.llmUsed ? "success" : generation.llmConfigured ? "warning" : "success",
              message: generation.llmUsed
                ? `RCA generated using ${generation.sourceLabel}${generation.model !== "Not recorded" ? ` with ${generation.model}` : ""}.`
                : generation.llmConfigured
                  ? "RCA generated using the local fallback path. The LLM was configured but did not produce the final answer for this run."
                  : "RCA generated using the local fallback path because no LLM endpoint was configured.",
            });
          } catch (mutationError) {
            setNotice({
              kind: "error",
              message: mutationError instanceof Error ? mutationError.message : "RCA generation failed.",
            });
          }
          break;
        case "generateRemediations":
          try {
            await generateRemediationsMutation.mutateAsync();
            setNotice({ kind: "success", message: "Remediation options generated." });
          } catch (mutationError) {
            setNotice({
              kind: "error",
              message: mutationError instanceof Error ? mutationError.message : "Remediation generation failed.",
            });
          }
          break;
        case "executeSelected":
          await submitRemediationAction("execute");
          break;
        case "closeIncident":
          try {
            await closeIncidentMutation.mutateAsync("Incident closed after successful guided verification.");
            setNotice({ kind: "success", message: "Incident closed." });
          } catch (mutationError) {
            setNotice({
              kind: "error",
              message: mutationError instanceof Error ? mutationError.message : "Incident close failed.",
            });
          }
          break;
        case "openEvidence":
          scrollToSection(evidenceRef);
          break;
        case "reviewRca":
          scrollToSection(rcaRef);
          break;
        case "focusRemediation":
          scrollToSection(remediationRef);
          break;
        case "focusVerification":
          scrollToSection(verificationRef);
          break;
        case "focusTicket":
          scrollToSection(ticketRef);
          break;
        case "focusTimeline":
          scrollToSection(timelineRef);
          break;
        case "focusKnowledge":
          scrollToSection(knowledgeRef);
          break;
        case "reviewExecution":
          scrollToSection(executionRef);
          break;
        case "none":
          break;
      }
    },
    [
      closeIncidentMutation,
      evidenceRef,
      executionRef,
      generateRcaMutation,
      generateRemediationsMutation,
      knowledgeRef,
      rcaRef,
      remediationRef,
      scrollToSection,
      submitRemediationAction,
      ticketRef,
      timelineRef,
      verificationRef,
    ],
  );

  if (isLoading) {
    return <div className="text-sm text-[var(--text-muted)]">Loading guided incident workflow...</div>;
  }

  if (error || !incident || !data) {
    return <div className="text-sm text-[var(--danger-fg)]">Could not load this incident workflow.</div>;
  }

  const hasRca = Boolean(latestRca);
  const hasRemediations = data.current_remediations.length > 0;
  const hasTicket = Boolean(currentTicket);
  const hasActionHistory = data.actions.length > 0;
  const hasVerification = data.verifications.length > 0 || ["VERIFIED", "CLOSED", "FALSE_POSITIVE"].includes(incident.status);
  const verificationUnlocked =
    hasActionHistory || ["EXECUTED", "EXECUTING", "VERIFIED", "CLOSED", "VERIFICATION_FAILED", "FALSE_POSITIVE"].includes(incident.status);
  const ticketUnlocked = hasRca || hasTicket || incident.status === "ESCALATED";
  const observedSignals = buildObservedSignals(incident);
  const flowGuide = deriveFlowGuide({
    state: incident.status,
    selectedRemediation,
    hasRca,
    hasRemediations,
    hasTicket,
    hasExecutionHistory: hasActionHistory,
    hasVerification,
  });
  const relatedData = relatedQuery.data;
  const pending =
    generateRcaMutation.isPending ||
    generateRemediationsMutation.isPending ||
    actionMutation.isPending ||
    verificationMutation.isPending ||
    escalateIncidentMutation.isPending ||
    ticketMutation.isPending ||
    ticketSyncMutation.isPending ||
    closeIncidentMutation.isPending;

  const selectedRemediationMode = remediationMode(selectedRemediation);
  const selectedRemediationPreview = selectedRemediation ? buildRemediationPreview(selectedRemediation) : "Choose a remediation to see its mapped action details.";
  const ticketAutoSyncHint = currentTicket
    ? `Operator notes entered in this workflow will also be posted to the current ${currentTicket.provider.toUpperCase()} ticket.`
    : "Create a Plane ticket here to mirror later operator updates automatically.";
  const currentTicketMetadata = (currentTicket?.metadata ?? {}) as Record<string, unknown>;
  const currentTicketSourceUrl = asStringValue(currentTicketMetadata.source_url);
  const incidentWorkspaceHref = currentPageUrl || currentTicketSourceUrl;
  const commandCenterSummary = selectedRemediation ? selectedRemediationPreview : latestRcaRecommendation;
  const decisionRisk = titleize(selectedRemediation?.risk_level ?? (incident.severity === "Critical" ? "medium" : "low"));
  const rcaConfidenceLabel = hasRca ? `${Math.round(Number(latestRca?.confidence ?? 0) * 100)}%` : "Pending";
  const simulationPreview = buildSimulationPreview(incident, selectedRemediation, latestRca);
  const translatedEvidence = buildHumanEvidenceSummary(incident, observedSignals);
  const recommendedAlternatives = data.current_remediations.filter((item) => item.id !== selectedRemediation?.id).slice(0, 2);
  const commandMode = !hasRca
    ? "rca"
    : !hasRemediations
      ? "remediation"
      : ["EXECUTED", "EXECUTING", "VERIFIED", "VERIFICATION_FAILED"].includes(incident.status)
        ? "verification"
        : ["CLOSED", "FALSE_POSITIVE"].includes(incident.status)
          ? "review"
          : "decision";

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Incident workflow"
        title={titleize(incident.anomaly_type)}
        description="Guide the operator through RCA, remediation, approval, execution, verification, and reusable knowledge capture from one state-driven surface."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <div className={cn("rounded-full border px-3 py-1 text-xs font-medium", TONE_BADGE_CLASSES[flowGuide.tone])}>{flowGuide.badge}</div>
            <StatusBadge value={incident.status} />
          </div>
        }
      />

      {notice ? (
        <div className="fixed bottom-4 left-4 right-4 z-50 md:left-auto md:max-w-md" role="status" aria-live="polite">
          <div
            className={cn(
              "flex items-start gap-3 rounded-2xl border px-4 py-3 text-sm shadow-2xl backdrop-blur",
              notice.kind === "success"
                ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-200"
                : notice.kind === "warning"
                  ? "border-amber-500/20 bg-amber-500/10 text-amber-200"
                  : "border-rose-500/20 bg-rose-500/10 text-rose-200",
            )}
          >
            <div className="flex-1">{notice.message}</div>
            <button
              type="button"
              className="text-xs font-medium uppercase tracking-[0.15em] opacity-80 transition-opacity hover:opacity-100"
              onClick={() => setNotice(null)}
            >
              Dismiss
            </button>
          </div>
        </div>
      ) : null}

      <div className="sticky top-4 z-30">
        <WorkflowStageDock flowGuide={flowGuide} status={incident.status} />
      </div>

      <Card className={cn(TONE_CARD_CLASSES[flowGuide.tone], "overflow-hidden")}>
        <CardHeader className="gap-6">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div>
              <div className="text-[11px] uppercase tracking-[0.35em] text-[var(--text-muted)]">Command center</div>
              <CardTitle className="mt-2 text-2xl sm:text-3xl">
                {selectedRemediation?.title ?? flowGuide.title}
              </CardTitle>
              <CardDescription className="mt-3 max-w-5xl text-sm leading-6">
                {commandCenterSummary}
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge value={incident.severity} />
              <StatusBadge value={incident.status} />
              <div className={cn("rounded-full border px-3 py-1 text-xs font-medium", TONE_BADGE_CLASSES[flowGuide.tone])}>
                {flowGuide.badge}
              </div>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <SummaryItem label="Operational impact" value={incident.impact ?? incident.subtitle ?? "Assessing impact"} />
            <SummaryItem label="Blast radius" value={incident.blast_radius ?? "Not yet mapped"} />
            <SummaryItem label="AI confidence" value={rcaConfidenceLabel} />
            <SummaryItem label="Decision risk" value={decisionRisk} />
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-6 xl:grid-cols-[1.45fr_0.95fr]">
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-3xl border border-emerald-500/20 bg-emerald-500/8 p-5">
                  <div className="text-xs uppercase tracking-[0.2em] text-emerald-200/80">AI analysis</div>
                  <div className="mt-3 text-lg font-semibold text-[var(--text-strong)]">
                    {latestRca?.root_cause ?? "RCA is still being generated"}
                  </div>
                  <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">{latestRcaAnalysis}</p>
                </div>
                <div className="rounded-3xl border border-amber-500/20 bg-amber-500/8 p-5">
                  <div className="text-xs uppercase tracking-[0.2em] text-amber-200/80">Operator takeaway</div>
                  <div className="mt-3 text-lg font-semibold text-[var(--text-strong)]">
                    {selectedRemediation?.title ?? latestRcaRecommendation}
                  </div>
                  <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">
                    {selectedRemediation?.expected_outcome ??
                      "Choose the safest action that reduces customer impact without widening the blast radius."}
                  </p>
                </div>
              </div>

              <div className="rounded-3xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Why this recommendation is on top</div>
                    <div className="mt-3 text-base font-semibold text-[var(--text-strong)]">
                      {selectedRemediation ? selectedRemediation.title : "Recommendation pending"}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <div className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]">
                      RCA source: {latestRcaGeneration.sourceLabel}
                    </div>
                    <div className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]">
                      Action type: {selectedRemediationMode}
                    </div>
                  </div>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-3">
                  {flowGuide.helpers.map((helper) => (
                    <div key={helper.title} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
                      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{helper.title}</div>
                      <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{helper.text}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="rounded-3xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Operator decision</div>
                  <div className="mt-2 text-lg font-semibold text-[var(--text-strong)]">
                    {commandMode === "verification"
                      ? "Verify the outcome"
                      : commandMode === "review"
                        ? "Review and communicate outcome"
                        : "Take the next safest step"}
                  </div>
                </div>
                <StatusBadge value={incident.status} />
              </div>

              {commandMode === "rca" ? (
                <div className="mt-5 space-y-4">
                  <p className="text-sm leading-6 text-[var(--text-secondary)]">
                    RCA is the first gate. Generate a grounded explanation before ranking or executing any remediation.
                  </p>
                  <div className="flex flex-wrap gap-3">
                    <Button onClick={() => handleGuideAction("generateRca")} disabled={pending}>
                      {generateRcaMutation.isPending ? "Generating..." : "Generate RCA"}
                    </Button>
                    <Button variant="secondary" onClick={() => scrollToSection(evidenceRef)}>
                      Review evidence first
                    </Button>
                    <Button
                      variant="outline"
                      onClick={async () => {
                        try {
                          await escalateIncidentMutation.mutateAsync("Operator escalated before RCA approval.");
                          scrollToSection(ticketRef);
                          setNotice({ kind: "warning", message: "Incident escalated. Coordinate through the Plane ticket workflow." });
                        } catch (mutationError) {
                          setNotice({
                            kind: "error",
                            message: mutationError instanceof Error ? mutationError.message : "Escalation failed.",
                          });
                        }
                      }}
                      disabled={pending || escalateIncidentMutation.isPending}
                    >
                      Escalate
                    </Button>
                  </div>
                </div>
              ) : commandMode === "remediation" ? (
                <div className="mt-5 space-y-4">
                  <p className="text-sm leading-6 text-[var(--text-secondary)]">
                    RCA is ready. Generate ranked remediations so the operator can compare impact, risk, and rollback cost from one place.
                  </p>
                  <div className="flex flex-wrap gap-3">
                    <Button onClick={() => handleGuideAction("generateRemediations")} disabled={pending}>
                      {generateRemediationsMutation.isPending ? "Generating..." : "Generate remediations"}
                    </Button>
                    <Button variant="secondary" onClick={() => scrollToSection(rcaRef)}>
                      Review RCA
                    </Button>
                    <Button
                      variant="outline"
                      onClick={async () => {
                        try {
                          await escalateIncidentMutation.mutateAsync("Operator escalated after RCA review.");
                          scrollToSection(ticketRef);
                          setNotice({ kind: "warning", message: "Incident escalated. Use the ticket workflow to coordinate next steps." });
                        } catch (mutationError) {
                          setNotice({
                            kind: "error",
                            message: mutationError instanceof Error ? mutationError.message : "Escalation failed.",
                          });
                        }
                      }}
                      disabled={pending || escalateIncidentMutation.isPending}
                    >
                      Escalate
                    </Button>
                  </div>
                </div>
              ) : commandMode === "verification" ? (
                <form
                  className="mt-5 space-y-4"
                  onSubmit={verificationForm.handleSubmit(async (values) => {
                    try {
                      await verificationMutation.mutateAsync(values);
                      setNotice({ kind: "success", message: "Verification recorded." });
                    } catch (mutationError) {
                      setNotice({
                        kind: "error",
                        message: mutationError instanceof Error ? mutationError.message : "Verification failed.",
                      });
                    }
                  })}
                >
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <Label htmlFor="action_id">Related action</Label>
                      <Select id="action_id" {...verificationForm.register("action_id")}>
                        <option value="">No action selected</option>
                        {data.actions.map((action) => (
                          <option key={action.id} value={String(action.id)}>
                            {action.id} · {action.result_summary ?? action.execution_status}
                          </option>
                        ))}
                      </Select>
                    </div>
                    <div>
                      <Label htmlFor="verification_status">Outcome</Label>
                      <Select id="verification_status" {...verificationForm.register("verification_status")}>
                        <option value="verified">verified</option>
                        <option value="failed">failed</option>
                        <option value="false_positive">false_positive</option>
                      </Select>
                    </div>
                    <div>
                      <Label htmlFor="verified_by">Actor</Label>
                      <Input id="verified_by" {...verificationForm.register("verified_by")} />
                    </div>
                    <div className="rounded-2xl border border-sky-400/20 bg-sky-500/8 p-4 text-sm leading-6 text-[var(--text-secondary)]">
                      {ticketAutoSyncHint}
                    </div>
                  </div>
                  <div>
                    <Label htmlFor="verification_notes">Verification notes</Label>
                    <Textarea id="verification_notes" placeholder="What changed after execution?" {...verificationForm.register("notes")} />
                  </div>
                  <div>
                    <Label htmlFor="custom_resolution">Actual fix applied</Label>
                    <Textarea
                      id="custom_resolution"
                      placeholder="Record the real operator fix so it can become reusable knowledge."
                      {...verificationForm.register("custom_resolution")}
                    />
                  </div>
                  <div className="flex flex-wrap gap-4 text-sm text-[var(--text-secondary)]">
                    <label className="flex items-center gap-2">
                      <input type="checkbox" {...verificationForm.register("metric_based")} />
                      Evidence includes metric-based verification
                    </label>
                    <label className="flex items-center gap-2">
                      <input type="checkbox" {...verificationForm.register("close_after_verify")} />
                      Close the incident after successful verification
                    </label>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <Button type="submit" disabled={verificationMutation.isPending}>
                      {verificationMutation.isPending ? "Saving..." : "Record verification"}
                    </Button>
                    <Button variant="secondary" type="button" onClick={() => scrollToSection(simulationRef)}>
                      Review simulation
                    </Button>
                    <Button variant="outline" type="button" onClick={() => scrollToSection(ticketRef)}>
                      Update Plane ticket
                    </Button>
                  </div>
                </form>
              ) : commandMode === "review" ? (
                <div className="mt-5 space-y-4">
                  <p className="text-sm leading-6 text-[var(--text-secondary)]">
                    The workflow is complete. Keep the ticket synchronized, review the audit trail, and preserve the verified outcome for future incidents.
                  </p>
                  <div className="flex flex-wrap gap-3">
                    <Button variant="secondary" onClick={() => scrollToSection(timelineRef)}>
                      Review timeline
                    </Button>
                    <Button variant="secondary" onClick={() => scrollToSection(ticketRef)}>
                      Open ticket workflow
                    </Button>
                    <Button variant="outline" onClick={() => scrollToSection(knowledgeRef)}>
                      Review technical details
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-5 space-y-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <Label htmlFor="remediation_id">Primary action</Label>
                      <Select id="remediation_id" {...actionForm.register("remediation_id")}>
                        <option value="">Select remediation</option>
                        {data.current_remediations.map((remediation) => (
                          <option key={remediation.id} value={String(remediation.id)}>
                            #{remediation.suggestion_rank} {remediation.title}
                          </option>
                        ))}
                      </Select>
                    </div>
                    <div>
                      <Label htmlFor="approved_by">Actor</Label>
                      <Input id="approved_by" {...actionForm.register("approved_by")} />
                    </div>
                  </div>

                  <div>
                    <Label htmlFor="approval_notes">Operator note</Label>
                    <Textarea
                      id="approval_notes"
                      placeholder="Why is this the safest next move right now?"
                      {...actionForm.register("notes")}
                    />
                  </div>

                  <div className="rounded-2xl border border-sky-400/20 bg-sky-500/8 p-4 text-sm leading-6 text-[var(--text-secondary)]">
                    {ticketAutoSyncHint}
                  </div>

                  <div className="flex flex-wrap gap-3">
                    <Button onClick={() => submitRemediationAction("execute")} disabled={pending || !selectedRemediation}>
                      Approve & execute
                    </Button>
                    <Button variant="secondary" onClick={() => scrollToSection(simulationRef)} disabled={!selectedRemediation}>
                      Simulate first
                    </Button>
                    <Button
                      variant="outline"
                      onClick={async () => {
                        try {
                          await escalateIncidentMutation.mutateAsync(
                            actionForm.getValues("notes") || "Operator escalated instead of executing the current remediation.",
                          );
                          scrollToSection(ticketRef);
                          setNotice({ kind: "warning", message: "Incident escalated. Continue coordination in the Plane ticket workflow." });
                        } catch (mutationError) {
                          setNotice({
                            kind: "error",
                            message: mutationError instanceof Error ? mutationError.message : "Escalation failed.",
                          });
                        }
                      }}
                      disabled={pending || escalateIncidentMutation.isPending}
                    >
                      Escalate
                    </Button>
                    <Button variant="ghost" onClick={() => submitRemediationAction("approve")} disabled={pending || !selectedRemediation}>
                      Approve only
                    </Button>
                    <Button variant="danger" onClick={() => submitRemediationAction("reject")} disabled={pending || !selectedRemediation}>
                      Reject selected
                    </Button>
                  </div>

                  {!selectedRemediation ? (
                    <InlineEmptyState
                      title="Select one remediation"
                      description="Choose the safest remediation first. This is the only action path that should be active at one time."
                    />
                  ) : null}
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[1.45fr_0.95fr]">
        <div className="space-y-6">
          <div ref={evidenceRef}>
            <Card>
              <CardHeader className="flex-row items-start justify-between gap-4">
                <div>
                  <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">System evidence</div>
                  <CardTitle className="mt-1">What changed in human terms</CardTitle>
                  <CardDescription>Translate model and signal data into an operator-readable summary before going deeper.</CardDescription>
                </div>
                <Button variant="secondary" onClick={() => scrollToSection(timelineRef)}>
                  What changed?
                </Button>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                {translatedEvidence.map((item) => (
                  <div key={item.title} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                    <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{item.title}</div>
                    <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">{item.detail}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <div ref={rcaRef}>
            <Card>
              <CardHeader className="flex-row items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">
                    <Sparkles className="h-3.5 w-3.5 text-[var(--accent)]" aria-hidden="true" />
                    <span>AI analysis</span>
                  </div>
                  <CardTitle className="mt-1">Root cause and recommendation</CardTitle>
                  <CardDescription>The AI explanation is kept separate from raw system signals so operators can judge it clearly.</CardDescription>
                </div>
                {!hasRca ? (
                  <Button variant="secondary" onClick={() => handleGuideAction("generateRca")} disabled={pending}>
                    {generateRcaMutation.isPending ? "Generating..." : "Generate RCA"}
                  </Button>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <AiGeneratedBadge generation={latestRcaGeneration} />
                    <div
                      className={cn(
                        "rounded-full border px-3 py-1 text-xs font-medium",
                        latestRcaGeneration.llmUsed
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                          : latestRcaGeneration.llmConfigured
                            ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                            : "border-[var(--border-subtle)] bg-[var(--surface-subtle)] text-[var(--text-secondary)]",
                      )}
                    >
                      {latestRcaGeneration.sourceLabel}
                    </div>
                  </div>
                )}
              </CardHeader>
              <CardContent className="space-y-4">
                {hasRca ? (
                  <div className="flex items-start gap-3 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                    <div
                      className={cn(
                        "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border",
                        latestRcaGeneration.llmUsed
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                          : latestRcaGeneration.llmConfigured
                            ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                            : "border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--text-secondary)]",
                      )}
                    >
                      {latestRcaGeneration.llmUsed ? <Bot className="h-4 w-4" aria-hidden="true" /> : <Info className="h-4 w-4" aria-hidden="true" />}
                    </div>
                    <div className="min-w-0">
                      <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{latestRcaGeneration.provenanceLabel}</div>
                      <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{latestRcaGeneration.summary}</p>
                    </div>
                  </div>
                ) : null}
                <div className="rounded-3xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-5">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Analysis</div>
                    {hasRca ? <AiContentPill generation={latestRcaGeneration} /> : null}
                  </div>
                  <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">{latestRcaAnalysis}</p>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <SummaryItem
                    label="Root cause summary"
                    value={latestRca?.root_cause || "Pending RCA"}
                    meta={hasRca ? <AiContentPill generation={latestRcaGeneration} /> : undefined}
                  />
                  <SummaryItem
                    label="Recommended action"
                    value={latestRcaRecommendation}
                    meta={hasRca ? <AiContentPill generation={latestRcaGeneration} /> : undefined}
                  />
                </div>
                <div className="grid gap-4 md:grid-cols-4">
                  <SummaryItem label="Confidence" value={rcaConfidenceLabel} />
                  <SummaryItem label="Runtime" value={latestRcaGeneration.runtime} />
                  <SummaryItem label="Model" value={latestRcaGeneration.model} />
                  <SummaryItem label="Retrieved docs" value={String(latestRcaGeneration.retrievedDocumentCount)} />
                </div>
                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Evidence references</div>
                  <div className="mt-3 space-y-3">
                    {incident.evidence_sources?.length ? (
                      incident.evidence_sources.map((item) => (
                        <div key={`${item.title}-${item.detail}`} className="rounded-xl bg-[var(--surface-raised)] p-3">
                          <div className="font-medium text-[var(--text-strong)]">{item.title}</div>
                          <div className="mt-1 text-sm text-[var(--text-secondary)]">{item.detail}</div>
                        </div>
                      ))
                    ) : (
                      <InlineEmptyState
                        title="No evidence references yet"
                        description="Generate or refresh RCA to attach the exact evidence references used by the reasoning flow."
                      />
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div ref={remediationRef}>
            <Card>
              <CardHeader className="flex-row items-start justify-between gap-4">
                <div>
                  <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Action options</div>
                  <CardTitle className="mt-1">One primary action, alternatives kept nearby</CardTitle>
                  <CardDescription>Operators should see the recommended action immediately without losing access to safer fallbacks.</CardDescription>
                </div>
                {hasRca && !hasRemediations ? (
                  <Button variant="secondary" onClick={() => handleGuideAction("generateRemediations")} disabled={pending}>
                    {generateRemediationsMutation.isPending ? "Generating..." : "Generate remediations"}
                  </Button>
                ) : null}
              </CardHeader>
              <CardContent className="space-y-4">
                {hasRemediations ? (
                  <>
                    <div className="rounded-3xl border border-cyan-400/25 bg-cyan-500/8 p-5">
                      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                        <div>
                          <div className="text-xs uppercase tracking-[0.2em] text-cyan-200/80">Primary action</div>
                          <div className="mt-2 text-lg font-semibold text-[var(--text-strong)]">
                            {selectedRemediation?.title ?? "Select a remediation"}
                          </div>
                          <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">{selectedRemediationPreview}</p>
                        </div>
                        {selectedRemediation ? <StatusBadge value={selectedRemediation.risk_level} /> : null}
                      </div>
                      <div className="mt-4 grid gap-4 md:grid-cols-4">
                        <SummaryItem label="Action type" value={selectedRemediationMode} />
                        <SummaryItem label="Rank score" value={formatRelativeNumber(selectedRemediation?.rank_score ?? 0, 3)} />
                        <SummaryItem label="Automation" value={titleize(selectedRemediation?.automation_level ?? "pending")} />
                        <SummaryItem label="Revision scope" value={`Revision ${selectedRemediation?.based_on_revision ?? incident.workflow_revision}`} />
                      </div>
                    </div>

                    <div className="grid gap-3">
                      {recommendedAlternatives.length ? (
                        recommendedAlternatives.map((remediation) => (
                          <div key={remediation.id} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <div className="font-medium text-[var(--text-strong)]">
                                  #{remediation.suggestion_rank} {remediation.title}
                                </div>
                                <div className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">{remediation.description}</div>
                              </div>
                              <StatusBadge value={remediation.status || remediation.risk_level} />
                            </div>
                          </div>
                        ))
                      ) : (
                        <InlineEmptyState
                          title="No alternative remediations"
                          description="The current RCA produced only one safe remediation path for this workflow revision."
                        />
                      )}
                    </div>
                  </>
                ) : (
                  <InlineEmptyState
                    title="No remediations generated yet"
                    description="Generate ranked remediations from the current RCA so the operator can choose one exact action safely."
                  />
                )}
              </CardContent>
            </Card>
          </div>

          <div ref={knowledgeRef}>
            <Card>
              <CardHeader>
                <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Deep technical details</div>
                <CardTitle className="mt-1">Expand when you need proof, not before</CardTitle>
                <CardDescription>The core decision stays above. Detailed workflow, model, and retrieval context stays available on demand.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <details className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <summary className="cursor-pointer text-sm font-semibold text-[var(--text-strong)]">Workflow, model, and feature context</summary>
                  <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                    <SummaryItem label="Incident ID" value={incident.id} />
                    <SummaryItem label="Workflow revision" value={String(incident.workflow_revision)} />
                    <SummaryItem label="Model version" value={incident.model_version} />
                    <SummaryItem label="Feature window" value={incident.feature_window_id ?? "Unavailable"} />
                    <SummaryItem label="Plane state" value={data.plane_workflow_state} />
                    <SummaryItem label="Workflow state" value={<StatusBadge value={incident.status} />} />
                    <SummaryItem label="Anomaly score" value={formatRelativeNumber(incident.anomaly_score)} />
                    <SummaryItem label="Updated" value={formatTime(incident.updated_at)} />
                  </div>
                </details>

                <details className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <summary className="cursor-pointer text-sm font-semibold text-[var(--text-strong)]">Explainability and retrieved knowledge</summary>
                  <div className="mt-4 space-y-4">
                    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                      <SummaryItem label="Evidence matches" value={String(relatedData?.evidence.length ?? 0)} />
                      <SummaryItem label="Reasoning matches" value={String(relatedData?.reasoning.length ?? 0)} />
                      <SummaryItem label="Resolution matches" value={String(relatedData?.resolution.length ?? 0)} />
                      <SummaryItem label="Knowledge articles" value={String(relatedData?.knowledge.length ?? 0)} />
                    </div>
                    <div className="grid gap-4 md:grid-cols-2">
                      {(incident.explainability ?? []).slice(0, 4).map((item) => (
                        <div key={item.feature} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
                          <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{item.label}</div>
                          <div className="mt-2 text-sm text-[var(--text-strong)]">Weight {formatRelativeNumber(item.weight, 2)}</div>
                        </div>
                      ))}
                    </div>
                    {relatedQuery.isLoading ? (
                      <div className="text-sm text-[var(--text-muted)]">Loading retrieved context...</div>
                    ) : (
                      <div className="space-y-4">
                        {relatedData?.documents.length ? (
                          <div className="space-y-3">
                            <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Retrieved context</div>
                            {relatedData.documents.map((document) => (
                              <div
                                key={`${document.collection}-${document.reference}`}
                                className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4"
                              >
                                <div className="flex items-center justify-between gap-3">
                                  <div className="font-medium text-[var(--text-strong)]">{document.title}</div>
                                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">
                                    {titleize(document.collection)}
                                  </div>
                                </div>
                                <div className="mt-1 text-sm text-[var(--text-secondary)]">{truncateText(document.content, 180)}</div>
                              </div>
                            ))}
                          </div>
                        ) : null}

                        {relatedData?.knowledge.length ? (
                          <div className="space-y-3">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                              <div>
                                <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Knowledge articles</div>
                                <div className="mt-1 text-sm text-[var(--text-secondary)]">
                                  Category-matched remediation articles stored in Milvus for this incident type.
                                </div>
                              </div>
                              <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">
                                {relatedData.knowledge.length} available
                              </div>
                            </div>
                            {relatedData.knowledge.map((article) => (
                              <div
                                key={`${article.collection}-${article.reference}`}
                                className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4"
                              >
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                  <div className="min-w-0 flex-1">
                                    <div className="font-medium text-[var(--text-strong)]">{article.title}</div>
                                    <div className="mt-1 text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">
                                      {titleize(article.category ?? "knowledge")} · score {formatRelativeNumber(article.score, 2)}
                                    </div>
                                  </div>
                                  <Button asChild variant="secondary" className="shrink-0">
                                    <Link href={knowledgeArticleHref(incident.id, article.reference)}>View article</Link>
                                  </Button>
                                </div>
                                <div className="mt-2 text-sm text-[var(--text-secondary)]">{truncateText(article.content, 220)}</div>
                              </div>
                            ))}
                          </div>
                        ) : null}

                        {!relatedData?.documents.length && !relatedData?.knowledge.length ? (
                          <InlineEmptyState
                            title="No related knowledge yet"
                            description="The platform will surface similar evidence, reasoning, verified outcomes, and category articles after retrieval completes."
                          />
                        ) : null}
                      </div>
                    )}
                  </div>
                </details>

                <details className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <summary className="cursor-pointer text-sm font-semibold text-[var(--text-strong)]">Workflow step definitions</summary>
                  <div className="mt-4">
                    <StepReferenceCard steps={flowGuide.steps} planeState={data.plane_workflow_state} />
                  </div>
                </details>
              </CardContent>
            </Card>
          </div>
        </div>

        <div className="space-y-6">
          <div ref={timelineRef}>
            <Card>
              <CardHeader className="flex-row items-start justify-between gap-4">
                <div>
                  <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Incident timeline</div>
                  <CardTitle className="mt-1">What changed?</CardTitle>
                  <CardDescription>Operators trust the recommendation more when the sequence of events is explicit.</CardDescription>
                </div>
                <Button variant="secondary" onClick={() => void refreshWorkflow()}>
                  Refresh
                </Button>
              </CardHeader>
              <CardContent className="space-y-4">
                {incident.timeline?.length ? (
                  incident.timeline.map((entry) => (
                    <div key={`${entry.time}-${entry.title}`} className="flex gap-3">
                      <div className="mt-1 h-3 w-3 rounded-full bg-sky-400" />
                      <div>
                        <div className="font-medium text-[var(--text-strong)]">
                          {entry.title} · <span className="text-[var(--text-muted)]">{formatTime(entry.time)}</span>
                        </div>
                        <div className="text-sm leading-6 text-[var(--text-secondary)]">{entry.detail}</div>
                      </div>
                    </div>
                  ))
                ) : (
                  <InlineEmptyState
                    title="No timeline entries yet"
                    description="Generate RCA, choose a remediation, and record verification to build the incident story."
                  />
                )}
              </CardContent>
            </Card>
          </div>

          <div ref={simulationRef}>
            <Card>
              <CardHeader>
                <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Simulation preview</div>
                <CardTitle className="mt-1">Estimate impact before execution</CardTitle>
                <CardDescription>This preview is derived from the current RCA confidence, remediation rank, and recorded risk level.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 md:grid-cols-3">
                  <SummaryItem label="Predicted effectiveness" value={simulationPreview.effectiveness} />
                  <SummaryItem label="Latency impact" value={simulationPreview.latencyImpact} />
                  <SummaryItem label="Preview confidence" value={simulationPreview.confidence} />
                </div>
                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm leading-6 text-[var(--text-secondary)]">
                  {simulationPreview.summary}
                </div>
              </CardContent>
            </Card>
          </div>

          <div ref={ticketRef}>
            <Card className={cn(!ticketUnlocked && "opacity-70")}>
              <CardHeader className="flex-row items-start justify-between gap-4">
                <div>
                  <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Operator collaboration</div>
                  <CardTitle className="mt-1">Plane ticket workflow</CardTitle>
                  <CardDescription>Create the ticket once, then let action and verification notes keep it updated automatically.</CardDescription>
                </div>
                <div className="rounded-full border border-amber-400/30 bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-200">
                  {ticketUnlocked ? "Ready" : "Wait for RCA"}
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="rounded-2xl border border-amber-400/20 bg-amber-500/8 p-4 text-sm leading-6 text-[var(--text-secondary)]">
                  {flowGuide.ticketHint}
                </div>
                <div className="rounded-2xl border border-sky-400/20 bg-sky-500/8 p-4 text-sm leading-6 text-[var(--text-secondary)]">
                  {ticketAutoSyncHint}
                </div>

                {currentTicket ? (
                  <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="font-medium text-[var(--text-strong)]">{currentTicket.title ?? "Current ticket"}</div>
                        <div className="text-sm text-[var(--text-secondary)]">
                          {currentTicket.provider.toUpperCase()} · {currentTicket.external_key ?? currentTicket.external_id}
                        </div>
                      </div>
                      <StatusBadge value={currentTicket.sync_state ?? "synced"} />
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {currentTicketHref ? (
                        <Button asChild variant="secondary">
                          <a href={currentTicketHref} target="_blank" rel="noreferrer">
                            Open ticket
                          </a>
                        </Button>
                      ) : null}
                      {incidentWorkspaceHref ? (
                        <Button asChild variant="outline">
                          <a href={incidentWorkspaceHref} target="_blank" rel="noreferrer">
                            Incident workspace
                          </a>
                        </Button>
                      ) : null}
                      <Button
                        variant="outline"
                        onClick={() => ticketSyncMutation.mutate(Number(currentTicket.id))}
                        disabled={ticketSyncMutation.isPending}
                      >
                        {ticketSyncMutation.isPending ? "Syncing..." : "Quick resync"}
                      </Button>
                    </div>
                    {currentTicket.comments?.length ? (
                      <div className="mt-4 space-y-3">
                        <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Recent ticket updates</div>
                        {currentTicket.comments.slice(0, 3).map((comment) => (
                          <div key={comment.external_comment_id} className="rounded-xl bg-[var(--surface-raised)] p-3">
                            <div className="text-sm font-medium text-[var(--text-strong)]">
                              {comment.author ?? "IMS Platform"} · {formatTime(comment.updated_at)}
                            </div>
                            <div className="mt-1 whitespace-pre-wrap text-sm leading-6 text-[var(--text-secondary)]">
                              {comment.body ?? "No comment body recorded."}
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}

                <form
                  className="space-y-3"
                  onSubmit={ticketForm.handleSubmit(async (values) => {
                    if (!ticketUnlocked) {
                      return;
                    }
                    try {
                      await ticketMutation.mutateAsync(values);
                    } catch {
                      // Notice handling is already centralized in the mutation callbacks.
                    }
                  })}
                >
                  <Label htmlFor="ticket_note">Ticket note</Label>
                  <Textarea
                    id="ticket_note"
                    disabled={!ticketUnlocked || ticketMutation.isPending}
                    placeholder={
                      currentTicket
                        ? "Add a collaboration update before syncing the Plane ticket."
                        : "Add the initial context to include with the RCA and incident link."
                    }
                    {...ticketForm.register("note")}
                  />
                  <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                    <input type="checkbox" disabled={!ticketUnlocked || ticketMutation.isPending} {...ticketForm.register("force")} />
                    Force creation even if policy would normally skip it
                  </label>
                  <Button type="submit" className="w-full" disabled={!ticketUnlocked || ticketMutation.isPending}>
                    {ticketMutation.isPending
                      ? "Syncing..."
                      : currentTicket
                        ? "Sync Plane ticket with latest RCA"
                        : "Create Plane ticket with RCA"}
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>

          <div ref={verificationRef}>
            <Card>
              <CardHeader>
                <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Execution and verification</div>
                <CardTitle className="mt-1">Latest operator outcome</CardTitle>
                <CardDescription>Execution status and verification notes remain visible even when the active step moves on.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div ref={executionRef} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Latest execution</div>
                  {latestAction ? (
                    <>
                      <div className="mt-2 flex items-center justify-between gap-3">
                        <div className="font-medium text-[var(--text-strong)]">
                          {titleize(latestAction.execution_status)} · {titleize(latestAction.action_mode)}
                        </div>
                        <StatusBadge value={latestAction.execution_status} />
                      </div>
                      <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                        {latestAction.result_summary ?? latestAction.notes ?? "No execution summary captured yet."}
                      </p>
                    </>
                  ) : (
                    <InlineEmptyState
                      title="No execution history yet"
                      description="Approve or run a remediation to start building execution history."
                    />
                  )}
                </div>

                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Latest verification</div>
                  {latestVerification ? (
                    <>
                      <div className="mt-2 flex items-center justify-between gap-3">
                        <div className="font-medium text-[var(--text-strong)]">
                          {titleize(latestVerification.verification_status)} · {latestVerification.verified_by}
                        </div>
                        <StatusBadge value={latestVerification.verification_status} />
                      </div>
                      <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                        {latestVerification.custom_resolution || latestVerification.notes || "No verification note captured yet."}
                      </p>
                    </>
                  ) : (
                    <InlineEmptyState
                      title="Verification has not been recorded"
                      description="Use the command center decision panel when the workflow reaches verification."
                    />
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">History and knowledge</div>
          <CardTitle className="mt-1">Audit, execution, and reusable outcomes</CardTitle>
          <CardDescription>Kept collapsed by default so the operator can focus on the decision first and expand history when needed.</CardDescription>
        </CardHeader>
        <CardContent>
          <details className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
            <summary className="cursor-pointer text-sm font-semibold text-[var(--text-strong)]">Expand detailed history</summary>
            <div className="mt-4 grid gap-4 xl:grid-cols-3">
              <ListCard
                title="Execution history"
                items={buildActionItems(data.actions)}
                emptyTitle="No actions yet"
                emptyDescription="Approve or run a remediation to start building execution history."
              />
              <ListCard
                title="Verification records"
                items={buildVerificationItems(data.verifications)}
                emptyTitle="No verifications yet"
                emptyDescription="Verification appears only after an operator records an outcome."
              />
              <ListCard
                title="Verified knowledge"
                items={buildResolutionItems(data.resolution_extracts)}
                emptyTitle="No verified knowledge yet"
                emptyDescription="Only verified resolutions should flow into reusable incident knowledge."
              />
            </div>
          </details>
        </CardContent>
      </Card>

      <div className="text-sm text-[var(--text-muted)]">
        <Link href="/incidents" className="text-[var(--accent)]">
          Back to incident queue
        </Link>
      </div>
    </div>
  );
}

function WorkflowStageDock({
  flowGuide,
  status,
}: {
  flowGuide: FlowGuide;
  status: WorkflowState;
}) {
  return (
    <Card className="border-[var(--border-subtle)] bg-[var(--surface-raised)] shadow-lg backdrop-blur supports-[backdrop-filter]:bg-[var(--surface-raised)]">
      <CardContent className="space-y-4 p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <div className={cn("rounded-full border px-3 py-1 text-xs font-medium", TONE_BADGE_CLASSES[flowGuide.tone])}>
                {flowGuide.badge}
              </div>
              <StatusBadge value={status} />
            </div>
            <div className="mt-3 text-base font-semibold text-[var(--text-strong)]">{flowGuide.title}</div>
            <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">{flowGuide.subtext}</p>
          </div>
          <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] px-4 py-3 text-sm leading-6 text-[var(--text-secondary)] xl:max-w-md">
            The command center below is the only active decision surface. Everything else is supporting context.
          </div>
        </div>

        <div className="flex gap-2 overflow-x-auto pb-1">
          {flowGuide.steps.map((step) => (
            <div
              key={step.number}
              className={cn(
                "min-w-max rounded-full border px-3 py-1.5 text-xs font-medium whitespace-nowrap",
                stepChipClasses(step.status),
              )}
            >
              {step.number}. {step.title}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function StepReferenceCard({ steps, planeState }: { steps: GuideStep[]; planeState: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Workflow steps</CardTitle>
        <CardDescription>One active step, previous steps completed, and the rest intentionally locked.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {steps.map((step) => {
          const styles = stepClasses(step.status);
          return (
            <div key={step.number} className="flex gap-3">
              <div className={styles.dot}>{step.status === "done" ? "✓" : step.number}</div>
              <div className={styles.card}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium text-[var(--text-strong)]">{step.title}</div>
                    <div className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">{step.description}</div>
                  </div>
                  {step.status === "current" ? (
                    <span className="rounded-full bg-sky-500 px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-950">
                      Now
                    </span>
                  ) : null}
                  {step.status === "attention" ? (
                    <span className="rounded-full bg-amber-500 px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-950">
                      Review
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })}

        <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm leading-6 text-[var(--text-secondary)]">
          Plane mirrors the workflow for collaboration. Current mirrored state: <span className="font-medium text-[var(--text-strong)]">{planeState}</span>.
        </div>
      </CardContent>
    </Card>
  );
}

function AiGeneratedBadge({ generation }: { generation: RcaGenerationInfo }) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium",
        generation.llmUsed
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
          : generation.llmConfigured
            ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
            : "border-[var(--border-subtle)] bg-[var(--surface-subtle)] text-[var(--text-secondary)]",
      )}
    >
      {generation.llmUsed ? <Bot className="h-3.5 w-3.5" aria-hidden="true" /> : <Info className="h-3.5 w-3.5" aria-hidden="true" />}
      <span>{generation.provenanceLabel}</span>
    </div>
  );
}

function AiContentPill({ generation }: { generation: RcaGenerationInfo }) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.12em]",
        generation.llmUsed
          ? "border-emerald-500/25 bg-emerald-500/8 text-emerald-200/90"
          : generation.llmConfigured
            ? "border-amber-500/25 bg-amber-500/8 text-amber-200/90"
            : "border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--text-secondary)]",
      )}
    >
      {generation.llmUsed ? <Sparkles className="h-3 w-3" aria-hidden="true" /> : <Info className="h-3 w-3" aria-hidden="true" />}
      <span>{generation.llmUsed ? "AI generated" : "Fallback"}</span>
    </div>
  );
}

function SummaryItem({
  label,
  value,
  meta,
}: {
  label: string;
  value: React.ReactNode;
  meta?: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">{label}</div>
        {meta}
      </div>
      <div className="mt-2 text-sm text-[var(--text-strong)]">{value}</div>
    </div>
  );
}

function InlineEmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
      <div className="font-medium text-[var(--text-strong)]">{title}</div>
      <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{description}</p>
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
            <div key={`${item.title}-${item.description}`} className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
              <div className="font-medium text-[var(--text-strong)]">{item.title}</div>
              <div className="mt-1 text-sm text-[var(--text-secondary)]">{item.description}</div>
            </div>
          ))
        ) : (
          <InlineEmptyState title={emptyTitle} description={emptyDescription} />
        )}
      </CardContent>
    </Card>
  );
}

function deriveFlowGuide({
  state,
  selectedRemediation,
  hasRca,
  hasRemediations,
  hasTicket,
}: {
  state: WorkflowState;
  selectedRemediation?: RemediationRecord;
  hasRca: boolean;
  hasRemediations: boolean;
  hasTicket: boolean;
  hasExecutionHistory: boolean;
  hasVerification: boolean;
}): FlowGuide {
  const steps = GUIDE_STEP_TEMPLATES.map((step, index) => ({
    number: index + 1,
    title: step.title,
    description: step.description,
    status: STEP_STATUS_MAP[state][index],
  }));

  switch (state) {
    case "NEW":
      return {
        tone: "info",
        badge: "Step 1 of 5",
        title: "Generate RCA first",
        description:
          "This incident is still new. RCA is generated automatically for new events, but remediation, ticketing, approval, and verification remain blocked until the first RCA is attached.",
        subtext: "Use Generate RCA to retry or force the first RCA version for this workflow revision if auto-generation has not landed yet.",
        helpers: [
          {
            title: "Why now",
            text: "RCA is the first gate. The system should not rank fixes or ask for approval without a grounded explanation.",
          },
          {
            title: "Blocked until RCA",
            text: "Remediation ranking, execution, and verification stay locked until RCA exists.",
          },
          {
            title: "Expected output",
            text: "Root cause summary, category, confidence, and evidence references tied to the workflow revision.",
          },
        ],
        ticketHint:
          "Create a ticket only after RCA is generated or if escalation is required. Plane is used for collaboration, not decision-making.",
        primary: { label: "Generate RCA", action: "generateRca" },
        secondary: { label: "Open evidence", action: "openEvidence" },
        steps,
      };
    case "RCA_GENERATED":
      return {
        tone: "info",
        badge: "Step 2 of 5",
        title: "Generate remediations from the RCA",
        description:
          "RCA is now available. The next step is to map that explanation into ranked manual and automated remediation options.",
        subtext: "Create ranked remediations before approval or execution is allowed.",
        helpers: [
          {
            title: "What changed",
            text: "The incident now has a grounded RCA and can move into remediation planning.",
          },
          {
            title: "What stays blocked",
            text: "Approval, execution, and verification remain locked until remediations are generated.",
          },
          {
            title: "Expected output",
            text: "Ranked remediation options, mapped playbooks where available, and revision-safe approval scope.",
          },
        ],
        ticketHint:
          "RCA exists now, so you can create or sync a Plane issue with useful context for collaboration if needed.",
        primary: { label: "Generate remediations", action: "generateRemediations", disabled: !hasRca },
        secondary: { label: "Review RCA", action: "reviewRca" },
        steps,
      };
    case "REMEDIATION_SUGGESTED":
    case "AWAITING_APPROVAL":
      return {
        tone: "info",
        badge: "Step 3 of 5",
        title: "Choose and approve one remediation",
        description:
          "The system has ranked remediation options. Review the trade-offs, select the safest action, and approve it for this exact revision.",
        subtext: "Approval must match the selected remediation and the current workflow revision.",
        helpers: [
          {
            title: "What is ready",
            text: "RCA exists and the platform has mapped ranked remediation options to it.",
          },
          {
            title: "Approval rule",
            text: "Only one exact remediation should be approved at a time, and the approval must match the current revision.",
          },
          {
            title: "Expected output",
            text: "One approved action that the platform can execute or record safely.",
          },
        ],
        ticketHint: hasTicket
          ? "A collaboration ticket already exists. Keep it synced while the platform remains source of truth."
          : "You can create or sync a Plane issue now if you need collaboration or escalation context.",
        primary: { label: "Review remediations", action: "focusRemediation", disabled: !hasRemediations },
        secondary: { label: hasTicket ? "Open ticket workflow" : "Create Plane ticket", action: "focusTicket" },
        steps,
      };
    case "APPROVED":
      return {
        tone: "info",
        badge: "Step 4 of 5",
        title: "Run the approved fix",
        description:
          "An exact remediation has been approved. The operator can now run the mapped Ansible job or record the approved manual action.",
        subtext: "Run only the approved remediation that belongs to the current revision.",
        helpers: [
          {
            title: "What is ready",
            text: "Approval exists, the remediation is selected, and execution scope is clear.",
          },
          {
            title: "Execution safety",
            text: "Only the approved remediation should run. A revision change invalidates the approval.",
          },
          {
            title: "Expected output",
            text: "Execution logs, result summary, and a clean handoff into verification.",
          },
        ],
        ticketHint:
          "Plane can mirror the execution outcome, but execution approval still belongs to the platform workflow.",
        primary: { label: "Run approved action", action: "executeSelected", disabled: !selectedRemediation },
        secondary: { label: "Review remediation", action: "focusRemediation" },
        steps,
      };
    case "EXECUTING":
      return {
        tone: "info",
        badge: "Step 4 of 5",
        title: "Execution is in progress",
        description:
          "The approved remediation is running now. Review the execution output and keep the ticket synchronized if collaboration is active.",
        subtext: "Wait for execution output before moving into verification.",
        helpers: [
          {
            title: "What is happening",
            text: "The platform is executing the approved remediation or recording the manual action outcome.",
          },
          {
            title: "What stays blocked",
            text: "Verification should wait until execution finishes and the result summary is visible.",
          },
          {
            title: "Expected output",
            text: "Execution logs, result summary, and an updated incident timeline.",
          },
        ],
        ticketHint:
          "Use Plane as a collaboration mirror only. The platform still decides when the incident can verify or close.",
        primary: { label: "Review execution history", action: "reviewExecution" },
        secondary: { label: "Open ticket workflow", action: "focusTicket" },
        steps,
      };
    case "EXECUTED":
      return {
        tone: "info",
        badge: "Step 5 of 5",
        title: "Verify the outcome",
        description:
          "Execution finished. The operator must confirm whether the incident is truly resolved before the outcome becomes reusable knowledge.",
        subtext: "Record verification before closing the incident or learning from the result.",
        helpers: [
          {
            title: "What is ready",
            text: "Execution history and result output are available for review.",
          },
          {
            title: "What to confirm",
            text: "Confirm service recovery, record the actual fix, and state whether the RCA was correct.",
          },
          {
            title: "Expected output",
            text: "A verified resolution or a clean loop back into RCA or remediation if the fix did not hold.",
          },
        ],
        ticketHint:
          "If a ticket exists, add the verified outcome so collaborators can see what actually fixed the incident.",
        primary: { label: "Record verification", action: "focusVerification" },
        secondary: { label: "Review execution history", action: "reviewExecution" },
        steps,
      };
    case "VERIFIED":
      return {
        tone: "success",
        badge: "Step 5 of 5",
        title: "Publish verified knowledge and close the incident",
        description:
          "The incident is resolved. Capture the verified outcome as reusable knowledge and close the workflow cleanly.",
        subtext: "Only verified outcomes should influence future RCA confidence and remediation ranking.",
        helpers: [
          {
            title: "What is complete",
            text: "RCA, remediation, approval, execution, and verification all completed successfully.",
          },
          {
            title: "Why it matters",
            text: "This verified outcome becomes high-value knowledge for future retrieval and ranking.",
          },
          {
            title: "Expected output",
            text: "Closed incident, verified resolution artifact, and stronger future retrieval signals.",
          },
        ],
        ticketHint: "Keep the ticket synchronized so collaborators see the final verified outcome before closure.",
        primary: { label: "Close incident", action: "closeIncident" },
        secondary: { label: "Review knowledge", action: "focusKnowledge" },
        steps,
      };
    case "CLOSED":
      return {
        tone: "success",
        badge: "Workflow complete",
        title: "Incident closed",
        description:
          "The workflow is complete. Review the captured knowledge, execution trail, and ticket history when you need to explain what happened later.",
        subtext: "Verified outcomes remain available for RCA and remediation retrieval.",
        helpers: [
          {
            title: "What remains useful",
            text: "Resolution extracts, RCA history, and ticket updates remain attached to the incident record.",
          },
          {
            title: "What is safe now",
            text: "No further execution should happen from a closed incident without reopening the workflow.",
          },
          {
            title: "Expected output",
            text: "A clean historical record that future incidents can reuse safely.",
          },
        ],
        ticketHint: "Keep Plane synchronized for audit and collaboration, but the incident is now complete locally.",
        primary: { label: "Review knowledge", action: "focusKnowledge" },
        secondary: { label: "Review timeline", action: "focusTimeline" },
        steps,
      };
    case "RCA_REJECTED":
      return {
        tone: "warning",
        badge: "RCA needs revision",
        title: "Regenerate RCA from the latest evidence",
        description:
          "The current RCA was rejected or is no longer trusted. Generate a new RCA before choosing another remediation.",
        subtext: "Review the evidence again, then create a fresh RCA for the current incident state.",
        helpers: [
          {
            title: "Why it stopped",
            text: "An operator rejected the RCA, so downstream remediation decisions should pause.",
          },
          {
            title: "What stays blocked",
            text: "Approval and execution should not continue until a replacement RCA exists.",
          },
          {
            title: "Expected output",
            text: "A new RCA version tied to the current workflow revision and evidence set.",
          },
        ],
        ticketHint: "Use Plane to coordinate RCA review if needed, but replace the RCA in the platform before executing anything.",
        primary: { label: "Generate RCA", action: "generateRca" },
        secondary: { label: "Open evidence", action: "openEvidence" },
        steps,
      };
    case "EXECUTION_FAILED":
      return {
        tone: "warning",
        badge: "Execution failed",
        title: "Review the failed run and choose the next remediation",
        description:
          "The approved action did not complete successfully. Review the output, then choose whether to adjust the remediation or escalate.",
        subtext: "Execution failures should loop back to remediation rather than skipping straight to closure.",
        helpers: [
          {
            title: "What happened",
            text: "The chosen action failed or was rejected during execution.",
          },
          {
            title: "What to do next",
            text: "Review the execution output, then select a safer remediation or open escalation in Plane.",
          },
          {
            title: "Expected output",
            text: "A revised remediation path or a deliberate escalation rather than a silent failure.",
          },
        ],
        ticketHint:
          "Execution failure is a good time to sync or update Plane so collaborators see the failed path and next steps.",
        primary: { label: "Review remediations", action: "focusRemediation" },
        secondary: { label: "Review execution history", action: "reviewExecution" },
        steps,
      };
    case "VERIFICATION_FAILED":
      return {
        tone: "warning",
        badge: "Verification failed",
        title: "Verification did not confirm recovery",
        description:
          "The incident did not pass verification. Use the recorded outcome to decide whether to regenerate remediation or revisit the RCA.",
        subtext: "Failed verification should feed back into the workflow rather than being closed out.",
        helpers: [
          {
            title: "What happened",
            text: "The action ran, but the operator did not verify the incident as resolved.",
          },
          {
            title: "What to do next",
            text: "Use the execution and verification notes to choose between new remediations or new RCA.",
          },
          {
            title: "Expected output",
            text: "A corrected remediation path and better final resolution notes.",
          },
        ],
        ticketHint:
          "Update Plane if collaborators need visibility into why the first fix did not fully resolve the incident.",
        primary: { label: "Review remediations", action: "focusRemediation" },
        secondary: { label: "Review execution history", action: "reviewExecution" },
        steps,
      };
    case "FALSE_POSITIVE":
      return {
        tone: "success",
        badge: "Workflow complete",
        title: "Incident marked as false positive",
        description:
          "The operator confirmed this incident should not continue through remediation or closure as a real fault.",
        subtext: "Keep the evidence and timeline for audit, but no remediation or verification loop is needed.",
        helpers: [
          {
            title: "What is complete",
            text: "The workflow ended safely without executing remediation for a false positive incident.",
          },
          {
            title: "Why it matters",
            text: "False positives should still be auditable so model and policy tuning can improve over time.",
          },
          {
            title: "Expected output",
            text: "A closed false-positive record with enough detail for later model review.",
          },
        ],
        ticketHint: hasTicket
          ? "If a ticket exists, keep it synchronized so collaborators know this incident was closed as a false positive."
          : "No ticket is required unless collaborators need visibility into the false positive decision.",
        primary: { label: "Review timeline", action: "focusTimeline" },
        secondary: { label: "Open evidence", action: "openEvidence" },
        steps,
      };
    case "ESCALATED":
      return {
        tone: "warning",
        badge: "Coordination required",
        title: "Coordinate through Plane while the platform keeps state",
        description:
          "The incident needs human coordination or external ownership. Plane can carry collaboration, but the platform still owns execution and verification state.",
        subtext: "Keep the ticket synchronized and use the incident record as the source of truth for workflow state.",
        helpers: [
          {
            title: "What is ready",
            text: "The incident record, RCA, and remediation context can all be mirrored into Plane for coordination.",
          },
          {
            title: "What stays true",
            text: "Plane should not directly execute the remediation or move the incident through verification by itself.",
          },
          {
            title: "Expected output",
            text: "Clear collaboration, explicit ownership, and a synchronized ticket without losing platform control.",
          },
        ],
        ticketHint:
          "Escalation is active. Keep Plane synchronized with the RCA, remediation context, and the latest operator notes.",
        primary: { label: "Open ticket workflow", action: "focusTicket" },
        secondary: { label: "Review RCA", action: "reviewRca" },
        steps,
      };
  }
}

function stepClasses(status: StepStatus) {
  if (status === "current") {
    return {
      dot: "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-sky-500 text-sm font-bold text-slate-950",
      card: "flex-1 rounded-2xl border border-sky-400/25 bg-sky-500/8 p-4",
    };
  }
  if (status === "done") {
    return {
      dot: "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-sm font-bold text-slate-950",
      card: "flex-1 rounded-2xl border border-emerald-400/20 bg-emerald-500/8 p-4",
    };
  }
  if (status === "attention") {
    return {
      dot: "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-500 text-sm font-bold text-slate-950",
      card: "flex-1 rounded-2xl border border-amber-400/20 bg-amber-500/8 p-4",
    };
  }
  return {
    dot: "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--border-subtle)] bg-[var(--surface-subtle)] text-sm font-bold text-[var(--text-secondary)]",
    card: "flex-1 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4",
  };
}

function stepChipClasses(status: StepStatus) {
  if (status === "current") {
    return "border-sky-400/25 bg-sky-500/10 text-sky-100";
  }
  if (status === "done") {
    return "border-emerald-400/25 bg-emerald-500/10 text-emerald-100";
  }
  if (status === "attention") {
    return "border-amber-400/25 bg-amber-500/10 text-amber-100";
  }
  return "border-[var(--border-subtle)] bg-[var(--surface-subtle)] text-[var(--text-secondary)]";
}

function buildObservedSignals(incident: IncidentRecord): string[] {
  const features = (incident.feature_snapshot ?? {}) as Record<string, unknown>;
  const signals: string[] = [];
  const scenarioName = String(features.scenario_name ?? "").trim();
  if (scenarioName) {
    signals.push(`Scenario: ${scenarioName}`);
  }
  const contributingConditions = asStringList(features.contributing_conditions);
  if (contributingConditions.length) {
    signals.push(`Contributing conditions: ${contributingConditions.join(", ")}`);
  }

  const numericFragments = [
    featureFragment("5xx ratio", features.error_5xx_ratio),
    featureFragment("4xx ratio", features.error_4xx_ratio),
    featureFragment("Latency p95", features.latency_p95),
    featureFragment("Retransmissions", features.retransmission_count),
  ].filter(Boolean);
  if (numericFragments.length) {
    signals.push(numericFragments.join(" · "));
  }

  const targetNode = [features.target_endpoint, features.node_id, features.active_node]
    .map((value) => String(value ?? "").trim())
    .find(Boolean);
  if (targetNode) {
    signals.push(`Target node: ${targetNode}`);
  }
  if (incident.feature_window_id) {
    signals.push(`Feature window: ${incident.feature_window_id}`);
  }
  if (!signals.length) {
    signals.push(`Model ${incident.model_version} raised ${incident.anomaly_type} with score ${formatRelativeNumber(incident.anomaly_score)}.`);
  }
  return signals;
}

function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item ?? "").trim()).filter(Boolean);
  }
  const text = String(value ?? "").trim();
  return text ? [text] : [];
}

function asStringValue(value: unknown): string {
  return String(value ?? "").trim();
}

function asBooleanValue(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function asRcaPayload(source?: RcaRecord | RcaPayload | null): RcaPayload {
  return source && "payload" in source && source.payload && typeof source.payload === "object"
    ? (source.payload as RcaPayload)
    : (((source as RcaPayload | null | undefined) ?? {}) as RcaPayload);
}

function buildRcaGenerationInfo(source?: RcaRecord | RcaPayload | null): RcaGenerationInfo {
  const payload = asRcaPayload(source);
  const generationMode =
    asStringValue(payload.generation_mode) ||
    (source && "prompt_version" in source ? asStringValue(source.prompt_version) : "");
  const llmUsed = asBooleanValue(payload.llm_used) ?? generationMode === "llm-rag";
  const llmModel = asStringValue(payload.llm_model);
  const llmConfiguredFlag = asBooleanValue(payload.llm_configured);
  const llmConfigured = llmConfiguredFlag ?? (llmUsed || Boolean(llmModel));
  const sourceLabel =
    asStringValue(payload.generation_source_label) ||
    (generationMode === "llm-rag"
      ? "LLM + RAG"
      : generationMode === "local-rag"
        ? "Local RAG fallback"
        : "Generation source unavailable");
  const model = llmModel || "Not recorded";
  const runtime = asStringValue(payload.llm_runtime) || (llmUsed ? "Not recorded" : llmConfigured ? "Configured" : "Not configured");
  const retrievedDocuments = Array.isArray(payload.retrieved_documents)
    ? payload.retrieved_documents.length
    : source && "retrieval_refs" in source && Array.isArray(source.retrieval_refs)
      ? source.retrieval_refs.length
      : 0;

  let provenanceLabel = "Local summary";
  let summary = "This RCA does not include generation metadata yet.";
  if (llmUsed) {
    provenanceLabel = "AI generated";
    summary =
      `Generated by the AI reasoning service` +
      `${model !== "Not recorded" ? ` with ${model}` : ""}` +
      `${runtime !== "Not recorded" ? ` via ${runtime}` : ""}` +
      `${retrievedDocuments ? ` and grounded with ${retrievedDocuments} retrieved evidence references.` : "."}`;
  } else if (llmConfigured) {
    provenanceLabel = "Fallback summary";
    summary = "This RCA used the local fallback path for this run even though an AI runtime was configured.";
  } else {
    summary = "This RCA used the local fallback path because no AI runtime was configured for the service.";
  }

  return {
    sourceLabel,
    summary,
    model,
    runtime,
    retrievedDocumentCount: retrievedDocuments,
    llmUsed,
    llmConfigured,
    provenanceLabel,
  };
}

function buildRcaAnalysis(source: RcaRecord | undefined, incident?: IncidentRecord): string {
  const payload = asRcaPayload(source);
  const explanation = asStringValue(source?.explanation) || asStringValue(payload.explanation);
  if (explanation) {
    return explanation;
  }

  const rootCause = asStringValue(source?.root_cause) || asStringValue(payload.root_cause) || asStringValue(incident?.narrative);
  const retrievedDocs = Array.isArray(payload.retrieved_documents) ? payload.retrieved_documents : [];
  const docRefs = retrievedDocs
    .map((item) => {
      if (!item || typeof item !== "object") {
        return "";
      }
      return asStringValue(item.title) || asStringValue(item.reference);
    })
    .filter(Boolean)
    .slice(0, 2);

  let analysis = rootCause || "Grounded RCA details are not available yet.";
  if (analysis && !/[.!?]$/.test(analysis)) {
    analysis = `${analysis}.`;
  }
  const anomalyType = asStringValue(source?.category) || asStringValue(incident?.anomaly_type) || "current";
  analysis += ` This aligns with the ${titleize(anomalyType)} incident pattern.`;
  if (docRefs.length) {
    analysis += ` Supporting context was retrieved from ${docRefs.join(" and ")}.`;
  }
  return analysis;
}

function buildRcaRecommendation(source: RcaRecord | undefined, incidentRecommendation?: string | null): string {
  const payload = asRcaPayload(source);
  return (
    asStringValue(payload.recommendation) ||
    asStringValue(incidentRecommendation) ||
    "No recommendation has been recorded yet."
  );
}

function featureFragment(label: string, value: unknown) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "";
  }
  return `${label}: ${formatRelativeNumber(numeric)}`;
}

function remediationMode(remediation?: RemediationRecord) {
  if (!remediation) {
    return "Pending";
  }
  if (remediation.playbook_ref) {
    return "Ansible playbook";
  }
  if (remediation.suggestion_type.includes("escalate")) {
    return "Escalation";
  }
  return "Manual action";
}

function buildRemediationPreview(remediation: RemediationRecord) {
  if (remediation.playbook_ref) {
    return `${remediation.description} This path maps to ${remediation.playbook_ref} and should be tied to workflow revision ${remediation.based_on_revision}.`;
  }
  if (remediation.suggestion_type.includes("escalate")) {
    return `${remediation.description} This option is for coordination and ticket workflow rather than direct automation.`;
  }
  return `${remediation.description} This path records a manual operator action instead of invoking automation.`;
}

function buildHumanEvidenceSummary(incident: IncidentRecord, observedSignals: string[]) {
  const topEvidence = incident.evidence_sources?.[0];
  const strongestSignal = incident.explainability?.[0];
  return [
    {
      title: "Business impact",
      detail: incident.impact || incident.subtitle || "Impact is still being assessed from the current incident evidence.",
    },
    {
      title: "What changed",
      detail: observedSignals[0] || "The platform has not published the leading signal yet.",
    },
    {
      title: "Top contributing factor",
      detail: topEvidence
        ? `${topEvidence.title}. ${topEvidence.detail}.`
        : strongestSignal
          ? `${strongestSignal.label} carried the strongest explainability weight for this incident.`
          : "No dominant contributing factor has been summarized yet.",
    },
    {
      title: "Safe response posture",
      detail: incident.blast_radius
        ? `Keep the first response scoped to ${incident.blast_radius}.`
        : "Keep the first response targeted and easy to rollback until verification confirms recovery.",
    },
  ];
}

function buildSimulationPreview(
  incident: IncidentRecord,
  remediation: RemediationRecord | undefined,
  latestRca: RcaRecord | undefined,
): SimulationPreview {
  const baseConfidence = Number(latestRca?.confidence ?? incident.anomaly_score ?? 0.5);
  if (!remediation) {
    return {
      effectiveness: "Pending selection",
      latencyImpact: "Unknown",
      confidence: `${Math.round(Math.max(45, Math.min(baseConfidence * 100, 90)))}%`,
      summary: "Select a remediation to preview estimated impact before execution.",
    };
  }

  const rankScore = Number(remediation.rank_score ?? remediation.confidence ?? 0.6);
  const effectivenessPercent = Math.round(Math.max(35, Math.min(92, 42 + rankScore * 26 + baseConfidence * 24)));
  const riskLevel = asStringValue(remediation.risk_level).toLowerCase();
  const latencyImpact =
    riskLevel === "high" ? "+9%" : riskLevel === "medium" ? "+5%" : riskLevel === "critical" ? "+12%" : "+2%";
  const previewConfidence = Math.round(
    Math.max(45, Math.min((((Number(remediation.confidence ?? 0.6) + baseConfidence) / 2) * 100), 95)),
  );

  return {
    effectiveness: `${effectivenessPercent}%`,
    latencyImpact,
    confidence: `${previewConfidence}%`,
    summary: remediation.expected_outcome
      ? `${remediation.expected_outcome} This preview keeps the first move scoped to workflow revision ${remediation.based_on_revision}.`
      : `This preview assumes ${remediation.title.toLowerCase()} is applied first and focuses on reducing customer-facing pressure before broader escalation.`,
  };
}

function buildActionItems(actions: IncidentActionRecord[]) {
  return actions.map((action) => ({
    title: `${titleize(action.execution_status)} · ${titleize(action.action_mode)}`,
    description: `${action.result_summary ?? action.notes ?? "No action summary"} · ${formatTime(action.finished_at ?? action.started_at ?? null)}`,
  }));
}

function buildVerificationItems(records: VerificationRecord[]) {
  return records.map((record) => ({
    title: `${titleize(record.verification_status)} · ${record.verified_by}`,
    description: `${record.custom_resolution ?? record.notes ?? "No verification note"} · ${formatTime(record.created_at)}`,
  }));
}

function buildResolutionItems(extracts: ResolutionExtract[]) {
  return extracts.map((extract) => ({
    title: `${titleize(extract.verification_quality)} quality`,
    description: `${extract.summary} · ${formatTime(extract.created_at)}`,
  }));
}

function truncateText(value: string | null | undefined, limit: number) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit - 1)}...`;
}

function knowledgeArticleHref(incidentId: string, reference: string) {
  const encodedReference = reference
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `/incidents/${encodeURIComponent(incidentId)}/knowledge/${encodedReference}`;
}
