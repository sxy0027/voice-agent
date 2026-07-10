import type { CodexProposal, InputMatchMode, SlowSystemScenario } from "../slowSystem";

type ConversationPanelProps = Readonly<{
  runScenario: SlowSystemScenario;
  hasRun: boolean;
  inputText: string;
  matchedBy: InputMatchMode;
  proposal: CodexProposal | null;
  proposalError: string | null;
  loading: boolean;
  onInputTextChange: (value: string) => void;
  onRunMockInput: () => void;
}>;

export function ConversationPanel({
  runScenario,
  hasRun,
  inputText,
  matchedBy,
  proposal,
  proposalError,
  loading,
  onInputTextChange,
  onRunMockInput,
}: ConversationPanelProps) {
  return (
    <section className="conversation-panel" aria-labelledby="conversation-heading">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Active mock</p>
          <h2 id="conversation-heading">{hasRun ? runScenario.title : "等待输入并运行"}</h2>
        </div>
        <span className="status-pill">{hasRun ? runScenario.slowTask.lifecycleState : "not_run"}</span>
      </div>

      <p className="scenario-summary">
        {hasRun
          ? runScenario.summary
          : "选择左侧 demo 只会填充输入框；点击运行后才会生成 Router 分类、SlowTask 状态和 Codex 分析。"}
      </p>

      <div className="composer-box">
        <label className="input-label" htmlFor="mock-input">
          输入 mocklist 里的用户/工具消息
        </label>
        <textarea
          id="mock-input"
          className="mock-input"
          value={inputText}
          onChange={(event) => onInputTextChange(event.target.value)}
          rows={4}
        />
        <div className="composer-actions">
          <span data-match={matchedBy}>matched_by: {hasRun ? matchedBy : "not_run"}</span>
          <button className="run-button" type="button" disabled={loading} onClick={onRunMockInput}>
            {loading ? "Running Router + Codex..." : "Run Router + Codex analysis"}
          </button>
        </div>
      </div>

      {hasRun ? (
        <div className="router-summary" aria-label="Router summary">
          <div>
            <span>RouterDecision</span>
            <strong>{runScenario.routerDecision}</strong>
          </div>
          <div>
            <span>TaskFocus</span>
            <strong>{runScenario.taskFocus}</strong>
          </div>
          <div>
            <span>Active task</span>
            <p>{runScenario.activeTaskContext}</p>
          </div>
          <div>
            <span>Router rule</span>
            <p>{runScenario.routerRule}</p>
          </div>
        </div>
      ) : (
        <div className="proposal-empty">
          <strong>Router / SlowTask not run</strong>
          <p>侧栏和右侧 facts 会在运行后根据本次输入以及 Codex bridge 返回结果更新。</p>
        </div>
      )}

      <div className="turn-list">
        {hasRun
          ? runScenario.conversation
          .filter((turn) => turn.speaker !== "assistant_fast")
          .map((turn) => (
          <article className={`turn turn-${turn.speaker}`} key={turn.id}>
            <div className="turn-meta">
              <span>{labelForSpeaker(turn.speaker)}</span>
              <span>owner: {turn.owner}</span>
            </div>
            <p>{turn.text}</p>
            <small>{turn.note}</small>
          </article>
        ))
          : null}
        <CodexAnswerTurn proposal={proposal} loading={loading} error={proposalError} />
      </div>
    </section>
  );
}

type CodexAnswerTurnProps = Readonly<{
  proposal: CodexProposal | null;
  loading: boolean;
  error: string | null;
}>;

function CodexAnswerTurn({ proposal, loading, error }: CodexAnswerTurnProps) {
  if (loading) {
    return (
      <article className="turn turn-system">
        <div className="turn-meta">
          <span>Codex bridge</span>
          <span>calling backend provider</span>
        </div>
        <p>正在通过后端 bridge 调用 Codex，返回前不会显示分析结果。</p>
        <small>浏览器不直接调用 Codex；请求走本地 Vite endpoint 和 Python adapter bridge。</small>
      </article>
    );
  }

  if (error) {
    return (
      <article className="turn turn-system">
        <div className="turn-meta">
          <span>Codex bridge</span>
          <span>failed closed</span>
        </div>
        <p>{error}</p>
        <small>没有拿到后端返回前，页面不会展示静态 Codex 分析。</small>
      </article>
    );
  }

  if (!proposal) {
    return (
      <article className="turn turn-system turn-placeholder">
        <div className="turn-meta">
          <span>Codex bridge</span>
          <span>not called</span>
        </div>
        <p>点击运行后才会调用 Codex；这里不会预先显示静态回答。</p>
        <small>默认 provider 是 Local Codex CLI。</small>
      </article>
    );
  }

  return (
    <article className="turn turn-assistant_fast">
      <div className="turn-meta">
        <span>Codex analysis</span>
        <span>backend: {proposal.backendMode}</span>
      </div>
      <p>{proposal.summary}</p>
      {proposal.suggestedNextSteps.length > 0 ? (
        <ul className="codex-answer-list">
          {proposal.suggestedNextSteps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ul>
      ) : null}
      <small>{proposal.boundaryWarning}</small>
    </article>
  );
}

function labelForSpeaker(speaker: SlowSystemScenario["conversation"][number]["speaker"]) {
  if (speaker === "user") {
    return "User";
  }
  if (speaker === "tool") {
    return "Tool result";
  }
  if (speaker === "system") {
    return "System";
  }
  return "Workbench answer";
}
