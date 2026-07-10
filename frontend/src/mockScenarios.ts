import type {
  CanonicalDemoEventName,
  CodexProposal,
  DemoAction,
  EvidenceItem,
  InputMatchMode,
  PlanVersionReadOnly,
  ProposalType,
  SlowSystemScenario,
  SlowTaskLifecycleState,
  SlowTaskTimelineEvent,
  TaskEventSeqReadOnly,
} from "./slowSystem";

const readonlyPlanVersion = (value: number): PlanVersionReadOnly => ({
  kind: "plan_version",
  value,
  owner: "slowtask",
  source: "SlowTask mock event",
  label: "read-only / owned by SlowTask",
});

const readonlyTaskEventSeq = (value: number): TaskEventSeqReadOnly => ({
  kind: "task_event_seq",
  value,
  owner: "slowtask",
  source: "SlowTask mock event",
  label: "read-only / owned by SlowTask",
});

const proposal = (
  id: string,
  proposalType: ProposalType,
  summary: string,
  sourceEvidenceRefs: readonly string[],
  suggestedNextSteps: readonly string[],
): CodexProposal => ({
  proposalId: id,
  owner: "codex_proposal",
  proposalType,
  status: "draft",
  label: "Proposal only",
  summary,
  suggestedNextSteps,
  missingFields: proposalType === "clarification" ? ["explicit_user_confirmation"] : [],
  requiresConfirmation: proposalType === "clarification" || proposalType === "tool_preview",
  riskNotes: [
    "Codex proposal only — not committed to SlowTask.",
    "Codex 不推进 plan_version，不写 Event Journal，不授权工具。",
  ],
  sourceEvidenceRefs,
  safety: {
    codex_is_fact_owner: false,
    advances_plan_version: false,
    authorizes_tool: false,
    contains_secret: false,
    mutates_task_snapshot: false,
    emits_canonical_event: false,
    executes_external_tool: false,
    contains_raw_provider_body: false,
  },
  backendMode: "static_mock",
  boundaryWarning:
    "Codex 只能生成 proposal；只有 SlowTask / Event Journal 接受、校验、记录后才可能成为事实。",
});

const evidence = (
  id: string,
  title: string,
  body: string,
  planVersion: number,
  sourceRef: string,
  kind: EvidenceItem["kind"] = "authoritative",
  stale = false,
): EvidenceItem => ({
  id,
  evidenceId: sourceRef,
  kind,
  title,
  body,
  owner: stale ? "slowtask" : kind === "non_authoritative_hypothesis" ? "router" : "event_journal",
  sourceRef,
  planVersion,
  stale,
});

const event = (
  order: number,
  eventName: CanonicalDemoEventName,
  summary: string,
  details: readonly string[],
  options: Partial<SlowTaskTimelineEvent> = {},
): SlowTaskTimelineEvent => ({
  id: `${String(order).padStart(2, "0")}_${String(eventName).toLowerCase()}`,
  order,
  title: String(eventName)
    .toLowerCase()
    .replace(/_/g, " "),
  identity: {
    canonical: true,
    eventName,
  },
  owner: "slowtask",
  summary,
  details,
  ...options,
});

type BuildScenarioInput = Readonly<{
  id: string;
  title: string;
  shortName: string;
  demoAction: DemoAction;
  mockInput: string;
  matchKeywords: readonly string[];
  summary: string;
  routerDecision: SlowSystemScenario["routerDecision"];
  taskFocus: SlowSystemScenario["taskFocus"];
  routerRule: string;
  activeTaskContext: string;
  lifecycleState: SlowTaskLifecycleState;
  currentPlanVersion: number;
  currentTaskEventSeq: number;
  planVersions: SlowSystemScenario["slowTask"]["planVersions"];
  timeline: readonly SlowTaskTimelineEvent[];
  evidence: readonly EvidenceItem[];
  staleEvidence?: readonly EvidenceItem[];
  codexProposal: CodexProposal;
  proposalType: ProposalType;
  answer: string;
  pendingConfirmation?: SlowSystemScenario["slowTask"]["pendingConfirmation"];
}>;

