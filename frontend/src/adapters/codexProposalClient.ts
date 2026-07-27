import type {
  CodexProposal,
  CodexProposalSafety,
  CanonicalDemoEventName,
  ConversationTurn,
  EvidenceItem,
  Owner,
  PlanVersionSnapshot,
  ProposalStatus,
  ProposalType,
  SlowSystemScenario,
  SlowTaskLifecycleState,
  SlowTaskTimelineEvent,
  WorkbenchSnapshotWire,
  WorkbenchEvidenceWire,
} from "../slowSystem";

export type CodexProviderMode = "fake" | "codex_cli_local" | "codex_cli_unavailable";

type BackendProposal = Readonly<{
  proposal_id: string;
  proposal_type: ProposalType;
  status: ProposalStatus;
  summary: string;
  suggested_next_steps: readonly string[];
  missing_fields: readonly string[];
  requires_confirmation: boolean;
  risk_notes: readonly string[];
  source_evidence_refs: readonly string[];
  safety: CodexProposalSafety;
}>;

type BackendResponse = Readonly<{
  proposal: BackendProposal;
  backend: string;
  session_id?: string;
  snapshot?: WorkbenchSnapshotWire;
  capability: Readonly<{
    output_mode: "mock" | "real" | "degraded";
    provider: string;
    health_status: string;
  }>;
}>;

type WorkbenchMessageResponse = Readonly<{
  snapshot: WorkbenchSnapshotWire;
  proposal?: BackendProposal;
  capability?: BackendResponse["capability"];
}>;

export type RequestCodexProposalInput = Readonly<{
  scenario: SlowSystemScenario;
  intent: string;
  providerMode: CodexProviderMode;
  allowLocalCodexCli: boolean;
  sessionId?: string | null;
  onSessionCreated?: (sessionId: string, snapshot: WorkbenchSnapshotWire) => void;
  onSnapshot?: (snapshot: WorkbenchSnapshotWire) => void;
  onStreamStarted?: (stop: () => void) => void;
}>;

export type CodexProposalResult = CodexProposal & Readonly<{
  sessionId?: string;
  runtimeSnapshot?: WorkbenchSnapshotWire;
}>;

export async function requestCodexProposal({
  scenario,
  intent,
  providerMode,
  allowLocalCodexCli,
  sessionId,
  onSessionCreated,
  onSnapshot,
  onStreamStarted,
}: RequestCodexProposalInput): Promise<CodexProposalResult> {
  // Keep the old compatibility route for non-browser test/runtime surfaces
  // without EventSource. Real browser sessions take the direct session route
  // below so the SSE stream can be opened before the provider call begins.
  if (typeof EventSource === "undefined") {
    return requestCompatibilityProposal({
      scenario,
      intent,
      providerMode,
      allowLocalCodexCli,
      sessionId,
    });
  }

  const created = sessionId
    ? null
    : await createWorkbenchSession({
        provider_mode: providerMode,
        allow_local_codex_cli: allowLocalCodexCli,
        timeout_seconds: 45,
      });
  const resolvedSessionId = created?.sessionId ?? sessionId;
  if (!resolvedSessionId) {
    throw new Error("Workbench session was not created");
  }
  if (created) {
    onSessionCreated?.(created.sessionId, created.snapshot);
    onSnapshot?.(created.snapshot);
  } else {
    onSnapshot?.(await getWorkbenchSnapshot(resolvedSessionId));
  }

  const stopStream = streamWorkbenchSession(resolvedSessionId, onSnapshot ?? (() => undefined));
  onStreamStarted?.(stopStream);

  const action = sessionId ? undefined : scenario.demoAction;
  let currentSnapshot = created?.snapshot;
  if (!currentSnapshot) {
    currentSnapshot = await getWorkbenchSnapshot(resolvedSessionId);
  }
  // Cancellation is meaningful only for an existing active SlowTask.  Do not
  // manufacture an unrelated bootstrap task merely so a cancellation demo can
  // show a confirmation gate; the backend returns a truthful no-active-task
  // answer instead.
  if (
    action
    && action !== "start_new_task"
    && action !== "foreground_chat"
    && action !== "request_cancel_confirmation"
    && !hasActiveTask(currentSnapshot)
  ) {
    const bootstrap = await sendWorkbenchMessage(
      resolvedSessionId,
      "帮我规划一个两天的客户来访行程，地点尽量靠近公司。",
      "start",
    );
    currentSnapshot = bootstrap.snapshot;
    onSnapshot?.(currentSnapshot);
  }

  const message = await sendWorkbenchMessage(resolvedSessionId, intent, action);
  onSnapshot?.(message.snapshot);
  const capability = message.capability ?? {
    output_mode: "degraded" as const,
    provider: "codex_cli",
    health_status: "unknown",
  };
  if (!message.proposal) {
    throw new Error("Workbench message did not return a validated proposal");
  }
  const proposal = fromBackendProposal(message.proposal, providerMode, capability.output_mode);
  return Object.assign(proposal, {
    sessionId: resolvedSessionId,
    runtimeSnapshot: message.snapshot,
  });
}

