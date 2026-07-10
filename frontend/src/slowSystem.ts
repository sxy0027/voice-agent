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
  status: "current" | "superseded" | "failed" | "completed";
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

export type SlowTaskSnapshot = Readonly<{
  taskId: string;
  owner: "slowtask";
  lifecycleState: SlowTaskLifecycleState;
  currentPlanVersion: PlanVersionReadOnly;
  currentTaskEventSeq: TaskEventSeqReadOnly;
  planVersions: readonly PlanVersionSnapshot[];
  pendingConfirmation?: PendingConfirmation;
  semanticCommitmentStatus: "not_emitted_yet" | "emitted_by_slowtask";
  staleEvidencePolicy: string;
  processingSummary: string;
}>;

export type CanonicalDemoEventName =
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
  | "CONFIRMATION_REQUIRED"
  | "WAITING_FOR_USER_CONFIRMATION";

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
