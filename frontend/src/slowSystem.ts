export type Owner =
  | "react_ui"
  | "slowtask"
  | "codex_proposal"
  | "router"
  | "event_journal"
  | "composer"
  | "tool_executor"
  | "user_patch_pipeline";

export type RouterDecision =
  | "FAST_ONLY"
  | "SPAWN_SLOW_TASK"
  | "PATCH_ACTIVE_SLOW_TASK"
  | "IGNORE";

export type SlowTaskLifecycleState =
  | "CREATED"
  | "WAITING_FOR_SLOT"
  | "PLANNING"
  | "EXECUTING"
  | "WAITING_FOR_USER_CONFIRMATION"
  | "COMPLETED"
  | "CANCELLED"
  | "FAILED";

export type TaskFocus =
  | "ACTIVE_TASK_PATCH"
  | "FOREGROUND_CHAT"
  | "NEW_TASK_CANDIDATE"
  | "CANCEL_OR_PAUSE_CANDIDATE"
  | "NON_ASSISTANT"
  | "AMBIGUOUS";

export type EvidenceKind =
  | "authoritative"
  | "non_authoritative_hypothesis"
  | "untrusted_web_evidence"
  | "stale_evidence"
  | "codex_proposal";

export type ProposalType =
  | "plan_update"
  | "evidence_review"
  | "tool_preview"
  | "clarification"
  | "commitment_draft";

export type ProposalStatus = "draft" | "validated" | "accepted" | "rejected";

export type InputMatchMode =
  | "scenario_button"
  | "keyword_match"
  | "dynamic_router"
  | "manual_fallback";

export type PlanVersionReadOnly = Readonly<{
  kind: "plan_version";
  value: number;
  owner: "slowtask";
  source: "SlowTask mock event";
  label: "read-only / owned by SlowTask";
}>;

export type TaskEventSeqReadOnly = Readonly<{
  kind: "task_event_seq";
  value: number;
  owner: "slowtask";
  source: "SlowTask mock event";
  label: "read-only / owned by SlowTask";
}>;

export type ConversationTurn = Readonly<{
  id: string;
  speaker: "user" | "assistant_fast" | "tool" | "system";
  text: string;
  owner: Owner;
  note: string;
}>;

export type PlanVersionSnapshot = Readonly<{
  planVersion: number;
  status: "current" | "superseded" | "failed" | "completed" | "cancelled";
  summary: string;
  reason: "initial_plan" | "user_patch" | "stale_evidence_adopted" | "manual_demo";
  createdByEventId: string;
}>;

export type PendingConfirmation = Readonly<{
  confirmationId: string;
  scope:
    | "TASK_CANCEL"
    | "SWITCH_TASK"
    | "DEMO_DESTRUCTIVE_ACTION"
    | "FINAL_ARGUMENT_CONFIRMATION";
  planVersion: number;
  prompt: string;
  riskSummary: string;
  status: "pending" | "accepted_by_user" | "rejected_by_user";
}>;

export type SlotSummaryItem = Readonly<{
  name: string;
  label: string;
  state: "UNKNOWN" | "CANDIDATE" | "RESOLVED" | "AMBIGUOUS" | "CONFLICTING" | "DEFAULTED";
  valuePreview: string;
  source: string;
  requiredFor: readonly string[];
  askedCount: number;
}>;

export type ReadinessSummary = Readonly<{
  search: boolean;
  plan: boolean;
  commitment: boolean;
}>;

export type ClarificationSummary = Readonly<{
  blockedStage: "search" | "plan" | "commitment";
  askFields: readonly string[];
  reason: "missing" | "ambiguous" | "conflicting";
  attempt: number;
}>;

export type SlowTaskSnapshot = Readonly<{
  taskId: string;
  owner: "slowtask";
  lifecycleState: SlowTaskLifecycleState;
  currentPlanVersion: PlanVersionReadOnly;
  currentTaskEventSeq: TaskEventSeqReadOnly;
  planVersions: readonly PlanVersionSnapshot[];
  missingFields: readonly string[];
  conflictingFields: readonly string[];
  slotSummary?: readonly SlotSummaryItem[];
  readiness?: ReadinessSummary;
  clarification?: ClarificationSummary;
  pendingConfirmation?: PendingConfirmation;
  semanticCommitmentStatus: "not_emitted_yet" | "emitted_by_slowtask";
  staleEvidencePolicy: string;
  processingSummary: string;
}>;

