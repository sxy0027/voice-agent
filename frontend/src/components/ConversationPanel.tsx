import type {
  CodexProposal,
  ConversationTurn,
  InputMatchMode,
  SlowSystemScenario,
} from "../slowSystem";
import { CodexProgressFeed } from "./CodexProgressFeed";

type ConversationPanelProps = Readonly<{
  runScenario: SlowSystemScenario;
  hasRun: boolean;
  inputText: string;
  matchedBy: InputMatchMode;
  proposal: CodexProposal | null;
  proposalError: string | null;
  loading: boolean;
  transcriptTurns: readonly ConversationTurn[];
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
  transcriptTurns,
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
          : "选择左侧 demo 只会填充输入框；发送后会追加到同一条聊天记录，并生成 Router 分类、SlowTask 状态和 Codex 分析。"}
      </p>

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
        {transcriptTurns.map((turn) => (
          <article className={`turn turn-${turn.speaker}`} key={turn.id}>
            <div className="turn-meta">
              <span>{labelForSpeaker(turn.speaker)}</span>
              <span>owner: {turn.owner}</span>
            </div>
            <p>{turn.text}</p>
            <small>{turn.note}</small>
          </article>
        ))}
        <CodexProgressFeed scenario={runScenario} loading={loading} />
        <CodexAnswerTurn proposal={proposal} loading={loading} error={proposalError} />
      </div>

      <div className="composer-box">
        <label className="input-label" htmlFor="mock-input">
          输入下一条用户/工具消息
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
            {loading ? "Running Router + Codex..." : "Send message"}
          </button>
        </div>
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
        <p>正在通过后端 bridge 调用 Codex；上方会持续显示安全的阶段和工具进度。</p>
        <small>浏览器不直接调用 Codex；请求走本地 Vite endpoint、SSE 和 Python adapter bridge。</small>
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

  return null;
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