const buildScenario = (input: BuildScenarioInput): SlowSystemScenario => ({
  id: input.id,
  title: input.title,
  shortName: input.shortName,
  demoAction: input.demoAction,
  mockInput: input.mockInput,
  matchKeywords: input.matchKeywords,
  summary: input.summary,
  conversation: [
    {
      id: `${input.id}_user`,
      speaker: input.demoAction === "receive_late_tool_result" ? "tool" : "user",
      text: input.mockInput,
      owner: input.demoAction === "receive_late_tool_result" ? "tool_executor" : "event_journal",
      note:
        input.demoAction === "receive_late_tool_result"
          ? "这是 Tool Executor 返回的 mock result，不是用户 turn。"
          : "用户输入先进入 turn ingress / Event Journal；React 只负责显示和选择 mock。",
    },
    {
      id: `${input.id}_assistant`,
      speaker: "assistant_fast",
      text: input.answer,
      owner: "composer",
      note: "这是 mock answer，用来解释 SlowTask 会如何处理；不是 SemanticCommitment。",
    },
  ],
  routerDecision: input.routerDecision,
  taskFocus: input.taskFocus,
  routerRule: input.routerRule,
  activeTaskContext: input.activeTaskContext,
  slowTask: {
    taskId: "slowtask_visit_demo_001",
    owner: "slowtask",
    lifecycleState: input.lifecycleState,
    currentPlanVersion: readonlyPlanVersion(input.currentPlanVersion),
    currentTaskEventSeq: readonlyTaskEventSeq(input.currentTaskEventSeq),
    planVersions: input.planVersions,
    pendingConfirmation: input.pendingConfirmation,
    semanticCommitmentStatus: "not_emitted_yet",
    staleEvidencePolicy:
      "旧 plan_version 的 ToolResult 默认进入 stale_evidence；只有 SlowTask 显式 adopt/rebase 后才可复用。",
    processingSummary: input.answer,
  },
  timeline: input.timeline,
  evidence: input.evidence,
  staleEvidence: input.staleEvidence ?? [],
  codexProposal: input.codexProposal,
  proposalType: input.proposalType,
  answer: input.answer,
});

