import type { CodexProposal, InputMatchMode, SlowSystemScenario } from "../slowSystem";

type StatusSidebarProps = Readonly<{
  scenarios: readonly SlowSystemScenario[];
  selectedScenarioId: string | null;
  runScenario: SlowSystemScenario;
  hasRun: boolean;
  loading: boolean;
  matchedBy: InputMatchMode;
  proposal: CodexProposal | null;
  onSelectScenario: (scenarioId: string) => void;
}>;

export function StatusSidebar({
  scenarios,
  selectedScenarioId,
  runScenario,
  hasRun,
  loading,
  matchedBy,
  proposal,
  onSelectScenario,
}: StatusSidebarProps) {
  const staleCount = hasRun ? runScenario.staleEvidence.length : 0;
  const hasConfirmation = hasRun && Boolean(runScenario.slowTask.pendingConfirmation);
  const codexStatus = loading
    ? runScenario.streaming.active
      ? "streaming"
      : "calling"
    : proposal?.status ?? "not_called";

  return (
    <aside className="status-sidebar" aria-label="Workbench status sidebar">
      <div className="sidebar-brand">
        <span className="brand-mark" aria-hidden="true">
          VA
        </span>
        <div>
          <strong>voice-agent</strong>
          <span>slow system</span>
        </div>
      </div>

      <nav className="scenario-nav" aria-label="Mock scenario selector">
        <p className="sidebar-label">Demo scenarios</p>
        {scenarios.map((scenario) => (
          <button
            key={scenario.id}
            className="scenario-button"
            type="button"
            aria-label={scenario.shortName}
            aria-pressed={scenario.id === selectedScenarioId}
            onClick={() => onSelectScenario(scenario.id)}
          >
            <span>{scenario.shortName}</span>
            <small>{scenario.demoAction.replace(/_/g, " ")}</small>
          </button>
        ))}
      </nav>

      <div className="sidebar-status" aria-label="Current workbench status">
        <p className="sidebar-label">Current state</p>
        <StatusRow label="matched_by" value={hasRun ? matchedBy : "not_run"} tone="neutral" />
        <StatusRow label="router" value={hasRun ? runScenario.routerDecision : "pending"} tone="blue" />
        <StatusRow label="task_focus" value={hasRun ? runScenario.taskFocus : "pending"} tone="violet" />
        <StatusRow
          label="lifecycle"
          value={hasRun ? runScenario.slowTask.lifecycleState : "not_started"}
          tone="green"
        />
        <StatusRow
          label="plan_version"
          value={hasRun ? `v${runScenario.slowTask.currentPlanVersion.value}` : "-"}
          tone="green"
        />
        <StatusRow
          label="task_event_seq"
          value={hasRun ? `#${runScenario.slowTask.currentTaskEventSeq.value}` : "-"}
          tone="neutral"
        />
        <StatusRow
          label="stale_evidence"
          value={staleCount > 0 ? `${staleCount} item` : "none"}
          tone={staleCount > 0 ? "amber" : "neutral"}
        />
        <StatusRow
          label="confirmation"
          value={hasConfirmation ? "pending" : "none"}
          tone={hasConfirmation ? "amber" : "neutral"}
        />
        <StatusRow label="codex" value={codexStatus} tone="blue" />
        <StatusRow
          label="codex_backend"
          value={proposal?.backendMode ?? "-"}
          tone={proposal ? "blue" : "neutral"}
        />
        <StatusRow
          label="live_steps"
          value={runScenario.liveProgress.length > 0 ? String(runScenario.liveProgress.length) : "-"}
          tone={runScenario.streaming.active ? "blue" : "neutral"}
        />
        <StatusRow
          label="proposal_type"
          value={proposal?.proposalType ?? "-"}
          tone={proposal ? "violet" : "neutral"}
        />
      </div>

      <div className="sidebar-boundary" aria-label="Ownership boundary">
        <p className="sidebar-label">Ownership</p>
        <span>React owns UI controls</span>
        <span>Router owns classification</span>
        <span>SlowTask owns facts</span>
        <span>Codex owns proposals</span>
      </div>
    </aside>
  );
}

type StatusTone = "neutral" | "blue" | "green" | "amber" | "violet";

type StatusRowProps = Readonly<{
  label: string;
  value: string;
  tone: StatusTone;
}>;

function StatusRow({ label, value, tone }: StatusRowProps) {
  return (
    <div className="status-row" data-tone={tone}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