async function requestCompatibilityProposal({
  scenario,
  intent,
  providerMode,
  allowLocalCodexCli,
  sessionId,
}: RequestCodexProposalInput): Promise<CodexProposalResult> {
  const response = await fetch("/api/workbench/codex-proposal", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      snapshot: toBackendSnapshot(scenario),
      intent,
      proposal_type: scenario.proposalType,
      source_evidence_refs: scenario.codexProposal.sourceEvidenceRefs,
      provider_mode: providerMode,
      allow_local_codex_cli: allowLocalCodexCli,
      timeout_seconds: 45,
      session_id: sessionId ?? undefined,
      action: sessionId ? undefined : scenario.demoAction,
    }),
  });

  if (!response.ok) {
    throw new Error(`Codex proposal backend returned ${response.status}`);
  }

  const payload = (await response.json()) as BackendResponse;
  const proposal = fromBackendProposal(payload.proposal, providerMode, payload.capability.output_mode);
  return Object.assign(proposal, {
    sessionId: payload.session_id,
    runtimeSnapshot: payload.snapshot,
  });
}

export function fromBackendProposal(
  proposal: BackendProposal,
  providerMode: CodexProviderMode,
  outputMode: "mock" | "real" | "degraded" = "mock",
): CodexProposal {
  return {
    proposalId: proposal.proposal_id,
    owner: "codex_proposal",
    proposalType: proposal.proposal_type,
    status: proposal.status,
    label: "Proposal only",
    summary: proposal.summary,
    suggestedNextSteps: proposal.suggested_next_steps,
    missingFields: proposal.missing_fields,
    requiresConfirmation: proposal.requires_confirmation,
    riskNotes: proposal.risk_notes,
    sourceEvidenceRefs: proposal.source_evidence_refs,
    safety: proposal.safety,
    backendMode:
      outputMode === "real" && providerMode === "codex_cli_local"
        ? "python_codex_cli_local"
        : outputMode === "degraded"
          ? "unavailable"
          : "python_fake",
    boundaryWarning:
      "该 proposal 已经过后端 bridge 校验；它仍然不是 SlowTask fact，不能直接推进 plan_version。",
  };
}

function toBackendSnapshot(scenario: SlowSystemScenario) {
  return {
    snapshot_id: `snapshot_${scenario.id}`,
    mode: "demo",
    scenario_id: scenario.id,
    router: {
      router_decision: scenario.routerDecision,
      task_focus: scenario.taskFocus,
      turn_id: `${scenario.id}_turn`,
      evidence_uncertainty: scenario.taskFocus === "AMBIGUOUS" ? "high" : "low",
    },
    task: {
      task_id: scenario.slowTask.taskId,
      lifecycle: scenario.slowTask.lifecycleState,
      current_plan_version: scenario.slowTask.currentPlanVersion.value,
      latest_task_event_seq: scenario.slowTask.currentTaskEventSeq.value,
      plan_versions: scenario.slowTask.planVersions.map((plan) => ({
        plan_version: plan.planVersion,
        status: plan.status,
        summary: plan.summary,
        created_by_event_id: plan.createdByEventId,
        reason: plan.reason,
      })),
      evidence: [...scenario.evidence, ...scenario.staleEvidence].map((item) => ({
        evidence_id: item.sourceRef,
        event_id: item.id,
        source_evidence_refs: [item.sourceRef],
        label: item.title,
        summary: item.body,
        plan_version: item.planVersion,
        stale: item.stale,
      })),
    },
    codex_proposals: [],
    safety: {
      raw_audio_included: false,
      raw_provider_body_included: false,
      prompt_dump_included: false,
      secret_included: false,
      local_path_included: false,
    },
  };
}