export type CanonicalDemoEventName =
  | "TEXT_INPUT_RECEIVED"
  | "TURN_OPENED"
  | "TURN_INGRESS_COMMITTED"
  | "ROUTER_DECISION_EMITTED"
  | "TASK_FOCUS_STATE_UPDATED"
  | "SLOWTASK_CREATED"
  | "SLOWTASK_STATE_CHANGED"
  | "PLANNING_STARTED"
  | "EVIDENCE_REVIEWED"
  | "INSUFFICIENT_EVIDENCE_FOR_ACTION"
  | "CLARIFICATION_REQUESTED"
  | "WAITING_FOR_SLOT"
  | "USER_PATCH_RECEIVED"
  | "USER_PATCH_INTERPRETED"
  | "PLAN_VERSION_ADVANCED"
  | "TASK_REPLANNED"
  | "PLANNING_RESTARTED"
  | "TOOL_CALL_STARTED"
  | "WAITING_FOR_TOOL"
  | "TOOL_RESULT_RECEIVED"
  | "TOOL_RESULT_MARKED_STALE"
  | "STALE_EVIDENCE_RECORDED"
  | "STALE_EVIDENCE_ADOPTED"
  | "SEMANTIC_COMMITMENT_EMITTED"
  | "CONFIRMATION_REQUIRED"
  | "WAITING_FOR_USER_CONFIRMATION"
  | "USER_CONFIRMATION_RECEIVED"
  | "CONFIRMATION_ACCEPTED"
  | "CONFIRMATION_REJECTED"
  | "SLOWTASK_CANCEL_REQUESTED"
  | "SLOWTASK_CANCELLED"
  | "FINALIZING"
  | "ARGUMENTS_RESOLVED"
  | "ARGUMENT_RESOLUTION_PROVENANCE"
  | "TOOL_MANIFEST_LOADED"
  | "TOOL_ARGUMENTS_READY"
  | "TOOL_PREVIEW_AVAILABLE"
  | "TOOL_EXECUTION_AUTHORIZED"
  | "TOOL_EXECUTION_STARTED"
  | "TOOL_PROGRESS_UPDATED"
  | "TOOL_EXECUTION_CANCEL_REQUESTED"
  | "TOOL_EXECUTION_CANCELLED"
  | "SLOW_LLM_STRUCTURED_OUTPUT_EMITTED"
  | "ADAPTER_OUTPUT_DEGRADED";

export type TimelineEventIdentity =
  | Readonly<{
      canonical: true;
      eventName: CanonicalDemoEventName;
    }>
  | Readonly<{
      canonical: false;
      displayLabel: "CODEX_PROPOSAL_DISPLAYED" | "MOCK_ACTION_CLICKED";
      nonCanonicalReason: "UI display label / non-canonical mock label";
    }>;

export type SlowTaskTimelineEvent = Readonly<{
  id: string;
  order: number;
  title: string;
  identity: TimelineEventIdentity;
  owner: Owner;
  planVersion?: PlanVersionReadOnly;
  taskEventSeq?: TaskEventSeqReadOnly;
  routerDecision?: RouterDecision;
  taskFocus?: TaskFocus;
  summary: string;
  details: readonly string[];
}>;

export type EvidenceItem = Readonly<{
  id: string;
  evidenceId: string;
  kind: EvidenceKind;
  title: string;
  body: string;
  owner: Owner;
  sourceRef: string;
  planVersion: number;
  stale: boolean;
}>;

export type CodexProposalSafety = Readonly<{
  codex_is_fact_owner: boolean;
  advances_plan_version: boolean;
  authorizes_tool: boolean;
  contains_secret: boolean;
  mutates_task_snapshot?: boolean;
  emits_canonical_event?: boolean;
  executes_external_tool?: boolean;
  contains_raw_provider_body?: boolean;
}>;

export type CodexProposal = Readonly<{
  proposalId: string;
  owner: "codex_proposal";
  proposalType: ProposalType;
  status: ProposalStatus;
  label: "Proposal only";
  summary: string;
  suggestedNextSteps: readonly string[];
  missingFields: readonly string[];
  requiresConfirmation: boolean;
  riskNotes: readonly string[];
  sourceEvidenceRefs: readonly string[];
  safety: CodexProposalSafety;
  backendMode: "static_mock" | "python_fake" | "python_codex_cli_local" | "unavailable";
  boundaryWarning: string;
}>;

export type DemoAction =
  | "start_new_task"
  | "send_user_patch"
  | "receive_late_tool_result"
  | "request_cancel_confirmation"
  | "foreground_chat";

