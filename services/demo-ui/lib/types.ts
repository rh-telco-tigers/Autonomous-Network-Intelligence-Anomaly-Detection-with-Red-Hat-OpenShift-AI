export type WorkflowState =
  | "NEW"
  | "RCA_GENERATED"
  | "REMEDIATION_SUGGESTED"
  | "AWAITING_APPROVAL"
  | "APPROVED"
  | "EXECUTING"
  | "EXECUTED"
  | "VERIFIED"
  | "CLOSED"
  | "RCA_REJECTED"
  | "EXECUTION_FAILED"
  | "VERIFICATION_FAILED"
  | "FALSE_POSITIVE"
  | "ESCALATED";

export type ServiceSnapshot = {
  name: string;
  ok: boolean;
  status: string;
  endpoint: string;
  payload: Record<string, unknown>;
};

export type IncidentTicketSummary = {
  provider: string;
  external_key?: string;
  external_id?: string;
  title?: string;
  url?: string;
  sync_state?: string;
  status?: string;
};

export type TopClassPrediction = {
  anomaly_type: string;
  probability: number;
};

export type IncidentRecord = {
  id: string;
  project: string;
  status: WorkflowState;
  workflow_state: WorkflowState;
  workflow_revision: number;
  severity: string;
  severity_tone?: string;
  anomaly_score: number;
  anomaly_type: string;
  predicted_confidence: number;
  top_classes?: TopClassPrediction[];
  class_probabilities?: Record<string, number>;
  model_version: string;
  feature_window_id?: string | null;
  feature_snapshot?: Record<string, number | string | boolean | null>;
  rca_payload?: RcaPayload | null;
  recommendation?: string | null;
  created_at: string;
  updated_at: string;
  subtitle?: string;
  impact?: string;
  blast_radius?: string;
  narrative?: string;
  timeline?: Array<{ time: string; title: string; detail: string }>;
  evidence_sources?: EvidenceSource[];
  similar_incidents?: Array<{ title: string; detail: string }>;
  explainability?: Array<{ feature: string; weight: number; label: string; tone: string }>;
  topology?: string[];
  plane_workflow_state?: string;
  is_active?: boolean;
  current_ticket_summary?: IncidentTicketSummary | null;
  ticket_search_text?: string;
  ticket_count?: number;
};

export type WorkflowDistribution = {
  state: WorkflowState;
  count: number;
  plane_state: string;
};

export type ConsoleState = {
  generated_at: string;
  cluster: {
    name: string;
    status: string;
    active_incident_id?: string | null;
    rca_status: string;
    auto_refresh_seconds: number;
  };
  summary: {
    incident_count: number;
    active_incident_count: number;
    open_incidents: number;
    critical_incidents: number;
    active_incident_categories: Array<{ anomaly_type: string; label: string; count: number; model_versions: string[] }>;
    active_incidents_by_model: Array<{ model_version: string; count: number; anomaly_types: string[] }>;
    workflow_state_distribution: WorkflowDistribution[];
    latest_score: number;
    latest_confidence: number;
    healthy_services: number;
    service_count: number;
  };
  incidents: IncidentRecord[];
  audit?: AuditEvent[];
  approvals?: ApprovalRecord[];
  services: ServiceSnapshot[];
  integrations: Record<string, Record<string, unknown>>;
  models?: {
    classifier_profiles?: ClassifierProfileSelectionState;
  };
  automation_actions?: Array<{
    action: string;
    playbook: string;
    exists: boolean;
    automation_mode: string;
    automation_enabled: boolean;
  }>;
  scenarios: Array<{
    scenario_name: string;
    anomaly_type: string;
    display_name: string;
    description: string;
    tone: string;
    is_nominal?: boolean;
  }>;
};

export type ClassifierProfile = {
  key: string;
  label: string;
  description: string;
  endpoint: string;
  model_name: string;
  model_version_label: string;
  configured: boolean;
  reachable?: boolean;
  status?: string;
  active: boolean;
  requested: boolean;
};

export type ClassifierProfileSelectionState = {
  active_profile: string | null;
  requested_profile: string | null;
  profiles: ClassifierProfile[];
  updated_at?: string | null;
  source?: string;
};