export async function confirmWorkbenchSession(
  sessionId: string,
  confirmationId: string,
  accepted: boolean,
): Promise<WorkbenchSnapshotWire> {
  const response = await fetch(
    `/api/workbench/sessions/${encodeURIComponent(sessionId)}/confirmations/${encodeURIComponent(confirmationId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accepted }),
    },
  );
  if (!response.ok) {
    throw new Error(`Workbench confirmation returned ${response.status}`);
  }
  const payload = (await response.json()) as { snapshot: WorkbenchSnapshotWire };
  return payload.snapshot;
}

export async function createWorkbenchSession(
  config: Readonly<Record<string, unknown>> = {},
): Promise<{ sessionId: string; snapshot: WorkbenchSnapshotWire }> {
  const response = await fetch("/api/workbench/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!response.ok) {
    throw new Error(`Workbench session creation returned ${response.status}`);
  }
  const payload = (await response.json()) as {
    session_id: string;
    snapshot: WorkbenchSnapshotWire;
  };
  return { sessionId: payload.session_id, snapshot: payload.snapshot };
}

export async function getWorkbenchSnapshot(sessionId: string): Promise<WorkbenchSnapshotWire> {
  const response = await fetch(
    `/api/workbench/sessions/${encodeURIComponent(sessionId)}/snapshot`,
  );
  if (!response.ok) {
    throw new Error(`Workbench snapshot returned ${response.status}`);
  }
  const payload = (await response.json()) as { snapshot: WorkbenchSnapshotWire };
  return payload.snapshot;
}

export async function sendWorkbenchMessage(
  sessionId: string,
  text: string,
  action?: string,
): Promise<WorkbenchMessageResponse> {
  const response = await fetch(
    `/api/workbench/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, action }),
    },
  );
  if (!response.ok) {
    throw new Error(`Workbench message returned ${response.status}`);
  }
  return (await response.json()) as WorkbenchMessageResponse;
}

export function streamWorkbenchSession(
  sessionId: string,
  onSnapshot: (snapshot: WorkbenchSnapshotWire) => void,
  onError?: (error: Event) => void,
): () => void {
  if (typeof EventSource === "undefined") {
    return () => undefined;
  }
  const stream = new EventSource(
    `/api/workbench/sessions/${encodeURIComponent(sessionId)}/stream`,
  );
  stream.addEventListener("snapshot", (event) => {
    try {
      onSnapshot(JSON.parse((event as MessageEvent<string>).data) as WorkbenchSnapshotWire);
    } catch {
      onError?.(event);
    }
  });
  stream.addEventListener("error", (event) => onError?.(event));
  return () => stream.close();
}