export type SlowSystemScenario = Readonly<{
  id: string;
  title: string;
  shortName: string;
  demoAction: DemoAction;
  mockInput: string;
  matchKeywords: readonly string[];
  summary: string;
  conversation: readonly ConversationTurn[];
  routerDecision: RouterDecision;
  taskFocus: TaskFocus;
  routerRule: string;
  activeTaskContext: string;
  slowTask: SlowTaskSnapshot;
  timeline: readonly SlowTaskTimelineEvent[];
  liveProgress: readonly WorkbenchProgressWire[];
  streaming: WorkbenchStreamingWire;
  evidence: readonly EvidenceItem[];
  staleEvidence: readonly EvidenceItem[];
  codexProposal: CodexProposal;
  proposalType: ProposalType;
  answer: string;
}>;

export type WorkbenchRun = Readonly<{
  scenario: SlowSystemScenario;
  inputText: string;
  matchedBy: InputMatchMode;
}>;

export type WorkbenchEvidenceWire = Readonly<{
  evidence_id: string;
  evidence_ref: string;
  source: string;
  trust_level: string;
  plan_version: number;
  label: string;
  summary: string;
  stale: boolean;
  provenance: string;
}>;

export type WorkbenchTimelineWire = Readonly<{
  event_id: string;
  event_seq: number;
  event_name: string;
  canonical: boolean;
  source_module: string;
  task_id?: string | null;
  plan_version?: number | null;
  task_event_seq?: number | null;
  created_monotonic_ms?: number | null;
  details: readonly string[];
}>;

export type WorkbenchProgressWire = Readonly<{
  trace_id: string;
  sequence: number;
  kind: string;
  status: string;
  provider_mode: string;
  output_mode: string;
  canonical: false;
  task_id?: string | null;
  plan_version?: number | null;
  created_monotonic_ms?: number | null;
  tool_name?: string | null;
  proposal_only?: boolean | null;
  result_present?: boolean | null;
  usage?: Readonly<Record<string, number>> | null;
  latency_ms?: number | null;
  detail?: string | null;
  phase?: string | null;
  label?: string | null;
  orchestration_role?: string | null;
  subtask_id?: string | null;
  subtask_goal?: string | null;
  public_thought?: string | null;
  tool_input_summary?: string | null;
  tool_output_summary?: string | null;
  next_step?: string | null;
  blocked_on_user?: boolean | null;
}>;

export type WorkbenchStreamingWire = Readonly<{
  active: boolean;
  phase: string;
  label: string;
  detail: string;
  sequence: number;
}>;

export type WorkbenchTaskWire = Readonly<{
  task_id: string;
  lifecycle: SlowTaskLifecycleState;
  current_plan_version: number;
  latest_task_event_seq: number;
  goal_summary: string;
  resolved_arguments: Readonly<Record<string, unknown>>;
  missing_fields: readonly string[];
  conflicting_fields: readonly string[];
  slot_summary?: readonly Readonly<Record<string, unknown>>[];
  readiness?: Readonly<Record<string, unknown>>;
  clarification?: Readonly<Record<string, unknown>> | null;
  plan_versions: readonly Readonly<Record<string, unknown>>[];
  evidence: readonly WorkbenchEvidenceWire[];
  stale_evidence: readonly WorkbenchEvidenceWire[];
  adopted_evidence: readonly Readonly<Record<string, unknown>>[];
  pending_confirmation: Readonly<Record<string, unknown>> | null;
  in_flight_tool_calls: readonly Readonly<Record<string, unknown>>[];
  tool_calls: readonly Readonly<Record<string, unknown>>[];
  semantic_commitment: Readonly<Record<string, unknown>>;
  terminal_outcome: string | null;
  late_evidence_count: number;
  stale_evidence_policy: string;
}>;

export type WorkbenchSnapshotWire = Readonly<{
  snapshot_id: string;
  session_id: string;
  mode: string;
  router: Readonly<Record<string, unknown>> | null;
  task: WorkbenchTaskWire | null;
  conversation: readonly Readonly<Record<string, unknown>>[];
  timeline: readonly WorkbenchTimelineWire[];
  provider_trace: readonly Readonly<Record<string, unknown>>[];
  live_progress: readonly WorkbenchProgressWire[];
  streaming: WorkbenchStreamingWire;
  codex_proposals: readonly Readonly<Record<string, unknown>>[];
  context_pack: Readonly<Record<string, unknown>>;
  context_hash: string;
  prompt_preview: string;
  timing_summary: Readonly<Record<string, number | null>>;
  capability_snapshot: Readonly<Record<string, unknown>>;
  capability_matrices: readonly Readonly<Record<string, unknown>>[];
  replay: Readonly<Record<string, unknown>>;
  safety: Readonly<Record<string, unknown>>;
}>;
