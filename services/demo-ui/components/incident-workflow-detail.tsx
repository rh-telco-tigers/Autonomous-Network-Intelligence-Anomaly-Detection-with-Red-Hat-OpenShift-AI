"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Bot, Info, Sparkles } from "lucide-react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { useApiToken } from "@/components/providers/app-providers";
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
  RcaPayload,
  RcaRecord,
  RemediationRecord,
  TicketRecord,
  VerificationRecord,
  WorkflowState,
} from "@/lib/types";
import { cn, formatRelativeNumber, formatTime, titleize } from "@/lib/utils";

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

type IncidentViewStep = GuideStep & {
  action: GuideActionId;
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

type RemediationDecisionState = "available" | "approved" | "executing" | "executed" | "failed" | "rejected";

type RemediationActivity = {
  attemptCount: number;
  canRetry: boolean;
  decisionLocked: boolean;
  decisionState: RemediationDecisionState;
  latestAction?: IncidentActionRecord;
  retryLabel?: string;
  summary: string;
};

type RemediationActionResponse = {
  workflow: IncidentWorkflow;
  action?: IncidentActionRecord;
  remediation?: RemediationRecord;
};

const GUIDE_STEP_TEMPLATES = [
  {
    title: "Generate RCA",
    description: "Review the evidence-backed analysis before choosing a fix.",
  },
  {
    title: "Generate remediations",
    description: "Turn the analysis into ranked manual and automated fix options.",
  },
  {
    title: "Approve a fix",
    description: "Approval applies only to the selected fix and workflow version.",
  },
  {
    title: "Execute approved fix",
    description: "Run automation or record a manual action result here.",
  },
  {
    title: "Verify and close",
    description: "Confirm the fix worked before closing the incident.",
  },
] as const;

const INCIDENT_VIEW_STEP_TEMPLATES = [
  {
    title: "Detect",
    description: "Incident created and scoped",
    action: "openEvidence",
  },
  {
    title: "Investigate",
    description: "Review evidence and impact",
    action: "openEvidence",
  },
  {
    title: "RCA",
    description: "Generate and review cause",
    action: "reviewRca",
  },
  {
    title: "Remediation",
    description: "Run a fix or escalate",
    action: "focusRemediation",
  },
  {
    title: "Verify & close",
    description: "Confirm recovery and learn",
    action: "focusVerification",
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

const INCIDENT_VIEW_STATUS_MAP: Record<WorkflowState, StepStatus[]> = {
  NEW: ["done", "current", "todo", "todo", "todo"],
  RCA_GENERATED: ["done", "done", "current", "todo", "todo"],
  REMEDIATION_SUGGESTED: ["done", "done", "done", "current", "todo"],
  AWAITING_APPROVAL: ["done", "done", "done", "current", "todo"],
  APPROVED: ["done", "done", "done", "current", "todo"],
  EXECUTING: ["done", "done", "done", "current", "todo"],
  EXECUTED: ["done", "done", "done", "done", "current"],
  VERIFIED: ["done", "done", "done", "done", "done"],
  CLOSED: ["done", "done", "done", "done", "done"],
  RCA_REJECTED: ["done", "attention", "attention", "todo", "todo"],
  EXECUTION_FAILED: ["done", "done", "done", "attention", "todo"],
  VERIFICATION_FAILED: ["done", "done", "done", "done", "attention"],
  FALSE_POSITIVE: ["done", "done", "done", "done", "done"],
  ESCALATED: ["done", "done", "done", "attention", "todo"],
};

const TONE_CARD_CLASSES = {
  info: "border-sky-400/20 bg-sky-500/5 ring-1 ring-sky-400/10",
  warning: "border-amber-400/20 bg-amber-500/5 ring-1 ring-amber-400/10",
  success: "border-emerald-400/20 bg-emerald-500/5 ring-1 ring-emerald-400/10",
} as const;

const TONE_BADGE_CLASSES = {
  info: "border-sky-400/30 bg-sky-500/10 text-[var(--text-strong)]",
  warning: "border-amber-400/30 bg-amber-500/10 text-[var(--text-strong)]",
  success: "border-emerald-400/30 bg-emerald-500/10 text-[var(--text-strong)]",
} as const;

export function IncidentWorkflowDetail() {
  const params = useParams<{ incidentId: string }>();
  const incidentId = params.incidentId;
  const { token } = useApiToken();
  const queryClient = useQueryClient();
  const [notice, setNotice] = React.useState<Notice>(null);
  const [currentPageUrl, setCurrentPageUrl] = React.useState("");
  const [actionActor, setActionActor] = React.useState("demo-ui");
  const [focusedRemediationId, setFocusedRemediationId] = React.useState<number | null>(null);
  const [remediationNotes, setRemediationNotes] = React.useState<Record<number, string>>({});

  const rcaRef = React.useRef<HTMLDivElement>(null);
  const remediationRef = React.useRef<HTMLDivElement>(null);
  const verificationRef = React.useRef<HTMLDivElement>(null);
  const ticketRef = React.useRef<HTMLDivElement>(null);
  const timelineRef = React.useRef<HTMLDivElement>(null);
  const knowledgeRef = React.useRef<HTMLDivElement>(null);
  const executionRef = React.useRef<HTMLDivElement>(null);

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
      queryClient.invalidateQueries({ queryKey: ["incidents"] }),
      queryClient.invalidateQueries({ queryKey: ["console-state"] }),
    ]);
  }, [incidentId, queryClient, token]);

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
      return request<RemediationActionResponse>(path, token, {
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
        body: JSON.stringify({ target_state: "ESCALATED", notes, source_url: currentPageUrl }),
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

  const remediationRevision = React.useMemo(() => {
    if (!data?.remediations.length) {
      return null;
    }
    return data.current_remediations[0]?.based_on_revision ?? data.remediations[0]?.based_on_revision ?? null;
  }, [data]);

  const displayedRemediations = React.useMemo(() => {
    if (!data || remediationRevision == null) {
      return [];
    }
    return data.remediations.filter((item) => item.based_on_revision === remediationRevision);
  }, [data, remediationRevision]);

  const remediationActivityById = React.useMemo(
    () => buildRemediationActivityMap(displayedRemediations, data?.actions ?? []),
    [displayedRemediations, data?.actions],
  );

  const preferredRemediation = React.useMemo(() => {
    if (!displayedRemediations.length) {
      return undefined;
    }
    return [...displayedRemediations].sort((left, right) => {
      const priorityDelta =
        remediationDecisionPriority(remediationActivityById[left.id]) - remediationDecisionPriority(remediationActivityById[right.id]);
      if (priorityDelta !== 0) {
        return priorityDelta;
      }
      return left.suggestion_rank - right.suggestion_rank;
    })[0];
  }, [displayedRemediations, remediationActivityById]);

  React.useEffect(() => {
    if (!data) {
      return;
    }
    verificationForm.reset({
      action_id: String(data.actions[0]?.id ?? ""),
      verified_by: verificationForm.getValues("verified_by") || "demo-ui",
      verification_status: "verified",
      notes: "",
      custom_resolution: "",
      metric_based: true,
      close_after_verify: true,
    });
  }, [data, verificationForm]);

  React.useEffect(() => {
    if (!displayedRemediations.length) {
      setFocusedRemediationId(null);
      return;
    }
    setFocusedRemediationId((current) => {
      if (current && displayedRemediations.some((item) => item.id === current)) {
        return current;
      }
      return preferredRemediation?.id ?? displayedRemediations[0]?.id ?? null;
    });
  }, [displayedRemediations, preferredRemediation]);

  const selectedRemediation = React.useMemo(() => {
    if (!displayedRemediations.length) {
      return undefined;
    }
    if (focusedRemediationId != null) {
      return displayedRemediations.find((item) => item.id === focusedRemediationId) ?? preferredRemediation;
    }
    return preferredRemediation;
  }, [displayedRemediations, focusedRemediationId, preferredRemediation]);

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

  const updateRemediationNote = React.useCallback((remediationId: number, value: string) => {
    setRemediationNotes((current) => ({ ...current, [remediationId]: value }));
  }, []);

  const clearRemediationNote = React.useCallback((remediationId: number) => {
    setRemediationNotes((current) => {
      if (!(remediationId in current)) {
        return current;
      }
      const next = { ...current };
      delete next[remediationId];
      return next;
    });
  }, []);

  const remediationNote = React.useCallback(
    (remediationId: number) => remediationNotes[remediationId] ?? "",
    [remediationNotes],
  );

  const actorName = actionActor.trim() || "demo-ui";

  const runRemediationAction = React.useCallback(
    async (remediation: RemediationRecord, mode: "approve" | "execute" | "reject") => {
      setFocusedRemediationId(remediation.id);
      try {
        const payload = await actionMutation.mutateAsync({
          remediationId: remediation.id,
          actor: actorName,
          notes: remediationNote(remediation.id),
          mode,
        });
        const executionStatus = payload.action?.execution_status ?? (mode === "reject" ? "rejected" : "approved");
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
                    : payload.action?.result_summary ?? "Remediation execution failed."
                : "Remediation approved.",
        });
        clearRemediationNote(remediation.id);
      } catch (mutationError) {
        setNotice({
          kind: "error",
          message: mutationError instanceof Error ? mutationError.message : "Remediation action failed.",
        });
      }
    },
    [actionMutation, actorName, clearRemediationNote, remediationNote],
  );

  const retryRemediation = React.useCallback(
    async (remediation: RemediationRecord) => {
      await runRemediationAction(remediation, "execute");
    },
    [runRemediationAction],
  );

  const escalateFromRemediation = React.useCallback(
    async (remediation: RemediationRecord) => {
      setFocusedRemediationId(remediation.id);
      try {
        const workflow = await escalateIncidentMutation.mutateAsync(
          remediationNote(remediation.id) || `Operator escalated instead of executing "${remediation.title}".`,
        );
        scrollToSection(ticketRef);
        setNotice({
          kind: "warning",
          message: workflow.current_ticket
            ? "Incident escalated. Plane ticket is attached and ready for coordination."
            : "Incident escalated. Open the ticket workflow to create or sync the Plane ticket.",
        });
        clearRemediationNote(remediation.id);
      } catch (mutationError) {
        setNotice({
          kind: "error",
          message: mutationError instanceof Error ? mutationError.message : "Escalation failed.",
        });
      }
    },
    [clearRemediationNote, escalateIncidentMutation, remediationNote, scrollToSection, ticketRef],
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
                  ? "RCA generated using the built-in fallback process for this run."
                  : "RCA generated using the built-in fallback process because no AI endpoint is configured.",
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
          if (selectedRemediation) {
            await runRemediationAction(selectedRemediation, "execute");
          }
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
          scrollToSection(rcaRef);
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
      executionRef,
      generateRcaMutation,
      generateRemediationsMutation,
      knowledgeRef,
      rcaRef,
      remediationRef,
      runRemediationAction,
      scrollToSection,
      selectedRemediation,
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
  const hasRemediations = displayedRemediations.length > 0;
  const hasTicket = Boolean(currentTicket);
  const hasActionHistory = data.actions.length > 0;
  const hasVerification = data.verifications.length > 0 || ["VERIFIED", "CLOSED", "FALSE_POSITIVE"].includes(incident.status);
  const verificationUnlocked =
    hasActionHistory || ["EXECUTED", "EXECUTING", "VERIFIED", "CLOSED", "VERIFICATION_FAILED", "FALSE_POSITIVE"].includes(incident.status);
  const ticketUnlocked = hasRca || hasTicket || incident.status === "ESCALATED";
  const flowGuide = deriveFlowGuide({
    state: incident.status,
    selectedRemediation,
    hasRca,
    hasRemediations,
    hasTicket,
    hasExecutionHistory: hasActionHistory,
    hasVerification,
  });
  const pending =
    generateRcaMutation.isPending ||
    generateRemediationsMutation.isPending ||
    actionMutation.isPending ||
    verificationMutation.isPending ||
    escalateIncidentMutation.isPending ||
    ticketMutation.isPending ||
    ticketSyncMutation.isPending ||
    closeIncidentMutation.isPending;

  const ticketAutoSyncHint = currentTicket
    ? `Operator notes entered in this workflow will also be posted to the current ${currentTicket.provider.toUpperCase()} ticket.`
    : "Create a Plane ticket here to mirror later operator updates automatically.";
  const currentTicketMetadata = (currentTicket?.metadata ?? {}) as Record<string, unknown>;
  const currentTicketSourceUrl = asStringValue(currentTicketMetadata.source_url);
  const incidentWorkspaceHref = currentPageUrl || currentTicketSourceUrl;
  const primaryRemediation = preferredRemediation;
  const headlineRemediation = primaryRemediation ?? selectedRemediation;
  const headlineRemediationMode = remediationMode(headlineRemediation);
  const commandCenterSummary = headlineRemediation ? buildRemediationPreview(headlineRemediation) : latestRcaRecommendation;
  const decisionRisk = titleize(headlineRemediation?.risk_level ?? (incident.severity === "Critical" ? "medium" : "low"));
  const rcaConfidenceLabel = hasRca ? `${Math.round(Number(latestRca?.confidence ?? 0) * 100)}%` : "Pending";
  const alternativeRemediations = displayedRemediations.filter((item) => item.id !== primaryRemediation?.id);
  const incidentViewSteps = buildIncidentViewSteps(incident.status);
  const currentIncidentStep =
    incidentViewSteps.find((step) => step.status === "current" || step.status === "attention") ??
    incidentViewSteps[incidentViewSteps.length - 1];
  const ticketDraftNote = ticketForm.watch("note");
  const plannedTicketNote = truncateText(
    ticketDraftNote?.trim() || latestRcaAnalysis || flowGuide.subtext || "Ticket updates will appear here once you add a note.",
    220,
  );

  return (
    <div className="space-y-6">
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

      <div className="sticky top-0 z-30 rounded-3xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 shadow-sm backdrop-blur">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0 flex-1 space-y-4">
            <Button asChild variant="outline" size="sm" className="w-fit">
              <Link href="/incidents">Back to incidents</Link>
            </Button>
            <div className="flex items-start gap-4">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[var(--accent)] text-sm font-bold text-slate-950 shadow-sm">
                IMS
              </div>
              <div className="min-w-0">
                <div className="text-[11px] uppercase tracking-[0.32em] text-[var(--text-muted)]">Incident workflow</div>
                <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[var(--text-strong)] sm:text-3xl">
                  {titleize(incident.anomaly_type)}
                </h1>
                <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">{flowGuide.subtext}</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <StatusBadge value={incident.status} />
              <StatusBadge value={incident.severity} />
              <div className={cn("rounded-full border px-3 py-1 text-xs font-medium", TONE_BADGE_CLASSES[flowGuide.tone])}>
                {flowGuide.badge}
              </div>
              <div className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-subtle)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]">
                Analysis confidence {rcaConfidenceLabel}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={() => void refreshWorkflow()} disabled={pending}>
              Refresh
            </Button>
            {currentTicketHref ? (
              <Button asChild>
                <a href={currentTicketHref} target="_blank" rel="noreferrer">
                  Open incident ticket
                </a>
              </Button>
            ) : (
              <Button onClick={() => void handleGuideAction("focusTicket")} disabled={!ticketUnlocked}>
                {ticketUnlocked ? "Open incident ticket" : "Ticket waits for RCA"}
              </Button>
            )}
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)_360px]">
        <aside className="space-y-4 xl:sticky xl:top-24 xl:self-start">
          <Card>
            <CardContent className="space-y-4 p-5">
              <div>
                <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Workflow</div>
                <div className="mt-2 text-lg font-semibold text-[var(--text-strong)]">Resolve incident</div>
                <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                  Stay in one page and move the incident forward without losing ticket context or operator actions.
                </p>
              </div>
              <div className="space-y-2">
                {incidentViewSteps.map((step) => {
                  const styles = stepClasses(step.status);
                  return (
                    <button
                      key={step.number}
                      type="button"
                      onClick={() => void handleGuideAction(step.action)}
                      className={cn("flex w-full items-start gap-3 text-left transition hover:opacity-95", styles.card)}
                    >
                      <div className={styles.dot}>{step.status === "done" ? "✓" : step.number}</div>
                      <div>
                        <div className="text-sm font-semibold text-[var(--text-strong)]">
                          {step.number}. {step.title}
                        </div>
                        <div className="mt-1 text-xs text-[var(--text-secondary)]">{step.description}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </CardContent>
          </Card>

        </aside>

        <section className="space-y-6">
          <Card className={cn(TONE_CARD_CLASSES[flowGuide.tone], "overflow-hidden")}>
            <CardContent className="space-y-6 p-6">
              <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
                <div className="max-w-3xl">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="rounded-full border border-sky-400/25 bg-sky-500/10 px-3 py-1 text-xs font-semibold text-[var(--text-strong)]">
                      Step {currentIncidentStep.number} of {incidentViewSteps.length}
                    </div>
                    <div className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-subtle)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                      {currentIncidentStep.title}
                    </div>
                  </div>
                  <div className="mt-4 text-2xl font-semibold tracking-tight text-[var(--text-strong)]">{flowGuide.title}</div>
                  <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">{commandCenterSummary}</p>
                </div>
                <div className="grid min-w-[260px] grid-cols-2 gap-3">
                  <SummaryItem
                    label="Incident ID"
                    value={<span className="font-mono text-xs [overflow-wrap:anywhere]">{incident.id}</span>}
                  />
                  <SummaryItem
                    label="Affected site"
                    value={
                      asStringValue((incident.feature_snapshot as Record<string, unknown> | null)?.node_role) ||
                      asStringValue((incident.feature_snapshot as Record<string, unknown> | null)?.node_id) ||
                      incident.feature_window_id ||
                      "Primary service path"
                    }
                  />
                  <SummaryItem label="Severity" value={<StatusBadge value={incident.severity} />} />
                  <SummaryItem label="Started" value={formatTime(incident.created_at)} />
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <SummaryItem label="Operational impact" value={incident.impact ?? incident.subtitle ?? "Assessing impact"} />
                <SummaryItem label="Blast radius" value={incident.blast_radius ?? "Not yet mapped"} />
                <SummaryItem label="AI confidence" value={rcaConfidenceLabel} />
                <SummaryItem label="Decision risk" value={decisionRisk} />
              </div>
            </CardContent>
          </Card>

          <div ref={rcaRef}>
            <Card>
              <CardContent className="space-y-6 p-6">
                <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                  <div className="max-w-3xl">
                    <div className="flex flex-wrap items-center gap-2 text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">
                      <Sparkles className="h-3.5 w-3.5 text-[var(--accent)]" aria-hidden="true" />
                      <span>AI investigation summary</span>
                      {hasRca ? <AiGeneratedBadge generation={latestRcaGeneration} /> : null}
                    </div>
                    <div className="mt-2 text-xl font-semibold text-[var(--text-strong)]">
                      {latestRca?.root_cause ?? "RCA has not been generated yet"}
                    </div>
                    <p className="mt-4 text-sm leading-7 text-[var(--text-secondary)]">{latestRcaAnalysis}</p>
                  </div>
                  <div className="w-full max-w-sm rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                    <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Decision recommendation</div>
                    <div className="mt-2 text-sm font-medium text-[var(--text-strong)]">{flowGuide.primary.label}</div>
                    <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{flowGuide.subtext}</p>
                    {hasRca ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        <AiContentPill generation={latestRcaGeneration} />
                        <div className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]">
                          Action type: {headlineRemediationMode}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="grid gap-4 md:grid-cols-4">
                  <SummaryItem label="Confidence" value={rcaConfidenceLabel} />
                  <SummaryItem label="Runtime" value={latestRcaGeneration.runtime} />
                  <SummaryItem label="Model" value={latestRcaGeneration.model} />
                  <SummaryItem label="Evidence docs" value={String(latestRcaGeneration.retrievedDocumentCount)} />
                </div>

                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Evidence references</div>
                  <div className="mt-3 space-y-3">
                    {incident.evidence_sources?.length ? (
                      incident.evidence_sources.map((item) => (
                        <div key={`${item.title}-${item.detail}`} className="rounded-xl bg-[var(--surface-raised)] p-3">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="font-medium text-[var(--text-strong)]">{item.title}</div>
                              {item.reference && item.reference !== item.title ? (
                                <div className="mt-1 break-all font-mono text-xs text-[var(--text-muted)]">{item.reference}</div>
                              ) : null}
                              <div className="mt-1 text-sm text-[var(--text-secondary)]">{item.detail}</div>
                              {item.excerpt ? (
                                <div className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{truncateText(item.excerpt, 220)}</div>
                              ) : null}
                            </div>
                            {item.reference && item.collection ? (
                              <Button asChild variant="secondary" className="shrink-0">
                                <Link href={evidenceDocumentHref(incident.id, item.collection, item.reference)}>Read evidence</Link>
                              </Button>
                            ) : null}
                          </div>
                        </div>
                      ))
                    ) : (
                      <InlineEmptyState
                        title="No evidence references yet"
                        description="Generate or refresh RCA to attach the evidence used in the analysis."
                      />
                    )}
                  </div>
                </div>

                {!hasRca ? (
                  <div className="flex flex-wrap gap-3">
                    <Button onClick={() => void handleGuideAction("generateRca")} disabled={pending}>
                      {generateRcaMutation.isPending ? "Generating..." : "Generate RCA"}
                    </Button>
                    <Button variant="outline" onClick={() => void handleGuideAction("openEvidence")}>
                      Review evidence first
                    </Button>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </div>

          <div ref={remediationRef}>
            <Card>
              <CardContent className="space-y-6 p-6">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Remediation workflow</div>
                    <div className="mt-2 text-xl font-semibold text-[var(--text-strong)]">Choose and approve a fix</div>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">
                      Each remediation keeps its own decision history. Completed one-time actions disable automatically, tried fixes stay visible, and retries are explicit.
                    </p>
                  </div>
                  {displayedRemediations.length ? (
                    <div className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-subtle)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]">
                      {displayedRemediations.length} suggestions in this review
                    </div>
                  ) : null}
                </div>

                {hasRemediations ? (
                  <>
                    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
                      <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                        <Label htmlFor="remediation_actor">Operator</Label>
                        <Input
                          id="remediation_actor"
                          value={actionActor}
                          onChange={(event) => setActionActor(event.target.value)}
                          placeholder="Who is taking the action?"
                          className="mt-2"
                        />
                      </div>
                      <div className="rounded-2xl border border-sky-400/20 bg-sky-500/8 p-4 text-sm leading-6 text-[var(--text-secondary)]">
                        {ticketAutoSyncHint}
                      </div>
                    </div>

                    {primaryRemediation ? (
                      <RemediationActionCard
                        remediation={primaryRemediation}
                        activity={remediationActivityById[primaryRemediation.id]}
                        titlePrefix="Primary action"
                        description={buildRemediationPreview(primaryRemediation)}
                        isPrimary
                        isFocused={selectedRemediation?.id === primaryRemediation.id}
                        actor={actorName}
                        note={remediationNote(primaryRemediation.id)}
                        pending={pending}
                        onNoteChange={updateRemediationNote}
                        onFocus={setFocusedRemediationId}
                        onExecute={(remediation) => void runRemediationAction(remediation, "execute")}
                        onApprove={(remediation) => void runRemediationAction(remediation, "approve")}
                        onReject={(remediation) => void runRemediationAction(remediation, "reject")}
                        onRetry={(remediation) => void retryRemediation(remediation)}
                        onEscalate={(remediation) => void escalateFromRemediation(remediation)}
                      />
                    ) : null}

                    <div className="grid gap-3">
                      {alternativeRemediations.length ? (
                        alternativeRemediations.map((remediation) => (
                          <RemediationActionCard
                            key={remediation.id}
                            remediation={remediation}
                            activity={remediationActivityById[remediation.id]}
                            titlePrefix={`#${remediation.suggestion_rank}`}
                            description={remediation.description}
                            isFocused={selectedRemediation?.id === remediation.id}
                            actor={actorName}
                            note={remediationNote(remediation.id)}
                            pending={pending}
                            onNoteChange={updateRemediationNote}
                            onFocus={setFocusedRemediationId}
                            onExecute={(item) => void runRemediationAction(item, "execute")}
                            onApprove={(item) => void runRemediationAction(item, "approve")}
                            onReject={(item) => void runRemediationAction(item, "reject")}
                            onRetry={(item) => void retryRemediation(item)}
                            onEscalate={(item) => void escalateFromRemediation(item)}
                          />
                        ))
                      ) : (
                        <InlineEmptyState
                          title="No alternative remediations"
                          description="Only one fix is currently recommended for this incident."
                        />
                      )}
                    </div>
                  </>
                ) : hasRca ? (
                  <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-5">
                    <div className="text-sm font-semibold text-[var(--text-strong)]">Generate ranked remediations</div>
                    <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                      RCA is ready. Generate ranked manual and automated options before you approve or execute anything.
                    </p>
                    <div className="mt-4">
                      <Button onClick={() => void handleGuideAction("generateRemediations")} disabled={pending}>
                        {generateRemediationsMutation.isPending ? "Generating..." : "Generate remediations"}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <InlineEmptyState
                    title="No remediations generated yet"
                    description="Generate RCA first, then use this section to review and execute ranked fix options."
                  />
                )}
              </CardContent>
            </Card>
          </div>

          <div ref={verificationRef}>
            <Card>
              <CardContent className="space-y-4 p-6">
                <div>
                  <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Execution and verification</div>
                  <div className="mt-2 text-lg font-semibold text-[var(--text-strong)]">Record the outcome before closing</div>
                  <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                    Confirm whether the action actually resolved the incident before closing the loop.
                  </p>
                </div>

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
                      description="Record the outcome here after execution completes."
                    />
                  )}
                </div>

                {verificationUnlocked ? (
                  <form
                    className="space-y-4 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4"
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
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Record verification</div>
                        <div className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                          Confirm whether the action actually resolved the incident before closing the loop.
                        </div>
                      </div>
                      <Button variant="outline" type="button" size="sm" onClick={() => scrollToSection(ticketRef)}>
                        Update Plane ticket
                      </Button>
                    </div>

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
                        placeholder="Record the actual fix that resolved the incident."
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
                    </div>
                  </form>
                ) : (
                  <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm leading-6 text-[var(--text-secondary)]">
                    Verification opens after the first execution event is recorded.
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          <div ref={knowledgeRef}>
            <Card>
              <CardContent className="space-y-4 p-6">
                <div>
                  <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Advanced technical details</div>
                  <div className="mt-2 text-lg font-semibold text-[var(--text-strong)]">Open the dedicated debug trace only when you need it</div>
                  <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                    Raw request and response packets, model inputs and outputs, LLM prompts and responses, and lifecycle trace data now live on a separate deep-dive page.
                  </p>
                </div>
                <div className="rounded-3xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-6">
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div className="max-w-3xl">
                      <div className="text-sm font-medium text-[var(--text-strong)]">Detailed execution trace</div>
                      <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                        Open the deep dive view for the full timestamped flow across API calls, model inference, RCA generation, ticket sync, and workflow events.
                      </p>
                    </div>
                    <Button asChild className="shrink-0">
                      <Link href={`/incidents/${encodeURIComponent(incident.id)}/debug`}>View Detailed Execution Trace</Link>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </section>

        <aside className="space-y-6 xl:sticky xl:top-24 xl:self-start">
          <div ref={timelineRef}>
            <Card>
              <CardContent className="space-y-6 p-6">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Incident ticket</div>
                    <div className="mt-2 text-lg font-semibold text-[var(--text-strong)]">
                      {currentTicket?.external_key ?? currentTicket?.external_id ?? "Ticket not created"}
                    </div>
                  </div>
                  <StatusBadge value={currentTicket?.sync_state ?? (currentTicket ? "synced" : "pending")} />
                </div>

                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="text-sm font-medium text-[var(--text-strong)]">
                    {currentTicket?.title ?? "This incident has not been synced to a collaboration ticket yet."}
                  </div>
                  <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{flowGuide.ticketHint}</p>
                </div>

                <div className="space-y-4 border-l-2 border-[var(--border-subtle)] pl-5">
                  {incident.timeline?.length ? (
                    incident.timeline.map((entry, index) => (
                      <div key={`${entry.time}-${entry.title}`} className="relative">
                        <span className="absolute -left-[29px] top-1.5 h-3 w-3 rounded-full bg-sky-400 ring-4 ring-[var(--surface-raised)]" />
                        <div className={cn(index > 1 && "opacity-80")}>
                          <div className="text-sm font-semibold text-[var(--text-strong)]">{entry.title}</div>
                          <div className="mt-1 text-xs text-[var(--text-muted)]">{formatTime(entry.time)}</div>
                          <div className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">{entry.detail}</div>
                        </div>
                      </div>
                    ))
                  ) : (
                    <InlineEmptyState
                      title="No timeline entries yet"
                      description="Generate RCA, choose a remediation, and record verification to build the incident story."
                    />
                  )}
                </div>

                <div className="flex flex-wrap gap-2">
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
                        Open incident page
                      </a>
                    </Button>
                  ) : null}
                  <Button variant="outline" onClick={() => void refreshWorkflow()}>
                    Refresh timeline
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>

          <div ref={ticketRef}>
            <Card className={cn(!ticketUnlocked && !hasTicket && "opacity-70")}>
              <CardContent className="space-y-4 p-6">
                <div className="text-xs uppercase tracking-[0.24em] text-[var(--text-muted)]">Planned ticket note</div>
                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm leading-6 text-[var(--text-secondary)]">
                  {plannedTicketNote}
                </div>

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

                {currentTicket ? (
                  <div className="rounded-2xl border border-sky-400/20 bg-sky-500/8 p-4 text-sm leading-6 text-[var(--text-secondary)]">
                    {ticketAutoSyncHint}
                  </div>
                ) : null}

                {currentTicket?.comments?.length ? (
                  <div className="space-y-3">
                    <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Recent ticket updates</div>
                    {currentTicket.comments.slice(0, 3).map((comment) => (
                      <div key={comment.external_comment_id} className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-3">
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
              </CardContent>
            </Card>
          </div>

        </aside>
      </div>
    </div>
  );
}

function RemediationActionCard({
  remediation,
  activity,
  titlePrefix,
  description,
  isPrimary = false,
  isFocused = false,
  actor,
  note,
  pending,
  onNoteChange,
  onFocus,
  onExecute,
  onApprove,
  onReject,
  onRetry,
  onEscalate,
}: {
  remediation: RemediationRecord;
  activity: RemediationActivity;
  titlePrefix: string;
  description: string;
  isPrimary?: boolean;
  isFocused?: boolean;
  actor: string;
  note: string;
  pending: boolean;
  onNoteChange: (remediationId: number, value: string) => void;
  onFocus: (remediationId: number) => void;
  onExecute: (remediation: RemediationRecord) => void;
  onApprove: (remediation: RemediationRecord) => void;
  onReject: (remediation: RemediationRecord) => void;
  onRetry: (remediation: RemediationRecord) => void;
  onEscalate: (remediation: RemediationRecord) => void;
}) {
  const noteId = `remediation-note-${remediation.id}`;
  const decisionLocked = activity.decisionLocked;
  const attemptLabel =
    activity.attemptCount > 0 ? `${activity.attemptCount} ${activity.attemptCount === 1 ? "attempt" : "attempts"}` : "Ready";

  return (
    <div
      className={cn(
        "rounded-3xl border p-5",
        isPrimary ? "border-cyan-400/25 bg-cyan-500/8" : "border-[var(--border-subtle)] bg-[var(--surface-subtle)]",
        isFocused && "ring-1 ring-[var(--accent-ring)]",
      )}
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1">
          <div className={cn("text-xs uppercase tracking-[0.2em]", isPrimary ? "text-cyan-200/80" : "text-[var(--text-muted)]")}>
            {titlePrefix}
          </div>
          <div
            className={cn(
              "mt-2 text-lg font-semibold text-[var(--text-strong)] [overflow-wrap:anywhere]",
              decisionLocked && "line-through decoration-2 decoration-[var(--text-muted)] opacity-85",
            )}
          >
            {remediation.title}
          </div>
          <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)] [overflow-wrap:anywhere]">{description}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isFocused ? (
            <div className="rounded-full border border-[var(--accent-ring)] bg-[var(--surface-raised)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]">
              Selected
            </div>
          ) : null}
          <RemediationDecisionPill state={activity.decisionState} />
          <div className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)]">
            {attemptLabel}
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-4">
        <SummaryItem label="Action type" value={remediationMode(remediation)} />
        <SummaryItem label="Rank score" value={formatRelativeNumber(remediation.rank_score ?? 0, 3)} />
        <SummaryItem label="Automation" value={titleize(remediation.automation_level ?? "pending")} />
        <SummaryItem label="Revision scope" value={`Revision ${remediation.based_on_revision ?? "current"}`} />
      </div>

      {decisionLocked ? (
        <div className="mt-4 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
          <div className="text-xs uppercase tracking-[0.2em] text-[var(--text-muted)]">Workflow state</div>
          <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)] [overflow-wrap:anywhere]">{activity.summary}</p>
        </div>
      ) : null}

      <div className="mt-4">
        <Label htmlFor={noteId}>Operator note</Label>
        <Textarea
          id={noteId}
          value={note}
          onChange={(event) => {
            onFocus(remediation.id);
            onNoteChange(remediation.id, event.target.value);
          }}
          placeholder="Reason, guardrails, rollback note, or escalation context"
          className="mt-2 min-h-[88px]"
        />
        <div className="mt-2 text-xs text-[var(--text-muted)]">Recorded as {actor}. This note is sent with the selected action.</div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {activity.canRetry && activity.retryLabel ? (
          <Button
            size="sm"
            onClick={() => {
              onFocus(remediation.id);
              onRetry(remediation);
            }}
            disabled={pending}
          >
            {activity.retryLabel}
          </Button>
        ) : null}
        <Button
          size="sm"
          onClick={() => {
            onFocus(remediation.id);
            onExecute(remediation);
          }}
          disabled={pending || decisionLocked}
          className={cn(decisionLocked && "line-through")}
        >
          Approve & execute
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            onFocus(remediation.id);
            onEscalate(remediation);
          }}
          disabled={pending}
        >
          Escalate
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            onFocus(remediation.id);
            onApprove(remediation);
          }}
          disabled={pending || decisionLocked}
          className={cn(decisionLocked && "line-through")}
        >
          Approve only
        </Button>
        <Button
          size="sm"
          variant="danger"
          onClick={() => {
            onFocus(remediation.id);
            onReject(remediation);
          }}
          disabled={pending || decisionLocked}
          className={cn(decisionLocked && "line-through")}
        >
          Reject
        </Button>
      </div>
    </div>
  );
}