export function workbenchSnapshotToScenario(
  snapshot: WorkbenchSnapshotWire,
  inputText: string,
  baseScenario: SlowSystemScenario,
  proposal: CodexProposal,
): SlowSystemScenario {
  const task = snapshot.task;
  const routerDecision = asRouterDecision(snapshot.router?.router_decision);
  const taskFocus = asTaskFocus(snapshot.router?.task_focus);
  const lifecycle = task?.lifecycle ?? baseScenario.slowTask.lifecycleState;
  const planVersions = (task?.plan_versions ?? []).map((plan) => {
    const record = plan as Record<string, unknown>;
    return {
      planVersion: Number(record.plan_version ?? 1),
      status: asPlanStatus(record.status),
      summary: String(record.summary ?? "Python-owned plan projection."),
      reason: asPlanReason(record.reason),
      createdByEventId: String(record.created_by_event_id ?? "event_journal"),
    } satisfies PlanVersionSnapshot;
  });
  const currentPlanVersion = task?.current_plan_version ?? baseScenario.slowTask.currentPlanVersion.value;
  const currentTaskEventSeq = task?.latest_task_event_seq ?? baseScenario.slowTask.currentTaskEventSeq.value;
  const pendingConfirmation = task?.pending_confirmation
    ? toPendingConfirmation(task.pending_confirmation)
    : undefined;
  const conversation = snapshot.conversation.map((item, index) => toConversation(item, index));
  const timeline = snapshot.timeline.map((item, index) => toTimelineEvent(item, index, snapshot.router));
  const liveProgress = snapshot.live_progress ?? [];
  const streaming = snapshot.streaming ?? {
    active: false,
    phase: "idle",
    label: "等待新的 Workbench turn",
    detail: "",
    sequence: 0,
  };
  const evidence = (task?.evidence ?? []).map((item, index) => toEvidence(item, index, false));
  const staleEvidence = (task?.stale_evidence ?? []).map((item, index) => toEvidence(item, index, true));
  const taskLifecycle = asLifecycle(lifecycle);
  const semanticCommitmentStatus = task?.semantic_commitment?.status === "emitted"
    ? "emitted_by_slowtask"
    : "not_emitted_yet";
  const dynamicId = `runtime_${snapshot.session_id}`;
  return {
    id: dynamicId,
    title: `Python session：${routerDecision}`,
    shortName: "Python session",
    demoAction: baseScenario.demoAction,
    mockInput: inputText,
    matchKeywords: [],
    summary: `动态 Workbench session ${snapshot.session_id} 由 Python Event Journal / reducer 投影。`,
    conversation: conversation.length > 0 ? conversation : baseScenario.conversation,
    routerDecision,
    taskFocus,
    routerRule: "RouterDecision、TaskFocus、plan_version 和 task_event_seq 均来自 Python canonical event projection。",
    activeTaskContext: task
      ? `${task.task_id} / lifecycle=${taskLifecycle} / plan_version=${currentPlanVersion}`
      : "当前没有 active SlowTask。",
    slowTask: {
      taskId: task?.task_id ?? baseScenario.slowTask.taskId,
      owner: "slowtask",
      lifecycleState: taskLifecycle,
      currentPlanVersion: readonlyPlanVersion(currentPlanVersion),
      currentTaskEventSeq: readonlyTaskEventSeq(currentTaskEventSeq),
      planVersions: planVersions.length > 0 ? planVersions : baseScenario.slowTask.planVersions,
      missingFields: task?.missing_fields ?? baseScenario.slowTask.missingFields,
      conflictingFields: task?.conflicting_fields ?? baseScenario.slowTask.conflictingFields,
      pendingConfirmation,
      semanticCommitmentStatus,
      staleEvidencePolicy: task?.stale_evidence_policy ?? baseScenario.slowTask.staleEvidencePolicy,
      processingSummary: task?.goal_summary ?? "Python-owned SlowTask projection.",
    },
    timeline: timeline.length > 0 ? timeline : baseScenario.timeline,
    liveProgress,
    streaming,
    evidence,
    staleEvidence,
    codexProposal: proposal,
    proposalType: proposal.proposalType,
    answer: proposal.summary,
  };
}

function hasActiveTask(snapshot: WorkbenchSnapshotWire): boolean {
  const lifecycle = snapshot.task?.lifecycle;
  return Boolean(snapshot.task) && !["COMPLETED", "CANCELLED", "FAILED"].includes(String(lifecycle));
}

function readonlyPlanVersion(value: number) {
  return {
    kind: "plan_version" as const,
    value,
    owner: "slowtask" as const,
    source: "SlowTask mock event" as const,
    label: "read-only / owned by SlowTask" as const,
  };
}

function readonlyTaskEventSeq(value: number) {
  return {
    kind: "task_event_seq" as const,
    value,
    owner: "slowtask" as const,
    source: "SlowTask mock event" as const,
    label: "read-only / owned by SlowTask" as const,
  };
}

function toConversation(item: Readonly<Record<string, unknown>>, index: number): ConversationTurn {
  const rawSpeaker = String(item.speaker ?? "system");
  const speaker: ConversationTurn["speaker"] = rawSpeaker === "user"
    ? "user"
    : rawSpeaker === "tool"
      ? "tool"
      : rawSpeaker === "assistant_fast"
        ? "assistant_fast"
        : "system";
  const owner = speaker === "user" ? "event_journal" : speaker === "tool" ? "tool_executor" : "slowtask";
  return {
    id: String(item.id ?? `conversation_${index}`),
    speaker,
    text: String(item.text ?? item.summary ?? ""),
    owner,
    note: String(item.note ?? "Python-owned session projection."),
  };
}

