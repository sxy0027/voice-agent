import type { CodexProviderMode } from "../adapters/codexProposalClient";
import type { CodexProposal } from "../slowSystem";

type CodexProposalPanelProps = Readonly<{
  proposal: CodexProposal | null;
  providerMode: CodexProviderMode;
  allowLocalCodexCli: boolean;
  loading: boolean;
  error: string | null;
  onProviderModeChange: (mode: CodexProviderMode) => void;
  onAllowLocalCodexCliChange: (enabled: boolean) => void;
  onRequestProposal: () => void;
  onMarkProposal: (status: "accepted" | "rejected") => void;
}>;

export function CodexProposalPanel({
  proposal,
  providerMode,
  allowLocalCodexCli,
  loading,
  error,
  onProviderModeChange,
  onAllowLocalCodexCliChange,
  onRequestProposal,
  onMarkProposal,
}: CodexProposalPanelProps) {
  const providerLabel =
    providerMode === "codex_cli_local"
      ? "Local Codex CLI"
      : providerMode === "fake"
        ? "Python fake provider"
        : "unavailable provider";

  return (
    <section className="panel codex-panel" aria-labelledby="codex-heading">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Codex proposal bridge</p>
          <h2 id="codex-heading">Draft Helper</h2>
        </div>
        <span className="badge proposal">{proposal?.label ?? "Waiting for Codex"}</span>
      </div>

      <div className="codex-controls">
        <label>
          Provider
          <select
            value={providerMode}
            onChange={(event) => onProviderModeChange(event.target.value as CodexProviderMode)}
          >
            <option value="codex_cli_local">Local Codex CLI</option>
            <option value="fake">Python fake provider (manual fallback)</option>
            <option value="codex_cli_unavailable">Unavailable mode</option>
          </select>
        </label>
        <label className="checkbox-row">
          <input
            checked={allowLocalCodexCli}
            disabled={providerMode !== "codex_cli_local"}
            type="checkbox"
            onChange={(event) => onAllowLocalCodexCliChange(event.target.checked)}
          />
          explicit local Codex CLI opt-in
        </label>
        <button className="run-button" type="button" disabled={loading} onClick={onRequestProposal}>
          {loading ? "Requesting..." : "Request backend proposal"}
        </button>
      </div>

      {error ? <p className="proposal-error">{error}</p> : null}

      {loading ? (
        <div className="proposal-empty" aria-live="polite">
          <strong>Calling Codex...</strong>
          <p>正在通过后端 bridge 请求 {providerLabel}；返回前不会展示静态 proposal。</p>
        </div>
      ) : null}

      {!loading && !proposal ? (
        <div className="proposal-empty">
          <strong>No Codex result yet</strong>
          <p>点击中间的运行按钮后才会显示 Codex 返回的分析。页面不会预置 fake 回答。</p>
        </div>
      ) : null}

      {proposal ? (
        <>
      <p className="proposal-summary">{proposal.summary}</p>
      <p className="proposal-warning">{proposal.boundaryWarning}</p>

      <dl className="proposal-facts">
        <div>
          <dt>status</dt>
          <dd>{proposal.status}</dd>
        </div>
        <div>
          <dt>proposal_type</dt>
          <dd>{proposal.proposalType}</dd>
        </div>
        <div>
          <dt>backend_mode</dt>
          <dd>{proposal.backendMode}</dd>
        </div>
        <div>
          <dt>owner</dt>
          <dd>{proposal.owner}</dd>
        </div>
      </dl>

      <div className="proposal-items">
        {proposal.suggestedNextSteps.map((step) => (
          <article className="proposal-item" key={step}>
            <div className="proposal-item-static">
              <span>{step}</span>
            </div>
          </article>
        ))}
      </div>

      {proposal.missingFields.length > 0 ? (
        <div className="source-ref-box">
          <strong>missing_fields</strong>
          {proposal.missingFields.map((field) => (
            <code key={field}>{field}</code>
          ))}
        </div>
      ) : null}

      <div className="source-ref-box">
        <strong>source_evidence_refs</strong>
        {proposal.sourceEvidenceRefs.map((ref) => (
          <code key={ref}>{ref}</code>
        ))}
      </div>

      <div className="safety-grid" aria-label="Codex safety flags">
        {Object.entries(proposal.safety).map(([key, value]) => (
          <div key={key}>
            <span>{key}</span>
            <strong>{String(value)}</strong>
          </div>
        ))}
      </div>

      <div className="proposal-actions">
        <button type="button" onClick={() => onMarkProposal("accepted")}>
          Accept proposal for demo
        </button>
        <button type="button" onClick={() => onMarkProposal("rejected")}>
          Reject proposal
        </button>
      </div>
        </>
      ) : null}
    </section>
  );
}
