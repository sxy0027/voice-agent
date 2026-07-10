import type {
  CodexProposal,
  CodexProposalSafety,
  ProposalStatus,
  ProposalType,
  SlowSystemScenario,
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
  capability: Readonly<{
    output_mode: "mock" | "real" | "degraded";
    provider: string;
    health_status: string;
  }>;
}>;

export type RequestCodexProposalInput = Readonly<{
  scenario: SlowSystemScenario;
  intent: string;
  providerMode: CodexProviderMode;
  allowLocalCodexCli: boolean;
}>;

export async function requestCodexProposal({
  scenario,
  intent,
  providerMode,
  allowLocalCodexCli,
}: RequestCodexProposalInput): Promise<CodexProposal> {
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
    }),
  });

  if (!response.ok) {
    throw new Error(`Codex proposal backend returned ${response.status}`);
  }

  const payload = (await response.json()) as BackendResponse;
  return fromBackendProposal(payload.proposal, providerMode, payload.capability.output_mode);
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