function toEvidence(item: WorkbenchEvidenceWire, index: number, stale: boolean): EvidenceItem {
  return {
    id: item.evidence_id || `evidence_${index}`,
    evidenceId: item.evidence_ref,
    kind: stale ? "stale_evidence" : item.source === "codex_adapter" ? "codex_proposal" : "authoritative",
    title: item.label,
    body: item.summary,
    owner: stale ? "slowtask" : item.source === "codex_adapter" ? "codex_proposal" : "event_journal",
    sourceRef: item.evidence_ref,
    planVersion: item.plan_version,
    stale,
  };
}

function toTimelineEvent(
  item: WorkbenchSnapshotWire["timeline"][number],
  index: number,
  router: WorkbenchSnapshotWire["router"],
): SlowTaskTimelineEvent {
  const routerDecision = item.event_name === "ROUTER_DECISION_EMITTED"
    ? asRouterDecision(router?.router_decision)
    : undefined;
  const taskFocus = item.event_name === "ROUTER_DECISION_EMITTED"
    ? asTaskFocus(router?.task_focus)
    : undefined;
  return {
    id: item.event_id || `timeline_${index}`,
    order: item.event_seq,
    title: item.event_name.toLowerCase().replace(/_/g, " "),
    identity: {
      canonical: true,
      eventName: item.event_name as CanonicalDemoEventName,
    },
    owner: ownerFromSource(item.source_module),
    routerDecision,
    taskFocus,
    planVersion: item.plan_version == null ? undefined : readonlyPlanVersion(item.plan_version),
    taskEventSeq: item.task_event_seq == null ? undefined : readonlyTaskEventSeq(item.task_event_seq),
    summary: item.event_name,
    details: item.details,
  };
}

function toPendingConfirmation(value: Readonly<Record<string, unknown>>) {
  return {
    confirmationId: String(value.confirmation_id ?? "confirmation"),
    scope: String(value.scope ?? "TASK_CANCEL") as "TASK_CANCEL",
    planVersion: Number(value.plan_version ?? 1),
    prompt: String(value.prompt ?? "请确认当前操作。"),
    riskSummary: String(value.risk_summary ?? "当前操作需要确认。"),
    status: value.status === "accepted_by_user" ? "accepted_by_user" as const : value.status === "rejected_by_user" ? "rejected_by_user" as const : "pending" as const,
  };
}

function ownerFromSource(source: string): Owner {
  if (source.includes("router")) return "router";
  if (source.includes("tool")) return "tool_executor";
  if (source.includes("patch")) return "user_patch_pipeline";
  if (source.includes("adapter")) return "codex_proposal";
  if (source.includes("interaction") || source.includes("access")) return "event_journal";
  return "slowtask";
}

function asRouterDecision(value: unknown): "FAST_ONLY" | "SPAWN_SLOW_TASK" | "PATCH_ACTIVE_SLOW_TASK" | "IGNORE" {
  return value === "SPAWN_SLOW_TASK" || value === "PATCH_ACTIVE_SLOW_TASK" || value === "IGNORE" ? value : "FAST_ONLY";
}

function asTaskFocus(value: unknown): "ACTIVE_TASK_PATCH" | "FOREGROUND_CHAT" | "NEW_TASK_CANDIDATE" | "CANCEL_OR_PAUSE_CANDIDATE" | "NON_ASSISTANT" | "AMBIGUOUS" {
  return value === "ACTIVE_TASK_PATCH" || value === "NEW_TASK_CANDIDATE" || value === "CANCEL_OR_PAUSE_CANDIDATE" || value === "NON_ASSISTANT" || value === "AMBIGUOUS" ? value : "FOREGROUND_CHAT";
}

function asLifecycle(value: unknown): SlowTaskLifecycleState {
  return value === "CREATED" || value === "WAITING_FOR_SLOT" || value === "PLANNING" || value === "EXECUTING" || value === "WAITING_FOR_USER_CONFIRMATION" || value === "COMPLETED" || value === "CANCELLED" || value === "FAILED" ? value : "CREATED";
}

function asPlanStatus(value: unknown): PlanVersionSnapshot["status"] {
  return value === "superseded" || value === "failed" || value === "completed" ? value : "current";
}

function asPlanReason(value: unknown): PlanVersionSnapshot["reason"] {
  return value === "user_patch" || value === "stale_evidence_adopted" || value === "manual_demo" ? value : "initial_plan";
}