export type AuditEvent = {
  id: number;
  event_type: string;
  actor: string;
  incident_id?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type ApprovalRecord = {
  id: number;
  incident_id: string;
  action: string;
  approved_by: string;
  execute: boolean;
  status: string;
  output: string;
  created_at: string;
};

export type RcaRecord = {
  id: number;
  incident_id: string;
  version: number;
  based_on_revision: number;
  root_cause: string;
  category?: string | null;
  confidence: number;
  explanation?: string | null;
  model_name?: string | null;
  prompt_version?: string | null;
  retrieval_refs: string[];
  payload: RcaPayload;
  created_at: string;
};

export type RcaPayload = Record<string, unknown> & {
  root_cause?: string;
  explanation?: string;
  generation_mode?: string;
  generation_source_label?: string;
  llm_used?: boolean;
  llm_configured?: boolean;
  llm_model?: string | null;
  llm_runtime?: string | null;
  recommendation?: string;
  retrieved_documents?: Array<Record<string, unknown>>;
};

export type RelatedDocument = {
  title: string;
  reference: string;
  content: string;
  doc_type: string;
  collection: string;
  score: number;
  stage?: string;
  category?: string;
  incident_id?: string;
  knowledge_weight?: number;
  summary?: string;
  anomaly_types?: string[];
  match_reasons?: string[];
  score_breakdown?: Record<string, number>;
};

export type EvidenceSource = {
  title: string;
  detail: string;
  reference?: string;
  collection?: string;
  doc_type?: string;
  score?: number;
  excerpt?: string;
};

export type RelatedRecords = {
  incident_id: string;
  documents: RelatedDocument[];
  evidence: RelatedDocument[];
  reasoning: RelatedDocument[];
  resolution: RelatedDocument[];
  knowledge: RelatedDocument[];
};

export type RemediationRecord = {
  id: number;
  incident_id: string;
  rca_id?: number | null;
  based_on_revision: number;
  suggestion_rank: number;
  title: string;
  suggestion_type: string;
  description: string;
  risk_level: string;
  confidence: number;
  automation_level: string;
  requires_approval: boolean;
  playbook_ref: string;
  action_ref: string;
  preconditions: string[];
  expected_outcome: string;
  rank_score: number;
  status: string;
  factors: Record<string, number>;
  metadata?: Record<string, unknown>;
  ai_generated?: boolean;
  generation_kind?: string;
  generation_provider?: string;
  generation_status?: string;
  generation_error?: string;
  playbook_yaml?: string;
};

export type IncidentActionRecord = {
  id: number;
  incident_id: string;
  remediation_id?: number | null;
  action_mode: string;
  source_of_action: string;
  approved_revision: number;
  triggered_by: string;
  execution_status: string;
  notes?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  result_summary?: string | null;
  result_json: Record<string, unknown>;
};

export type VerificationRecord = {
  id: number;
  incident_id: string;
  action_id?: number | null;
  verified_by: string;
  verification_status: string;
  notes?: string | null;
  custom_resolution?: string | null;
  metric_based: boolean;
  created_at: string;
};

export type TicketCommentRecord = {
  id: number;
  ticket_id: number;
  external_comment_id: string;
  author?: string | null;
  body?: string | null;
  comment_type?: string | null;
  created_at: string;
  updated_at: string;
};

export type TicketSyncEvent = {
  id: number;
  ticket_id: number;
  direction: string;
  event_type: string;
  delivery_id?: string | null;
  payload_hash: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type TicketRecord = {
  id: number;
  incident_id: string;
  provider: string;
  external_key?: string | null;
  external_id?: string | null;
  workspace_id?: string | null;
  project_id?: string | null;
  status?: string | null;
  url?: string | null;
  title?: string | null;
  last_synced_at?: string | null;
  sync_state?: string | null;
  last_synced_revision?: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  sync_events?: TicketSyncEvent[];
  comments?: TicketCommentRecord[];
  operation?: Record<string, unknown>;
};

export type ResolutionExtract = {
  id: number;
  incident_id: string;
  ticket_id?: number | null;
  source_comment_id?: string | null;
  summary: string;
  verified: boolean;
  verification_quality: string;
  knowledge_weight: number;
  usage_count: number;
  success_rate: number;
  last_validated_at?: string | null;
  created_at: string;
};

export type IncidentWorkflow = {
  incident: IncidentRecord;
  rca_history: RcaRecord[];
  remediations: RemediationRecord[];
  current_remediations: RemediationRecord[];
  actions: IncidentActionRecord[];
  verifications: VerificationRecord[];
  tickets: TicketRecord[];
  current_ticket?: TicketRecord | null;
  resolution_extracts: ResolutionExtract[];
  available_transitions: WorkflowState[];
  plane_workflow_state: string;
};

export type DebugTracePacket = {
  sequence: number;
  category: string;
  phase: string;
  title: string;
  timestamp: string;
  service?: string;
  target?: string;
  endpoint?: string;
  method?: string;
  payload: unknown;
  metadata: Record<string, unknown>;
};

export type IncidentDebugTraceResponse = {
  incident: IncidentRecord;
  trace_packets: DebugTracePacket[];
};

export type TicketLookupResponse = {
  ticket: TicketRecord;
  workflow: IncidentWorkflow;
};

export type KnowledgeArticleResponse = {
  article: RelatedDocument;
};

export type DocumentResponse = {
  document: RelatedDocument;
};

export type ScenarioRunResponse = {
  scenario: string;
  feature_window: Record<string, unknown>;
  score: Record<string, unknown>;
  rca?: Record<string, unknown> | null;
  rca_error?: Record<string, unknown> | null;
  incident?: IncidentRecord | null;
  state: ConsoleState;
};