function RemediationDecisionPill({ state }: { state: RemediationDecisionState }) {
  return (
    <span
      className={cn(
        "rounded-full border px-3 py-1 text-xs font-semibold",
        state === "executed"
          ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200"
          : state === "approved"
            ? "border-amber-400/25 bg-amber-500/10 text-amber-200"
            : state === "executing"
              ? "border-sky-400/25 bg-sky-500/10 text-sky-200"
              : state === "failed" || state === "rejected"
                ? "border-rose-400/25 bg-rose-500/10 text-rose-200"
                : "border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--text-secondary)]",
      )}
    >
      {titleize(state)}
    </span>
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
            Use the remediation cards and verification section below. Evidence, ticket details, and history stay on this page.
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

function buildIncidentViewSteps(status: WorkflowState): IncidentViewStep[] {
  return INCIDENT_VIEW_STEP_TEMPLATES.map((step, index) => ({
    number: index + 1,
    title: step.title,
    description: step.description,
    action: step.action,
    status: INCIDENT_VIEW_STATUS_MAP[status][index],
  }));
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
          ? "border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-500/25 dark:bg-emerald-500/8 dark:text-emerald-200/90"
          : generation.llmConfigured
            ? "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-500/25 dark:bg-amber-500/8 dark:text-amber-200/90"
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
    <div className="min-w-0 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0 text-xs uppercase tracking-[0.2em] text-[var(--text-muted)] [overflow-wrap:anywhere]">{label}</div>
        {meta}
      </div>
      <div className="mt-2 min-w-0 text-sm text-[var(--text-strong)] [overflow-wrap:anywhere]">{value}</div>
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
          "This incident is new. RCA is generated automatically for new events, but fixes and verification stay unavailable until the first analysis is ready.",
        subtext: "If the automatic RCA has not appeared yet, generate it again for the current incident.",
        helpers: [
          {
            title: "Why now",
            text: "Start with the analysis before choosing or approving a fix.",
          },
          {
            title: "Blocked until RCA",
            text: "Fix options, execution, and verification stay unavailable until RCA exists.",
          },
          {
            title: "Expected output",
            text: "Root cause summary, confidence, and evidence references for this incident.",
          },
        ],
        ticketHint:
          "Create a ticket after RCA is available, or earlier if you need to escalate.",
        primary: { label: "Generate RCA", action: "generateRca" },
        secondary: { label: "Open evidence", action: "openEvidence" },
        steps,
      };
    case "RCA_GENERATED":
      return {
        tone: "info",
        badge: "Step 2 of 5",
        title: "Generate fix options",
        description:
          "Analysis is ready. Next, generate ranked manual and automated fix options.",
        subtext: "Create fix options before approval or execution.",
        helpers: [
          {
            title: "What changed",
            text: "Analysis is available and you can move to fix planning.",
          },
          {
            title: "What stays blocked",
            text: "Approval, execution, and verification stay unavailable until fix options are generated.",
          },
          {
            title: "Expected output",
            text: "Ranked fix options, available playbooks, and approval scope for this incident.",
          },
        ],
        ticketHint:
          "You can create or sync a Plane ticket now if other teams need context.",
        primary: { label: "Generate remediations", action: "generateRemediations", disabled: !hasRca },
        secondary: { label: "Review RCA", action: "reviewRca" },
        steps,
      };
    case "REMEDIATION_SUGGESTED":
    case "AWAITING_APPROVAL":
      return {
        tone: "info",
        badge: "Step 3 of 5",
        title: "Choose and approve a fix",
        description:
          "Review the ranked fix options, choose the safest one, and approve it for this incident version.",
        subtext: "Approval applies only to the selected fix and current workflow version.",
        helpers: [
          {
            title: "What is ready",
            text: "Analysis is ready and the platform has mapped ranked fix options to it.",
          },
          {
            title: "Approval rule",
            text: "Approve one fix at a time, and only for the current workflow version.",
          },
          {
            title: "Expected output",
            text: "One approved fix that can be run or recorded safely.",
          },
        ],
        ticketHint: hasTicket
          ? "A collaboration ticket already exists. Keep it updated while incident status stays here."
          : "You can create or sync a Plane ticket now if you need collaboration or escalation context.",
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
          "A fix is approved. You can now run the mapped Ansible job or record the manual action.",
        subtext: "Run only the approved fix for the current workflow version.",
        helpers: [
          {
            title: "What is ready",
            text: "Approval exists, the fix is selected, and execution scope is clear.",
          },
          {
            title: "Execution safety",
            text: "Run only the approved fix. If the incident changes, review approval again.",
          },
          {
            title: "Expected output",
            text: "Execution logs, result summary, and a clean handoff into verification.",
          },
        ],
        ticketHint:
          "Plane can reflect the execution result, but run approval still happens here.",
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
          "The approved fix is running. Review the execution output and keep the ticket updated if others are involved.",
        subtext: "Wait for execution to finish before recording verification.",
        helpers: [
          {
            title: "What is happening",
            text: "The platform is running the approved fix or recording the manual action result.",
          },
          {
            title: "What stays blocked",
            text: "Wait for execution to finish and the result summary to appear before verifying.",
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
          "Execution finished. Confirm whether the issue is resolved, or return to the remediation options if the first action did not fully recover the service.",
        subtext: "Record verification before closing, or choose another suggested fix if recovery is incomplete.",
        helpers: [
          {
            title: "What is ready",
            text: "Execution history and result output are available for review.",
          },
          {
            title: "What to confirm",
            text: "Confirm service recovery, record the actual fix, and if recovery is incomplete, try another suggestion from the remediation section.",
          },
          {
            title: "Expected output",
            text: "A verified result, or a clear next step if the fix did not hold.",
          },
        ],
        ticketHint:
          "If a ticket exists, add the verified outcome so collaborators can see what actually fixed the incident.",
        primary: { label: "Record verification", action: "focusVerification" },
        secondary: { label: "Review remediations", action: "focusRemediation" },
        steps,
      };
    case "VERIFIED":
      return {
        tone: "success",
        badge: "Step 5 of 5",
        title: "Close the incident",
        description:
          "The issue is resolved. Record the verified outcome and close the incident.",
        subtext: "Close the incident only after the result is verified.",
        helpers: [
          {
            title: "What is complete",
            text: "RCA, remediation, approval, execution, and verification all completed successfully.",
          },
          {
            title: "Why it matters",
            text: "This verified outcome can help with similar incidents later.",
          },
          {
            title: "Expected output",
            text: "Closed incident and a verified resolution record for similar cases.",
          },
        ],
        ticketHint: "Keep the ticket synchronized so collaborators see the final verified outcome before closure.",
        primary: { label: "Close incident", action: "closeIncident" },
        secondary: { label: "View detailed trace", action: "focusKnowledge" },
        steps,
      };
    case "CLOSED":
      return {
        tone: "success",
        badge: "Workflow complete",
        title: "Incident closed",
        description:
          "This incident is closed. Review the history, notes, and ticket updates whenever you need them.",
        subtext: "Verified results remain available for future analysis.",
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
        primary: { label: "View detailed trace", action: "focusKnowledge" },
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
        title: "Escalated for coordination",
        description:
          "This incident needs team coordination or external ownership. Use Plane for collaboration while incident status stays here.",
        subtext: "Keep the ticket updated and use this incident record as the primary status record.",
        helpers: [
          {
            title: "What is ready",
            text: "The incident record, RCA, and fix context can all be shared in Plane for coordination.",
          },
          {
            title: "What stays true",
            text: "Plane does not run fixes or move the incident through verification on its own.",
          },
          {
            title: "Expected output",
            text: "Clear ownership, team coordination, and an updated ticket.",
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

function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item ?? "").trim()).filter(Boolean);
  }
  const text = String(value ?? "").trim();
  return text ? [text] : [];
}

function formatTopClassPredictions(topClasses: Array<{ anomaly_type: string; probability: number }>): string {
  if (!topClasses.length) {
    return "Unavailable";
  }
  return topClasses
    .slice(0, 3)
    .map((item) => `${titleize(item.anomaly_type)} ${Math.round(Number(item.probability ?? 0) * 100)}%`)
    .join(" · ");
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
      ? "AI + evidence"
      : generationMode === "local-rag"
        ? "Built-in fallback"
        : "Source not available");
  const model = llmModel || "Not recorded";
  const runtime = asStringValue(payload.llm_runtime) || (llmUsed ? "Not recorded" : llmConfigured ? "Configured" : "Not configured");
  const retrievedDocuments = Array.isArray(payload.retrieved_documents)
    ? payload.retrieved_documents.length
    : source && "retrieval_refs" in source && Array.isArray(source.retrieval_refs)
      ? source.retrieval_refs.length
      : 0;

  let provenanceLabel = "System summary";
  let summary = "This RCA does not include generation metadata yet.";
  if (llmUsed) {
    provenanceLabel = "AI generated";
    summary =
      `Generated by the AI service` +
      `${model !== "Not recorded" ? ` with ${model}` : ""}` +
      `${runtime !== "Not recorded" ? ` via ${runtime}` : ""}` +
      `${retrievedDocuments ? ` using ${retrievedDocuments} evidence references.` : "."}`;
  } else if (llmConfigured) {
    provenanceLabel = "Fallback summary";
    summary = "This RCA used the built-in fallback process for this run even though an AI service was configured.";
  } else {
    summary = "This RCA used the built-in fallback process because no AI service was configured.";
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

  let analysis = rootCause || "Analysis details are not available yet.";
  if (analysis && !/[.!?]$/.test(analysis)) {
    analysis = `${analysis}.`;
  }
  const anomalyType = asStringValue(source?.category) || asStringValue(incident?.anomaly_type) || "current";
  analysis += ` This aligns with the ${titleize(anomalyType)} incident pattern.`;
  if (docRefs.length) {
    analysis += ` Supporting evidence includes ${docRefs.join(" and ")}.`;
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

function buildRemediationActivityMap(
  remediations: RemediationRecord[],
  actions: IncidentActionRecord[],
): Record<number, RemediationActivity> {
  return remediations.reduce<Record<number, RemediationActivity>>((result, remediation) => {
    const relatedActions = actions
      .filter((action) => action.remediation_id === remediation.id)
      .sort(compareIncidentActionRecency);
    const latestAction = relatedActions[0];
    const decisionState = normalizeRemediationDecisionState(remediation.status, latestAction?.execution_status);
    const attemptCount = relatedActions.length || (decisionState === "rejected" ? 1 : 0);

    result[remediation.id] = {
      attemptCount,
      canRetry: decisionState === "approved" || decisionState === "executed" || decisionState === "failed",
      decisionLocked: decisionState !== "available",
      decisionState,
      latestAction,
      retryLabel: decisionState === "approved" ? "Run approved action" : attemptCount > 0 ? "Retry action" : undefined,
      summary: buildRemediationActivitySummary(decisionState, latestAction, attemptCount),
    };
    return result;
  }, {});
}

function compareIncidentActionRecency(left: IncidentActionRecord, right: IncidentActionRecord) {
  return incidentActionSortKey(right) - incidentActionSortKey(left);
}

function incidentActionSortKey(action: IncidentActionRecord) {
  const timestamp = action.finished_at ?? action.started_at;
  const parsed = timestamp ? Date.parse(timestamp) : Number.NaN;
  return Number.isNaN(parsed) ? action.id : parsed;
}

function normalizeRemediationDecisionState(
  remediationStatus?: string | null,
  actionStatus?: string | null,
): RemediationDecisionState {
  const normalized = String(actionStatus || remediationStatus || "available").trim().toLowerCase();
  if (!normalized) {
    return "available";
  }
  if (normalized.includes("reject")) {
    return "rejected";
  }
  if (normalized.includes("fail") || normalized.includes("error")) {
    return "failed";
  }
  if (normalized.includes("executing")) {
    return "executing";
  }
  if (normalized.includes("executed")) {
    return "executed";
  }
  if (normalized.includes("approval") || normalized.includes("approved")) {
    return "approved";
  }
  return "available";
}

function buildRemediationActivitySummary(
  decisionState: RemediationDecisionState,
  latestAction: IncidentActionRecord | undefined,
  attemptCount: number,
) {
  const actor = latestAction?.triggered_by?.trim();
  const decisionTime = latestAction ? formatTime(latestAction.finished_at ?? latestAction.started_at ?? null) : "";
  const actorSummary = [actor, decisionTime].filter(Boolean).join(" · ");

  switch (decisionState) {
    case "approved":
      return actorSummary
        ? `Approved for this workflow revision by ${actorSummary}. Run this fix when you are ready.`
        : "Approved for this workflow revision. Run this fix when you are ready.";
    case "executing":
      return actorSummary
        ? `Execution is in progress after ${actorSummary}. Wait for the current run to finish before retrying.`
        : "Execution is in progress. Wait for the current run to finish before retrying.";
    case "executed":
      return latestAction?.result_summary
        ? truncateText(latestAction.result_summary, 200)
        : `This solution was already tried ${attemptCount > 1 ? `${attemptCount} times` : "once"}. Retry it only if you want to run the same fix again.`;
    case "failed":
      return latestAction?.result_summary
        ? truncateText(latestAction.result_summary, 200)
        : "The latest run failed. Retry this fix or choose a different remediation.";
    case "rejected":
      return "Rejected for the current review cycle. This decision stays visible for audit, and the one-time actions remain disabled.";
    case "available":
      return "No decision recorded yet.";
  }
}

function remediationDecisionPriority(activity: RemediationActivity | undefined) {
  switch (activity?.decisionState) {
    case "executing":
      return 0;
    case "approved":
      return 1;
    case "available":
      return 2;
    case "failed":
      return 3;
    case "executed":
      return 4;
    case "rejected":
      return 5;
    default:
      return 6;
  }
}

function truncateText(value: string | null | undefined, limit: number) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit - 1)}...`;
}

function evidenceDocumentHref(incidentId: string, collection: string, reference: string) {
  const encodedReference = reference
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `/incidents/${encodeURIComponent(incidentId)}/evidence/${encodeURIComponent(collection)}/${encodedReference}`;
}
