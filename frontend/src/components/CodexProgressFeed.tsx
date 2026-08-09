import type { SlowSystemScenario, WorkbenchProgressWire } from "../slowSystem";

type ProgressDetail = readonly [string, string];

type CodexProgressFeedProps = Readonly<{
  scenario: SlowSystemScenario;
  loading: boolean;
  hasRun: boolean;
}>;

export function CodexProgressFeed({ scenario, loading, hasRun }: CodexProgressFeedProps) {
  const progress = scenario.liveProgress.slice(-14);
  const streaming = scenario.streaming;
  const visible = hasRun && (loading || progress.length > 0);

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
          <p className="panel-kicker">公开执行记录</p>
          <h3 id="live-progress-heading">Codex 执行过程</h3>
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
        这里按顺序展示可审计的阶段、工具名称、输入/输出摘要与下一步；不会展示隐藏思维链、原始 provider body 或凭据。
      </small>
    </section>
  );
}

function ProgressRow({ item }: Readonly<{ item: WorkbenchProgressWire }>) {
  const label = item.label || fallbackLabel(item.kind);
  const metadata = [
    item.phase,
    item.status,
    item.orchestration_role ? `role=${item.orchestration_role}` : null,
    item.role ? `role=${item.role}` : null,
    item.subtask_id ? `subtask=${item.subtask_id}` : null,
    item.tool_name ? `tool=${item.tool_name}` : null,
    item.blocked_on_user ? "blocked_on_user=true" : null,
  ].filter(Boolean);
  const expanded: ProgressDetail[] = [];
  if (item.subtask_goal) expanded.push(["目标", item.subtask_goal]);
  if (item.public_thought) expanded.push(["公开执行摘要", item.public_thought]);
  if (item.tool_input_summary) expanded.push(["工具输入", item.tool_input_summary]);
  if (item.tool_output_summary) expanded.push(["工具输出", item.tool_output_summary]);
  if (item.next_step) expanded.push(["下一步", item.next_step]);
  if (item.public_summary) expanded.push(["角色结果", item.public_summary]);
  if (item.context_hash) expanded.push(["上下文哈希", item.context_hash]);
  if (item.validation_status) expanded.push(["校验", item.validation_status]);
  if (item.degraded_reason) expanded.push(["降级原因", item.degraded_reason]);
  if (item.next_role) expanded.push(["下一角色", item.next_role]);
  return (
    <li className="live-progress-item">
      <span className="live-progress-index">{String(item.sequence).padStart(2, "0")}</span>
      <span className="live-progress-body">
        <strong>{label}</strong>
        <span className="live-progress-meta">{metadata.join(" · ")}</span>
        {item.detail ? <small>{item.detail}</small> : null}
        {expanded.length > 0 ? (
          <dl className="live-progress-details">
            {expanded.map(([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
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