export const slowSystemScenarios: readonly SlowSystemScenario[] = [
  buildScenario({
    id: "demo_01_new_complex_task",
    title: "场景 1：创建新的复杂任务",
    shortName: "1 新任务",
    demoAction: "start_new_task",
    mockInput: "帮我规划一个两天的客户来访行程，地点尽量靠近公司。",
    matchKeywords: ["规划", "客户", "来访", "行程", "靠近公司"],
    summary: "无 active SlowTask 时，复杂任务被 Router 分类为 NEW_TASK_CANDIDATE 并触发 SPAWN_SLOW_TASK。",
    routerDecision: "SPAWN_SLOW_TASK",
    taskFocus: "NEW_TASK_CANDIDATE",
    routerRule:
      "ADR-006：无 active task 且输入是复杂任务时，task_focus=NEW_TASK_CANDIDATE，RouterDecision=SPAWN_SLOW_TASK。",
    activeTaskContext: "当前没有 active SlowTask。",
    lifecycleState: "WAITING_FOR_SLOT",
    currentPlanVersion: 1,
    currentTaskEventSeq: 6,
    planVersions: [
      {
        planVersion: 1,
        status: "current",
        summary: "初始计划：两天客户来访行程，地点靠近公司；缺少预算和时间偏好。",
        reason: "initial_plan",
        createdByEventId: "evt_demo_01_planning_started",
      },
    ],
    evidence: [
      evidence(
        "demo_01_user_turn",
        "用户复杂任务",
        "用户请求规划两天客户来访行程，并给出地点靠近公司的约束。",
        1,
        "evidence://demo/01/user-turn",
      ),
      evidence(
        "demo_01_router",
        "Router classification",
        "Router 只做门控，不解释最终行程；分类为 NEW_TASK_CANDIDATE。",
        1,
        "evidence://demo/01/router-decision",
      ),
      evidence(
        "demo_01_hypothesis",
        "缺槽假设",
        "可能需要预算和时间偏好，属于 SlowTask 待确认的信息。",
        1,
        "hypothesis://demo/01/missing-budget-time",
        "non_authoritative_hypothesis",
      ),
    ],
    timeline: [
      event(1, "TURN_INGRESS_COMMITTED", "用户 turn 被提交。", [
        "这是可 replay 的输入事实。",
      ], { owner: "event_journal" }),
      event(2, "ROUTER_DECISION_EMITTED", "Router 输出 SPAWN_SLOW_TASK。", [
        "RouterDecision 使用已有枚举。",
        "Router 不创建 SlowTask，也不推进 plan_version。",
      ], {
        owner: "router",
        routerDecision: "SPAWN_SLOW_TASK",
        taskFocus: "NEW_TASK_CANDIDATE",
      }),
      event(3, "SLOWTASK_CREATED", "SlowTask 创建任务账本。", [
        "task_id=slowtask_visit_demo_001。",
        "current_plan_version 从 SlowTask mock event 读出。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(1) }),
      event(4, "PLANNING_STARTED", "SlowTask 进入 PLANNING。", [
        "创建 plan_version=1。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(2) }),
      event(5, "EVIDENCE_REVIEWED", "SlowTask 审查 evidence。", [
        "用户约束是 authoritative evidence。",
        "预算/时间偏好缺失由 SlowTask 判断。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(3) }),
      event(6, "INSUFFICIENT_EVIDENCE_FOR_ACTION", "证据不足，不能直接形成最终计划。", [
        "缺少预算或时间偏好。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(4) }),
      event(7, "CLARIFICATION_REQUESTED", "SlowTask 请求补充缺失字段。", [
        "澄清请求属于 SlowTask，不是 Codex 自行决定。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(5) }),
      event(8, "WAITING_FOR_SLOT", "SlowTask 等待用户补充 slot。", [
        "状态进入 WAITING_FOR_SLOT。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(6) }),
    ],
    codexProposal: proposal(
      "proposal_demo_01_plan",
      "plan_update",
      "Codex 起草一个两天来访行程 proposal，但它不是 SlowTask 事实。",
      ["evidence://demo/01/user-turn", "evidence://demo/01/router-decision"],
      ["保留靠近公司的地点约束。", "向用户追问预算和时间偏好。", "等待 SlowTask 接受后再成为事实。"],
    ),
    proposalType: "plan_update",
    answer:
      "我会把这句话视为新的复杂任务：Router=SPAWN_SLOW_TASK，TaskFocus=NEW_TASK_CANDIDATE；SlowTask 创建 plan_version=1，审查证据后发现预算/时间偏好缺失，所以进入 WAITING_FOR_SLOT。Codex 只能给计划草案 proposal。",
  }),
  buildScenario({
    id: "demo_02_material_user_patch",
    title: "场景 2：用户补充约束导致计划变化",
    shortName: "2 补约束",
    demoAction: "send_user_patch",
    mockInput: "改成明天上午，并且预算控制在 500 元以内。",
    matchKeywords: ["明天上午", "预算", "500", "改成"],
    summary: "active SlowTask 期间的明确补充会成为 UserPatch evidence；SlowTask 判断 material 后推进 plan_version。",
    routerDecision: "PATCH_ACTIVE_SLOW_TASK",
    taskFocus: "ACTIVE_TASK_PATCH",
    routerRule:
      "ADR-006：有 active task 且输入明显补充当前任务时，task_focus=ACTIVE_TASK_PATCH，RouterDecision=PATCH_ACTIVE_SLOW_TASK。",
    activeTaskContext: "已有 active SlowTask：slowtask_visit_demo_001，current_plan_version=1。",
    lifecycleState: "PLANNING",
    currentPlanVersion: 2,
    currentTaskEventSeq: 10,
    planVersions: [
      {
        planVersion: 1,
        status: "superseded",
        summary: "旧计划缺少明确时间和预算。",
        reason: "initial_plan",
        createdByEventId: "evt_demo_01_planning_started",
      },
      {
        planVersion: 2,
        status: "current",
        summary: "新计划加入明天上午和 500 元预算约束。",
        reason: "user_patch",
        createdByEventId: "evt_demo_02_plan_version_advanced",
      },
    ],
    evidence: [
      evidence(
        "demo_02_user_patch",
        "UserPatch evidence",
        "用户把时间改成明天上午，并设置预算 500 元以内。",
        1,
        "evidence://demo/02/user-patch/change-time-budget",
      ),
      evidence(
        "demo_02_hypothesis_material",
        "Material change hypothesis",
        "Router/Thinker 可提示这像 material change，但最终解释属于 SlowTask。",
        1,
        "hypothesis://demo/02/material-change",
        "non_authoritative_hypothesis",
      ),
    ],
    timeline: [
      event(1, "TURN_INGRESS_COMMITTED", "用户补充 turn 被提交。", [
        "这是 active task 期间的新输入。",
      ], { owner: "event_journal" }),
      event(2, "ROUTER_DECISION_EMITTED", "Router 输出 PATCH_ACTIVE_SLOW_TASK。", [
        "Router 只把它作为 UserPatch 路由到 active SlowTask。",
      ], {
        owner: "router",
        routerDecision: "PATCH_ACTIVE_SLOW_TASK",
        taskFocus: "ACTIVE_TASK_PATCH",
      }),
      event(3, "USER_PATCH_RECEIVED", "UserPatch 绑定 pre-advance plan_version=1。", [
        "UserPatch 是 evidence pack，不是新 plan。",
      ], { owner: "user_patch_pipeline", planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(7) }),
      event(4, "USER_PATCH_INTERPRETED", "SlowTask against plan_version=1 解释 patch。", [
        "interpretation_type=constraint_update。",
        "materially_changes_task=true。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(8) }),
      event(5, "PLAN_VERSION_ADVANCED", "SlowTask 推进到 plan_version=2。", [
        "只有 SlowTask 可以推进 plan_version。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(9) }),
      event(6, "TASK_REPLANNED", "当前计划按新约束重排。", [
        "plan_version=2 成为 current。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(10) }),
    ],
    codexProposal: proposal(
      "proposal_demo_02_patch",
      "plan_update",
      "Codex 建议把时间和预算作为 material change 交给 SlowTask 解释。",
      ["evidence://demo/02/user-patch/change-time-budget"],
      ["保留原有地点约束。", "将时间窗口改为明天上午。", "把预算约束加入 plan_version=2 的候选计划。"],
    ),
    proposalType: "plan_update",
    answer:
      "这是对 active SlowTask 的明确补充：Router=PATCH_ACTIVE_SLOW_TASK，TaskFocus=ACTIVE_TASK_PATCH。SlowTask 先记录 USER_PATCH_RECEIVED(plan_version=1)，再解释为 material change，随后才推进到 plan_version=2。",
  }),
  buildScenario({
    id: "demo_03_stale_tool_result",
    title: "场景 3：旧工具结果变成 stale evidence",
    shortName: "3 旧结果",
    demoAction: "receive_late_tool_result",
    mockInput: "demo tool lookup 返回了 plan_version=1 的酒店列表，但当前任务已经是 plan_version=2。",
    matchKeywords: ["工具", "tool", "返回", "plan_version=1", "旧结果", "酒店"],
    summary: "旧工具结果带着 old plan_version 返回，SlowTask 只能放入 stale evidence bucket，不能推进当前计划。",
    routerDecision: "PATCH_ACTIVE_SLOW_TASK",
    taskFocus: "ACTIVE_TASK_PATCH",
    routerRule:
      "这个场景的用户改动部分按 ADR-006 走 PATCH_ACTIVE_SLOW_TASK；late ToolResult 本身由 Tool Executor 报告，不由 Router 分类。",
    activeTaskContext: "active SlowTask 已因用户补充推进到 current_plan_version=2；旧工具调用绑定 plan_version=1。",
    lifecycleState: "PLANNING",
    currentPlanVersion: 2,
    currentTaskEventSeq: 14,
    planVersions: [
      {
        planVersion: 1,
        status: "superseded",
        summary: "旧计划启动过 demo tool lookup。",
        reason: "initial_plan",
        createdByEventId: "evt_demo_03_tool_call_started",
      },
      {
        planVersion: 2,
        status: "current",
        summary: "当前计划包含明天上午和 500 元预算约束。",
        reason: "user_patch",
        createdByEventId: "evt_demo_02_plan_version_advanced",
      },
    ],
    evidence: [
      evidence(
        "demo_03_current_patch",
        "Current-plan evidence",
        "用户已把任务改成明天上午且预算 500 元以内。",
        2,
        "evidence://demo/03/current-plan-user-patch",
      ),
    ],
    staleEvidence: [
      evidence(
        "demo_03_stale_tool",
        "Old tool result",
        "plan_version=1 的酒店列表返回太晚，不能直接进入当前 plan_version=2。",
        1,
        "stale://demo/03/tool-result-plan-1",
        "stale_evidence",
        true,
      ),
    ],
    timeline: [
      event(1, "TOOL_CALL_STARTED", "旧 plan_version=1 发起 demo tool lookup。", [
        "tool_call_id=tool_demo_visit_lookup_001。",
      ], { owner: "tool_executor", planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(7) }),
      event(2, "WAITING_FOR_TOOL", "SlowTask 等待工具结果。", [
        "此时仍是 plan_version=1。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(8) }),
      event(3, "PLAN_VERSION_ADVANCED", "用户补充导致 current plan 变成 2。", [
        "旧工具结果还没回来。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(11) }),
      event(4, "TOOL_RESULT_RECEIVED", "旧工具结果返回，仍绑定 plan_version=1。", [
        "Tool Executor 保留原始 plan binding。",
      ], { owner: "tool_executor", planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(12) }),
      event(5, "TOOL_RESULT_MARKED_STALE", "SlowTask 标记结果为 stale。", [
        "旧结果不得推进 current task。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(13) }),
      event(6, "STALE_EVIDENCE_RECORDED", "结果进入 stale evidence bucket。", [
        "除非 SlowTask 显式 adopt/rebase，否则不能成为当前事实。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(14) }),
    ],
    codexProposal: proposal(
      "proposal_demo_03_stale",
      "evidence_review",
      "Codex 可建议是否值得 adopt 旧结果，但不能自己采用 stale evidence。",
      ["stale://demo/03/tool-result-plan-1", "evidence://demo/03/current-plan-user-patch"],
      ["解释旧结果为什么 stale。", "建议 SlowTask 只复用仍满足当前约束的部分。", "等待显式 adopt/rebase。"],
    ),
    proposalType: "evidence_review",
    answer:
      "这个 late tool result 绑定 old plan_version=1；当前 SlowTask 已经是 plan_version=2。按 ADR-004，它必须进入 stale_evidence，不能推进当前计划。Codex 最多建议是否 adopt，不能自己采用。",
  }),
  buildScenario({
    id: "demo_04_confirmation_gate",
    title: "场景 4：确认门控",
    shortName: "4 确认",
    demoAction: "request_cancel_confirmation",
    mockInput: "那就取消这个任务吧。",
    matchKeywords: ["取消", "别做", "停止", "不做了", "cancel"],
    summary: "取消/暂停候选不会由 Router 或 Codex 直接执行；SlowTask 进入 WAITING_FOR_USER_CONFIRMATION。",
    routerDecision: "PATCH_ACTIVE_SLOW_TASK",
    taskFocus: "CANCEL_OR_PAUSE_CANDIDATE",
    routerRule:
      "ADR-006/016：有 active task 时，取消候选作为 UserPatch control evidence；SlowTask 拥有 confirmation/cancel state。",
    activeTaskContext: "已有 active SlowTask：slowtask_visit_demo_001，current_plan_version=2。",
    lifecycleState: "WAITING_FOR_USER_CONFIRMATION",
    currentPlanVersion: 2,
    currentTaskEventSeq: 18,
    planVersions: [
      {
        planVersion: 2,
        status: "current",
        summary: "当前计划等待用户确认是否取消。",
        reason: "user_patch",
        createdByEventId: "evt_demo_04_confirmation_required",
      },
    ],
    pendingConfirmation: {
      confirmationId: "confirm_demo_cancel_001",
      scope: "TASK_CANCEL",
      planVersion: 2,
      prompt: "你确定要取消这个客户来访行程规划任务吗？",
      riskSummary: "取消会终止当前 active SlowTask；MVP 不做 pause/resume。",
      status: "pending",
    },
    evidence: [
      evidence(
        "demo_04_cancel_text",
        "Cancel candidate",
        "用户表达取消当前任务的意图。",
        2,
        "evidence://demo/04/user-cancel-candidate",
      ),
      evidence(
        "demo_04_control_hypothesis",
        "Control hypothesis",
        "Router 可标注 CANCEL_OR_PAUSE_CANDIDATE，但不能直接 cancel。",
        2,
        "hypothesis://demo/04/cancel-control",
        "non_authoritative_hypothesis",
      ),
    ],
    timeline: [
      event(1, "TURN_INGRESS_COMMITTED", "取消请求被提交。", [
        "仍然必须走正常 turn ingress。",
      ], { owner: "event_journal" }),
      event(2, "ROUTER_DECISION_EMITTED", "Router 输出 PATCH_ACTIVE_SLOW_TASK。", [
        "task_focus=CANCEL_OR_PAUSE_CANDIDATE。",
      ], {
        owner: "router",
        routerDecision: "PATCH_ACTIVE_SLOW_TASK",
        taskFocus: "CANCEL_OR_PAUSE_CANDIDATE",
      }),
      event(3, "USER_PATCH_RECEIVED", "取消候选进入 UserPatch evidence。", [
        "不是 raw text shortcut。",
      ], { owner: "user_patch_pipeline", planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(15) }),
      event(4, "USER_PATCH_INTERPRETED", "SlowTask 解释为 cancel candidate。", [
        "confirmation ownership 属于 SlowTask。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(16) }),
      event(5, "CONFIRMATION_REQUIRED", "SlowTask 发起 TASK_CANCEL confirmation。", [
        "Codex 可起草提示语，但不能代替用户确认。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(17) }),
      event(6, "WAITING_FOR_USER_CONFIRMATION", "SlowTask 等待用户确认。", [
        "只有用户 action 可以 accepted/rejected。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(18) }),
    ],
    codexProposal: proposal(
      "proposal_demo_04_confirm",
      "clarification",
      "Codex 起草取消确认提示语，但不能替用户点确认。",
      ["evidence://demo/04/user-cancel-candidate"],
      ["用清楚的确认提示复述风险。", "等待用户明确 accept/reject。", "不要把待确认说成已取消。"],
    ),
    proposalType: "clarification",
    answer:
      "这是 active task 的取消候选：Router=PATCH_ACTIVE_SLOW_TASK，TaskFocus=CANCEL_OR_PAUSE_CANDIDATE。SlowTask 把它解释为 cancel candidate 后进入 WAITING_FOR_USER_CONFIRMATION；Codex 只能起草确认话术，不能替用户确认。",
  }),
  buildScenario({
    id: "demo_05_foreground_chat",
    title: "补充：active task 期间的非 material 前台闲聊",
    shortName: "5 闲聊",
    demoAction: "foreground_chat",
    mockInput: "谢谢，先这样。",
    matchKeywords: ["谢谢", "先这样", "辛苦", "好的"],
    summary: "active SlowTask 期间的明显闲聊走 FAST_ONLY，不生成 UserPatch，也不推进 plan_version。",
    routerDecision: "FAST_ONLY",
    taskFocus: "FOREGROUND_CHAT",
    routerRule:
      "ADR-006：有 active task 时，明显闲聊/轻问答走 FAST_ONLY，不 patch active task。",
    activeTaskContext: "已有 active SlowTask，但该输入不补充当前任务。",
    lifecycleState: "PLANNING",
    currentPlanVersion: 2,
    currentTaskEventSeq: 18,
    planVersions: [
      {
        planVersion: 2,
        status: "current",
        summary: "当前计划不因 foreground chat 改变。",
        reason: "user_patch",
        createdByEventId: "evt_demo_02_plan_version_advanced",
      },
    ],
    evidence: [
      evidence(
        "demo_05_foreground",
        "Foreground chat",
        "用户只是表达感谢，不补充任务参数。",
        2,
        "evidence://demo/05/foreground-chat",
      ),
    ],
    timeline: [
      event(1, "TURN_INGRESS_COMMITTED", "前台闲聊 turn 被提交。", [
        "输入仍可 replay，但不进入 SlowTask evidence。",
      ], { owner: "event_journal" }),
      event(2, "ROUTER_DECISION_EMITTED", "Router 输出 FAST_ONLY。", [
        "不生成 UserPatch。",
        "不推进 plan_version。",
      ], {
        owner: "router",
        routerDecision: "FAST_ONLY",
        taskFocus: "FOREGROUND_CHAT",
      }),
      event(3, "TASK_FOCUS_STATE_UPDATED", "TaskFocusState 记录 foreground chat。", [
        "active_task_id 不变。",
      ], { owner: "router" }),
    ],
    codexProposal: proposal(
      "proposal_demo_05_none",
      "evidence_review",
      "Codex 不需要为 foreground chat 生成任务变更 proposal。",
      ["evidence://demo/05/foreground-chat"],
      ["保持 active SlowTask 不变。", "只给前台简短回复。"],
    ),
    proposalType: "evidence_review",
    answer:
      "这不是任务补充，而是 foreground chat：Router=FAST_ONLY，TaskFocus=FOREGROUND_CHAT。不会生成 UserPatch，也不会推进 plan_version。",
  }),
];

export const defaultScenarioId = "demo_01_new_complex_task";

export function findScenarioForInput(input: string): SlowSystemScenario | undefined {
  const normalized = input.trim().toLowerCase();
  if (!normalized) {
    return undefined;
  }

  return slowSystemScenarios.find((scenario) =>
    scenario.matchKeywords.some((keyword) => normalized.includes(keyword.toLowerCase())),
  );
}

export type RuntimeScenarioResult = Readonly<{
  scenario: SlowSystemScenario;
  selectedScenarioId: string;
  matchedBy: InputMatchMode;
}>;

export function buildRuntimeScenarioForInput(
  input: string,
  selectedScenario: SlowSystemScenario,
): RuntimeScenarioResult {
  const trimmed = input.trim();
  if (!trimmed) {
    return {
      scenario: selectedScenario,
      selectedScenarioId: selectedScenario.id,
      matchedBy: "manual_fallback",
    };
  }

  const matched = findScenarioForInput(trimmed);
  if (matched) {
    return {
      scenario: buildRuntimeScenario(matched, trimmed, "keyword_match"),
      selectedScenarioId: matched.id,
      matchedBy: "keyword_match",
    };
  }

  const dynamicBase = selectDynamicBaseScenario(trimmed, selectedScenario);
  return {
    scenario: buildRuntimeScenario(dynamicBase, trimmed, "dynamic_router"),
    selectedScenarioId: dynamicBase.id,
    matchedBy: "dynamic_router",
  };
}

function buildRuntimeScenario(
  base: SlowSystemScenario,
  input: string,
  matchedBy: InputMatchMode,
): SlowSystemScenario {
  const sourceSlug = base.id.replace(/[^A-Za-z0-9_.:-]/g, "_");
  const userInputRef = `evidence://runtime/${sourceSlug}/user_input`;
  const sourceEvidenceRefs = [userInputRef];
  const runtimeAnswer =
    matchedBy === "dynamic_router"
      ? [
          `系统没有把这条输入当成静态 mock 卡片，而是按 Router fallback 生成 runtime snapshot：Router=${base.routerDecision}，TaskFocus=${base.taskFocus}。`,
          `SlowTask mock state 仍然只读展示；当前 plan_version=${base.slowTask.currentPlanVersion.value}，lifecycle=${base.slowTask.lifecycleState}。`,
          "右侧 Codex proposal 会由后端 bridge 基于本次输入生成分析；proposal 不能直接修改 SlowTask facts。",
        ].join(" ")
      : [
          base.answer,
          "本次用户 turn 已替换为输入框里的真实文本；右侧 Codex proposal 会基于这次输入重新请求后端 bridge。",
        ].join(" ");

  return {
    ...base,
    id: `runtime_${base.id}`,
    title:
      matchedBy === "dynamic_router"
        ? `动态输入：${base.routerDecision}`
        : base.title,
    mockInput: input,
    summary:
      matchedBy === "dynamic_router"
        ? `未命中必做 mocklist 关键词，系统按 Router fallback 规则分类为 ${base.routerDecision} / ${base.taskFocus}，并立即请求 Codex proposal bridge 分析。`
        : `${base.summary} 本次运行会立即调用 Codex proposal bridge 分析输入内容。`,
    conversation: base.conversation.map((turn, index) => {
      if (index === 0) {
        return {
          ...turn,
          text: input,
          note:
            turn.speaker === "tool"
              ? "这是本次输入框提交的工具结果文本；系统将它包装成 runtime evidence 后交给后端 proposal bridge。"
              : "这是本次输入框提交的真实用户文本；系统将它包装成 runtime evidence 后交给后端 proposal bridge。",
        };
      }
      if (index === 1) {
        return {
          ...turn,
          text: runtimeAnswer,
          note:
            "这是前端 runtime 处理摘要；Codex 分析结果来自右侧后端 proposal bridge，不是静态写死在页面里的结论。",
        };
      }
      return turn;
    }),
    evidence: base.evidence.map((item, index) =>
      index === 0
        ? {
            ...item,
            id: `runtime_${sourceSlug}_user_input`,
            evidenceId: userInputRef,
            title: "Runtime input evidence",
            body: `本次输入：${input}`,
            sourceRef: userInputRef,
          }
        : item,
    ),
    codexProposal: {
      ...base.codexProposal,
      proposalId: `proposal_runtime_${sourceSlug}`,
      status: "draft",
      summary: "等待后端 Codex proposal bridge 基于本次输入返回分析。",
      suggestedNextSteps: [
        "点击 Run 后会自动请求后端 proposal bridge。",
        "Codex 分析只能作为 proposal 展示。",
        "SlowTask facts、plan_version、tool authorization 仍由后端边界拥有。",
      ],
      missingFields: [],
      requiresConfirmation: base.proposalType === "clarification",
      sourceEvidenceRefs,
      backendMode: "static_mock",
      boundaryWarning:
        "这是本次输入的待分析 proposal 占位；真实分析需要等待后端 bridge 返回，且仍不能直接推进 plan_version。",
    },
    answer: runtimeAnswer,
  };
}

function selectDynamicBaseScenario(
  input: string,
  selectedScenario: SlowSystemScenario,
): SlowSystemScenario {
  const normalized = input.toLowerCase();

  if (containsAny(normalized, ["取消", "停止", "暂停", "别做", "不做了", "cancel", "stop", "pause"])) {
    return scenarioById("demo_04_confirmation_gate");
  }
  if (
    containsAny(normalized, [
      "tool",
      "工具",
      "toolresult",
      "tool result",
      "plan_version",
      "stale",
      "旧结果",
      "返回太晚",
    ])
  ) {
    return scenarioById("demo_03_stale_tool_result");
  }
  if (containsAny(normalized, ["谢谢", "先这样", "辛苦", "好的", "ok", "thanks"])) {
    return scenarioById("demo_05_foreground_chat");
  }
  if (
    containsAny(normalized, [
      "改",
      "换",
      "调整",
      "预算",
      "明天",
      "加上",
      "不要",
      "控制",
      "提前",
      "推迟",
    ])
  ) {
    return scenarioById("demo_02_material_user_patch");
  }
  if (
    selectedScenario.routerDecision === "PATCH_ACTIVE_SLOW_TASK" &&
    !containsAny(normalized, ["规划", "计划", "安排", "帮我", "分析", "整理"])
  ) {
    return scenarioById("demo_05_foreground_chat");
  }
  return scenarioById("demo_01_new_complex_task");
}

function scenarioById(id: string): SlowSystemScenario {
  return slowSystemScenarios.find((scenario) => scenario.id === id) ?? slowSystemScenarios[0];
}

function containsAny(value: string, candidates: readonly string[]) {
  return candidates.some((candidate) => value.includes(candidate.toLowerCase()));
}
