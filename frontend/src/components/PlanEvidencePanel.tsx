import type {
  CodexProposal,
  EvidenceItem,
  EvidenceKind,
  SlowSystemScenario,
  SlowTaskTimelineEvent,
} from "../slowSystem";

type PlanEvidencePanelProps = Readonly<{
  scenario: SlowSystemScenario;
  selectedEvent: SlowTaskTimelineEvent;
  hasRun: boolean;
  loading: boolean;
  proposal: CodexProposal | null;
  sessionId?: string | null;
  onConfirm?: (accepted: boolean) => void;
}>;

const evidenceHeading: Record<EvidenceKind, string> = {
  authoritative: "Authoritative evidence",
  non_authoritative_hypothesis: "Non-authoritative hypothesis",
  untrusted_web_evidence: "Untrusted web evidence",
  stale_evidence: "Stale evidence bucket",
  codex_proposal: "Codex proposal evidence",
};

export function PlanEvidencePanel({
  scenario,
  selectedEvent,
  hasRun,
  loading,
  proposal,
  sessionId,
  onConfirm,
}: PlanEvidencePanelProps) {
  const currentEvidence = scenario.evidence.filter((item) => !item.stale);
  const evidenceByKind = groupEvidenceByKind(currentEvidence);
  const readiness = scenario.slowTask.readiness ?? {
    search: false,
    plan: false,
    commitment: false,
  };
  const slotSummary = scenario.slowTask.slotSummary ?? [];

  return (
    <section className="panel evidence-panel" aria-labelledby="evidence-heading">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Plan / Evidence</p>
          <h2 id="evidence-heading">SlowTask Facts</h2>
        </div>
      </div>

      {!hasRun ? (
        <div className="proposal-empty">
          <strong>No SlowTask facts yet</strong>
          <p>这里不会预设默认场景。点击运行并等待 Codex bridge 返回后，右侧 facts 才会随本次分析更新。</p>
        </div>
      ) : null}

      {hasRun ? (
        <>
      <div className="codex-sync-note" data-state={loading ? "loading" : proposal ? "ready" : "pending"}>
        <strong>Codex sync</strong>
        <p>
          {loading
            ? "Codex bridge 正在执行；中央区域会实时显示阶段、工具状态和校验进度，右侧 facts 仍由 SlowTask snapshot 拥有。"
            : proposal
              ? `Codex 已返回 ${proposal.status} / ${proposal.backendMode} / ${proposal.proposalType}；下方 facts 随本次运行展示，但 proposal 仍不是 SlowTask fact。`
              : "Router / SlowTask 已运行；等待 Codex proposal 结果。"}
        </p>
      </div>

      <dl className="snapshot-grid">
        <div>
          <dt>task_id</dt>
          <dd>{scenario.slowTask.taskId}</dd>
        </div>
        <div>
          <dt>lifecycle_state</dt>
          <dd>{scenario.slowTask.lifecycleState}</dd>
        </div>
        <div>
          <dt>current_plan_version</dt>
          <dd>
            {scenario.slowTask.currentPlanVersion.value} (
            {scenario.slowTask.currentPlanVersion.label})
          </dd>
        </div>
        <div>
          <dt>current_task_event_seq</dt>
          <dd>
            {scenario.slowTask.currentTaskEventSeq.value} (
            {scenario.slowTask.currentTaskEventSeq.label})
          </dd>
        </div>
        <div>
          <dt>SemanticCommitment</dt>
          <dd>not emitted yet; owner remains SlowTask</dd>
        </div>
      </dl>

      <div className="policy-note">
        <strong>SlowTask processing</strong>
        <p>{scenario.slowTask.processingSummary}</p>
      </div>

      <div className="readiness-strip" aria-label="SlowTask readiness">
        <span data-ready={readiness.search}>search</span>
        <span data-ready={readiness.plan}>plan</span>
        <span data-ready={readiness.commitment}>commitment</span>
      </div>

      {scenario.slowTask.clarification ? (
        <div className="policy-note">
          <strong>Current clarification</strong>
          <p>
            blocked_stage={scenario.slowTask.clarification.blockedStage};
            reason={scenario.slowTask.clarification.reason};
            attempt={scenario.slowTask.clarification.attempt};
            ask_fields={scenario.slowTask.clarification.askFields.join(", ")}
          </p>
        </div>
      ) : null}

      {slotSummary.length > 0 ? (
        <div className="slot-ledger">
          <h3>Slot ledger</h3>
          <div className="slot-ledger-list">
            {slotSummary.map((slot) => (
              <article className="slot-ledger-item" data-state={slot.state} key={slot.name}>
                <div>
                  <strong>{slot.label}</strong>
                  <span>{slot.state}</span>
                </div>
                <p>{slot.valuePreview || "待补充"}</p>
                <small>
                  source={slot.source}; asked={slot.askedCount}
                  {slot.requiredFor.length > 0 ? `; required_for=${slot.requiredFor.join(", ")}` : ""}
                </small>
              </article>
            ))}
          </div>
        </div>
      ) : null}

      <div className="policy-note">
        <strong>Stale evidence policy</strong>
        <p>{scenario.slowTask.staleEvidencePolicy}</p>
      </div>

      {scenario.slowTask.pendingConfirmation ? (
        <div className="confirmation-box">
          <strong>Pending confirmation</strong>
          <p>{scenario.slowTask.pendingConfirmation.prompt}</p>
          <dl>
            <div>
              <dt>scope</dt>
              <dd>{scenario.slowTask.pendingConfirmation.scope}</dd>
            </div>
            <div>
              <dt>status</dt>
              <dd>{scenario.slowTask.pendingConfirmation.status}</dd>
            </div>
            <div>
              <dt>risk</dt>
              <dd>{scenario.slowTask.pendingConfirmation.riskSummary}</dd>
            </div>
          </dl>
          {sessionId && onConfirm ? (
            <div className="confirmation-actions">
              <button type="button" disabled={loading} onClick={() => onConfirm(true)}>
                Confirm cancellation
              </button>
              <button type="button" disabled={loading} onClick={() => onConfirm(false)}>
                Keep task
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="selected-event-note">
        <span>Selected timeline item</span>
        <strong>
          {selectedEvent.identity.canonical
            ? selectedEvent.identity.eventName
            : selectedEvent.identity.displayLabel}
        </strong>
      </div>

      <div className="plan-version-list">
        <h3>Plan versions</h3>
        {scenario.slowTask.planVersions.map((plan) => (
          <article className="plan-version-item" data-status={plan.status} key={plan.planVersion}>
            <strong>plan_version={plan.planVersion}</strong>
            <span>{plan.status}</span>
            <p>{plan.summary}</p>
            <small>
              reason={plan.reason}; created_by={plan.createdByEventId}
            </small>
          </article>
        ))}
      </div>

      <EvidenceGroup title={evidenceHeading.authoritative} items={evidenceByKind.authoritative} />
      <EvidenceGroup
        title={evidenceHeading.non_authoritative_hypothesis}
        items={evidenceByKind.non_authoritative_hypothesis}
      />
      <EvidenceGroup
        title={evidenceHeading.untrusted_web_evidence}
        items={evidenceByKind.untrusted_web_evidence}
      />
      <EvidenceGroup title={evidenceHeading.codex_proposal} items={evidenceByKind.codex_proposal} />
      <EvidenceGroup title={evidenceHeading.stale_evidence} items={scenario.staleEvidence} />
        </>
      ) : null}
    </section>
  );
}

type EvidenceGroupProps = Readonly<{
  title: string;
  items: readonly EvidenceItem[];
}>;

function groupEvidenceByKind(items: readonly EvidenceItem[]): Record<EvidenceKind, EvidenceItem[]> {
  return items.reduce<Record<EvidenceKind, EvidenceItem[]>>(
    (groups, item) => {
      groups[item.kind].push(item);
      return groups;
    },
    {
      authoritative: [],
      non_authoritative_hypothesis: [],
      untrusted_web_evidence: [],
      stale_evidence: [],
      codex_proposal: [],
    },
  );
}

function EvidenceGroup({ title, items }: EvidenceGroupProps) {
  if (items.length === 0) {
    return null;
  }

  return (
    <div className="evidence-group">
      <h3>{title}</h3>
      <div className="evidence-list">
        {items.map((item) => (
          <article className={`evidence-item evidence-${item.kind}`} key={item.id}>
            <div className="evidence-title-row">
              <strong>{item.title}</strong>
              <span>{trustLabel(item.kind)} · plan_version={item.planVersion}</span>
            </div>
            <p>{item.body}</p>
            <small>
              {item.sourceRef}; stale={String(item.stale)}
            </small>
          </article>
        ))}
      </div>
    </div>
  );
}

function trustLabel(kind: EvidenceKind) {
  if (kind === "authoritative") {
    return "trust=authoritative";
  }
  if (kind === "non_authoritative_hypothesis") {
    return "trust=hypothesis";
  }
  if (kind === "untrusted_web_evidence") {
    return "trust=untrusted_web_evidence";
  }
  if (kind === "codex_proposal") {
    return "trust=codex_proposal";
  }
  return "trust=stale_evidence";
}
