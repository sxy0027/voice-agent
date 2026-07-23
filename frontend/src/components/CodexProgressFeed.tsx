import type { SlowSystemScenario, WorkbenchProgressWire } from "../slowSystem";

type CodexProgressFeedProps = Readonly<{
  scenario: SlowSystemScenario;
  loading: boolean;
}>;

export function CodexProgressFeed({ scenario, loading }: CodexProgressFeedProps) {
  const progress = scenario.liveProgress.slice(-14);
  const streaming = scenario.streaming;
  const visible = loading || progress.length > 0;

  if (!visible) {
    return null;
  }

  return (
    <section
      className="live-progress-panel"
      data-active={streaming.active ? "true" : "false"}
      aria-labelledby="live-progress-heading"
      aria-live="polite"
    >
      <div className="live-progress-heading">
        <div>
          <p className="panel-kicker">Live execution</p>
          <h3 id="live-progress-heading">Codex 执行进度</h3>
        </div>
        <span className="live-progress-state">
          <span className="live-progress-dot" aria-hidden="true" />
          {streaming.active ? "streaming" : "completed"}
        </span>
      </div>

      <div className="live-progress-current">
        <strong>{streaming.label || "等待执行进度"}</strong>
        {streaming.detail ? <span>{streaming.detail}</span> : null}
      </div>

      {progress.length > 0 ? (
        <ol className="live-progress-list">
          {progress.map((item) => (
            <ProgressRow item={item} key={`${item.trace_id}_${item.sequence}`} />
          ))}
        </ol>
      ) : (
        <p className="live-progress-empty">正在连接本地 Codex CLI…</p>
      )}

      <small className="live-progress-safety">
        这里显示的是 allow-listed 的阶段、工具名称和校验状态；不会展示隐藏思维、原始 provider body 或凭据。
      </small>
    </section>
  );
}

function ProgressRow({ item }: Readonly<{ item: WorkbenchProgressWire }>) {
  const label = item.label || fallbackLabel(item.kind);
  const metadata = [item.phase, item.status, item.tool_name ? `tool=${item.tool_name}` : null].filter(Boolean);
  return (
    <li className="live-progress-item">
      <span className="live-progress-index">{String(item.sequence).padStart(2, "0")}</span>
      <span className="live-progress-body">
        <strong>{label}</strong>
        <span className="live-progress-meta">{metadata.join(" · ")}</span>
        {item.detail ? <small>{item.detail}</small> : null}
      </span>
    </li>
  );
}

function fallbackLabel(kind: string): string {
  const labels: Record<string, string> = {
    request_started: "已建立 Codex CLI 请求",
    "thread.started": "Codex 已启动本地执行线程",
    "turn.started": "Codex 正在分析当前任务",
    "item.started": "Codex 开始处理一个步骤",
    "item.completed": "Codex 完成一个步骤",
    provider_completed: "Codex CLI 已返回结构化候选",
    structured_output_emitted: "结构化输出已通过校验",
    provider_degraded: "Codex 暂不可用，已切换到受控 fallback",
  };
  return labels[kind] ?? "Codex provider 进度已更新";
}
