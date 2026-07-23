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

const progress = (
  sequence: number,
  kind: string,
  phase: string,
  label: string,
  detail: string,
  toolName?: string,
) => ({
  trace_id: "trace_demo_reception_yunnan",
  sequence,
  kind,
  status: "completed",
  provider_mode: "codex_cli_local",
  output_mode: "mock",
  canonical: false as const,
  task_id: "slowtask_reception_meal_001",
  plan_version: phase.includes("v3") ? 3 : phase.includes("v2") ? 2 : 1,
  created_monotonic_ms: sequence * 320,
  tool_name: toolName ?? null,
  proposal_only: true,
  result_present: kind.includes("completed") || kind.includes("structured"),
  usage: null,
  latency_ms: sequence * 180,
  detail,
  phase,
  label,
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
  liveProgress?: SlowSystemScenario["liveProgress"];
  streaming?: SlowSystemScenario["streaming"];
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
  liveProgress: input.liveProgress ?? [],
  streaming: input.streaming ?? {
    active: false,
    phase: "idle",
    label: "等待新的 Workbench turn",
    detail: "",
    sequence: 0,
  },
  evidence: input.evidence,
  staleEvidence: input.staleEvidence ?? [],
  codexProposal: input.codexProposal,
  proposalType: input.proposalType,
  answer: input.answer,
});

export const slowSystemScenarios: readonly SlowSystemScenario[] = [
  buildScenario({
    id: "demo_00_reception_yunnan_lunch",
    title: "主线：云南菜接待规划启动",
    shortName: "接待午饭",
    demoAction: "start_new_task",
    mockInput: "请帮忙规划一个接待午饭，选云南菜。",
    matchKeywords: ["接待午饭", "云南菜", "接待", "午饭"],
    summary:
      "用户启动接待用餐规划；SlowTask 先记录云南菜偏好与午饭目标，再发现人数、地点、预算、忌口、时间窗口缺失。",
    routerDecision: "SPAWN_SLOW_TASK",
    taskFocus: "NEW_TASK_CANDIDATE",
    routerRule:
      "ADR-006：无 active SlowTask 时，复杂规划请求触发 SPAWN_SLOW_TASK；缺失字段由 SlowTask 审查 evidence 后提出。",
    activeTaskContext: "当前没有 active SlowTask；新建 slowtask_reception_meal_001。",
    lifecycleState: "WAITING_FOR_SLOT",
    currentPlanVersion: 1,
    currentTaskEventSeq: 8,
    planVersions: [
      {
        planVersion: 1,
        status: "current",
        summary: "接待用餐规划：云南菜、午饭；缺少人数、地点、预算、忌口和精确时间。",
        reason: "initial_plan",
        createdByEventId: "evt_reception_v1_planning_started",
      },
    ],
    evidence: [
      evidence(
        "demo_00_user_goal",
        "用户目标",
        "用户请求规划接待午饭，并明确菜系为云南菜。",
        1,
        "evidence://reception-yunnan/turn/start",
      ),
      evidence(
        "demo_00_memory_write",
        "Session memory write",
        "记忆槽写入：meal_type=lunch, cuisine=Yunnan, purpose=reception。该 memory 只在本 session 内作为 evidence 使用。",
        1,
        "memory://session/reception_meal/preferences/v1",
      ),
      evidence(
        "demo_00_missing_slots",
        "缺失字段审查",
        "SlowTask 判断还缺少 guest_count、location_anchor、budget、dietary_constraints、time_window。",
        1,
        "evidence://reception-yunnan/missing-fields/v1",
        "non_authoritative_hypothesis",
      ),
    ],
    timeline: [
      event(1, "TURN_INGRESS_COMMITTED", "用户启动接待午饭规划。", [
        "输入通过 turn ingress 进入 Event Journal。",
      ], { owner: "event_journal" }),
      event(2, "ROUTER_DECISION_EMITTED", "Router 输出 SPAWN_SLOW_TASK。", [
        "Router 只做分类，不解释缺失字段。",
      ], {
        owner: "router",
        routerDecision: "SPAWN_SLOW_TASK",
        taskFocus: "NEW_TASK_CANDIDATE",
      }),
      event(3, "SLOWTASK_CREATED", "SlowTask 创建接待规划任务。", [
        "task_id=slowtask_reception_meal_001。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(1) }),
      event(4, "SLOWTASK_STATE_CHANGED", "SlowTask 进入 PLANNING。", [
        "state: CREATED -> PLANNING。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(2) }),
      event(5, "PLANNING_STARTED", "开始审查接待用餐约束。", [
        "云南菜和午饭是 authoritative evidence。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(3) }),
      event(6, "EVIDENCE_REVIEWED", "写入 session memory 并识别缺失槽。", [
        "memory evidence 不包含 PII 或 secret。",
        "缺少人数、地点、预算、忌口、时间窗口。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(4) }),
      event(7, "INSUFFICIENT_EVIDENCE_FOR_ACTION", "证据不足，不能直接输出最终规划。", [
        "不得把 proposal 说成最终安排。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(5) }),
      event(8, "CLARIFICATION_REQUESTED", "请求用户补充接待数据。", [
        "补充字段会作为 UserPatch evidence 进入下一版计划。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(6) }),
      event(9, "WAITING_FOR_SLOT", "等待用户补充缺失字段。", [
        "状态进入 WAITING_FOR_SLOT。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(7) }),
      event(10, "SLOWTASK_STATE_CHANGED", "SlowTask 状态更新为 WAITING_FOR_SLOT。", [
        "每个 SlowTask state transition 都有 journal event。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(8) }),
    ],
    liveProgress: [
      progress(1, "turn.started", "planning_v1", "Codex proposal bridge 收到接待午饭任务", "只生成 proposal，不推进 SlowTask fact。"),
      progress(2, "item.started", "planning_v1", "分析缺失字段", "检查人数、地点、预算、忌口、时间窗口是否足够。", "codex_gap_analyzer"),
      progress(3, "item.completed", "planning_v1", "缺失字段分析完成", "返回 missing_fields proposal，等待 SlowTask/用户补充。", "codex_gap_analyzer"),
      progress(4, "structured_output_emitted", "planning_v1", "结构化 proposal 已校验", "安全标志显示 Codex 未授权工具、未修改 plan_version。"),
    ],
    codexProposal: proposal(
      "proposal_reception_yunnan_v1",
      "clarification",
      "Codex 建议先追问人数、地点锚点、预算、忌口和可用时间；它只提供分析草案，不拥有任务事实。",
      ["evidence://reception-yunnan/turn/start", "memory://session/reception_meal/preferences/v1"],
      ["保留云南菜和接待午饭目标。", "请求用户补充人数、预算、地点、忌口和时间窗口。", "等待 UserPatch 后再推进 plan_version。"],
    ),
    proposalType: "clarification",
    answer:
      "我会先记录：接待用餐、云南菜、午饭。现在还缺人数、地点锚点、预算、忌口和具体时间窗口，所以 SlowTask 进入 WAITING_FOR_SLOT；Codex 只展示缺槽分析和追问建议。",
  }),
  buildScenario({
    id: "demo_06_reception_yunnan_details",
    title: "主线：用户补充接待数据",
    shortName: "补接待数据",
    demoAction: "send_user_patch",
    mockInput: "8个人，客户住在公司附近，人均200以内，有两位不吃辣，最好12点半。",
    matchKeywords: ["8个人", "人均200", "不吃辣", "12点半", "公司附近"],
    summary:
      "用户补齐关键接待数据；SlowTask 将补充内容作为 UserPatch evidence，推进到 plan_version=2 并准备 demo tool 查询。",
    routerDecision: "PATCH_ACTIVE_SLOW_TASK",
    taskFocus: "ACTIVE_TASK_PATCH",
    routerRule:
      "ADR-006/007：active task 期间的明确约束补充进入 UserPatch evidence pack；是否 material 由 SlowTask 解释。",
    activeTaskContext: "active SlowTask：slowtask_reception_meal_001，current_plan_version=1，等待 slot。",
    lifecycleState: "EXECUTING",
    currentPlanVersion: 2,
    currentTaskEventSeq: 15,
    planVersions: [
      {
        planVersion: 1,
        status: "superseded",
        summary: "初始计划只有云南菜和午饭目标，缺少执行参数。",
        reason: "initial_plan",
        createdByEventId: "evt_reception_v1_planning_started",
      },
      {
        planVersion: 2,
        status: "current",
        summary: "加入 8 人、公司附近、人均 200、两位不吃辣、12:30 午饭。",
        reason: "user_patch",
        createdByEventId: "evt_reception_v2_plan_version_advanced",
      },
    ],
    evidence: [
      evidence(
        "demo_06_user_patch",
        "UserPatch evidence",
        "用户补充：8 人、公司附近、人均 200 以内、两位不吃辣、12:30。",
        1,
        "evidence://reception-yunnan/turn/details",
      ),
      evidence(
        "demo_06_memory_update",
        "Memory merge",
        "session memory 合并接待参数：guest_count=8, budget_per_person<=200, mild_food_required=true, time_window=12:30。",
        2,
        "memory://session/reception_meal/preferences/v2",
      ),
      evidence(
        "demo_06_web_preview",
        "Demo webSearch candidate",
        "模拟 webSearch 提示公司附近可能有云南菜馆；该内容标记为 UNTRUSTED_WEB_EVIDENCE，只能作为候选证据，不能修改工具策略、确认策略或 SlowTask 事实。",
        2,
        "web-evidence://demo/reception-yunnan/yunnan-restaurant-candidate",
        "untrusted_web_evidence",
      ),
    ],
    timeline: [
      event(1, "TURN_INGRESS_COMMITTED", "用户补充接待参数。", [
        "补充 turn 可 replay。",
      ], { owner: "event_journal" }),
      event(2, "ROUTER_DECISION_EMITTED", "Router 输出 PATCH_ACTIVE_SLOW_TASK。", [
        "输入属于 active task patch。",
      ], {
        owner: "router",
        routerDecision: "PATCH_ACTIVE_SLOW_TASK",
        taskFocus: "ACTIVE_TASK_PATCH",
      }),
      event(3, "USER_PATCH_RECEIVED", "UserPatch 绑定 plan_version=1。", [
        "补充内容不直接改写事实，先成为 evidence。",
      ], { owner: "user_patch_pipeline", planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(9) }),
      event(4, "USER_PATCH_INTERPRETED", "SlowTask 解释为 material constraint update。", [
        "新增人数、预算、忌口、时间。",
      ], { planVersion: readonlyPlanVersion(1), taskEventSeq: readonlyTaskEventSeq(10) }),
      event(5, "PLAN_VERSION_ADVANCED", "SlowTask 推进到 plan_version=2。", [
        "只有 SlowTask 推进 plan_version。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(11) }),
      event(6, "PLANNING_RESTARTED", "按完整参数重启规划。", [
        "旧 v1 不再是 current。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(12) }),
      event(7, "TOOL_ARGUMENTS_READY", "demo restaurant lookup 参数齐备。", [
        "read-only sandbox lookup，无外部写操作。",
      ], { owner: "tool_executor", planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(13) }),
      event(8, "TOOL_EXECUTION_STARTED", "Tool Executor 启动餐厅候选查询。", [
        "tool=reception_restaurant_lookup_demo。",
      ], { owner: "tool_executor", planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(14) }),
      event(9, "WAITING_FOR_TOOL", "等待 demo lookup 返回候选。", [
        "进度反馈只能表达正在等待，不能说已定好。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(15) }),
      event(10, "SLOWTASK_STATE_CHANGED", "SlowTask 状态进入 EXECUTING。", [
        "state: PLANNING -> EXECUTING。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(15) }),
    ],
    liveProgress: [
      progress(1, "item.started", "planning_v2", "解释用户补充信息", "把用户补充作为 UserPatch evidence，而不是直接覆盖计划。", "user_patch_interpreter"),
      progress(2, "item.completed", "planning_v2", "UserPatch 解释完成", "判定为 material constraint update。", "user_patch_interpreter"),
      progress(3, "item.started", "planning_v2", "准备餐厅查询参数", "参数绑定 task_id、plan_version 和 task_event_seq。", "tool_argument_builder"),
      progress(4, "item.started", "planning_v2", "调用 demo restaurant lookup", "只运行 demo sandbox read-only 查询。", "reception_restaurant_lookup_demo"),
      progress(5, "item.completed", "planning_v2", "等待餐厅候选返回", "当前还不能表达最终规划完成。", "reception_restaurant_lookup_demo"),
    ],
    codexProposal: proposal(
      "proposal_reception_yunnan_v2",
      "tool_preview",
      "Codex 建议用补齐的参数生成 demo lookup 预览，并提醒两位不吃辣会影响云南菜点单策略。",
      ["evidence://reception-yunnan/turn/details", "memory://session/reception_meal/preferences/v2"],
      ["按 8 人和人均 200 过滤候选。", "为不吃辣客人保留清淡菜品备选。", "工具结果返回前不要输出最终安排。"],
    ),
    proposalType: "tool_preview",
    answer:
      "收到补充信息后，SlowTask 将它作为 UserPatch 解释为 material change，并推进到 plan_version=2。现在 demo Tool Executor 可以用这些参数做只读候选查询；进度里会展示工具名和状态。",
  }),
  buildScenario({
    id: "demo_07_reception_yunnan_evening",
    title: "主线：中途改到晚上",
    shortName: "改晚上",
    demoAction: "send_user_patch",
    mockInput: "中间我插一句，把时间修改到晚上吧。",
    matchKeywords: ["修改到晚上", "改到晚上", "晚上吧", "时间修改"],
    summary:
      "用户在工具查询中插入改期；SlowTask 推进到 plan_version=3，旧午饭 lookup 结果必须进入 stale evidence。",
    routerDecision: "PATCH_ACTIVE_SLOW_TASK",
    taskFocus: "ACTIVE_TASK_PATCH",
    routerRule:
      "ADR-004/016：material UserPatch 会推进 plan_version；旧 plan_version 的 ToolResult 返回后默认 stale。",
    activeTaskContext: "active SlowTask 正在执行 plan_version=2 的午饭候选查询。",
    lifecycleState: "PLANNING",
    currentPlanVersion: 3,
    currentTaskEventSeq: 22,
    planVersions: [
      {
        planVersion: 2,
        status: "superseded",
        summary: "午饭 12:30 查询已被晚餐改期 supersede。",
        reason: "user_patch",
        createdByEventId: "evt_reception_v2_plan_version_advanced",
      },
      {
        planVersion: 3,
        status: "current",
        summary: "接待用餐改为晚上；保留云南菜、8 人、人均 200、两位不吃辣、公司附近。",
        reason: "user_patch",
        createdByEventId: "evt_reception_v3_plan_version_advanced",
      },
    ],
    evidence: [
      evidence(
        "demo_07_user_patch",
        "Evening UserPatch",
        "用户将时间从午饭 12:30 改到晚上。",
        2,
        "evidence://reception-yunnan/turn/evening",
      ),
      evidence(
        "demo_07_memory_update",
        "Memory rebase",
        "session memory 更新：meal_type=dinner, time_window=evening；v2 午饭时间不再是 current fact。",
        3,
        "memory://session/reception_meal/preferences/v3",
      ),
    ],
    staleEvidence: [
      evidence(
        "demo_07_stale_lunch_lookup",
        "Stale lunch lookup result",
        "v2 午饭候选餐厅返回太晚，时间条件已不匹配 v3 晚餐计划。",
        2,
        "stale://reception-yunnan/tool-result/lunch-v2",
        "stale_evidence",
        true,
      ),
    ],
    timeline: [
      event(1, "TURN_INGRESS_COMMITTED", "用户插入改期请求。", [
        "输入仍通过正常 turn ingress。",
      ], { owner: "event_journal" }),
      event(2, "USER_PATCH_RECEIVED", "改期 UserPatch 绑定 plan_version=2。", [
        "这是 active task patch。",
      ], { owner: "user_patch_pipeline", planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(16) }),
      event(3, "USER_PATCH_INTERPRETED", "SlowTask 解释为 material time change。", [
        "时间从 lunch/12:30 改到 evening。",
      ], { planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(17) }),
      event(4, "PLAN_VERSION_ADVANCED", "SlowTask 推进到 plan_version=3。", [
        "v2 工具调用不能推进 v3。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(18) }),
      event(5, "TOOL_EXECUTION_CANCEL_REQUESTED", "请求取消 v2 in-flight lookup。", [
        "如果 adapter 不支持取消，等待结果并按 stale policy 处理。",
      ], { owner: "tool_executor", planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(19) }),
      event(6, "TOOL_RESULT_RECEIVED", "v2 午饭 lookup 晚到。", [
        "结果保留原始 plan_version=2。",
      ], { owner: "tool_executor", planVersion: readonlyPlanVersion(2), taskEventSeq: readonlyTaskEventSeq(20) }),
      event(7, "TOOL_RESULT_MARKED_STALE", "SlowTask 标记旧结果 stale。", [
        "不得推进 current plan_version=3。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(21) }),
      event(8, "STALE_EVIDENCE_RECORDED", "旧午饭结果进入 stale bucket。", [
        "除非显式 adopt/rebase，否则只作参考。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(22) }),
      event(9, "TASK_REPLANNED", "按晚餐约束重排第二版方案。", [
        "这是用户口中的第二版规划设计。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(22) }),
    ],
    liveProgress: [
      progress(1, "item.started", "planning_v3", "收到中途改期", "正在把晚餐时间作为 UserPatch 解释。", "user_patch_interpreter"),
      progress(2, "item.completed", "planning_v3", "推进到 plan_version=3", "v2 午饭工具结果将按 stale policy 处理。", "slowtask_replanner"),
      progress(3, "item.started", "planning_v3", "取消或隔离旧查询", "请求取消 v2 lookup；晚到结果不会进入当前计划。", "tool_cancellation_boundary"),
      progress(4, "item.completed", "planning_v3", "旧午饭结果已隔离", "stale evidence bucket 已记录。", "stale_evidence_guard"),
    ],
    codexProposal: proposal(
      "proposal_reception_yunnan_v3",
      "plan_update",
      "Codex 建议重做晚餐版本，并把午饭候选标成 stale evidence，避免旧结果污染当前计划。",
      ["evidence://reception-yunnan/turn/evening", "stale://reception-yunnan/tool-result/lunch-v2"],
      ["把 meal_type 改为 dinner。", "保留云南菜、8 人、人均 200 和不吃辣约束。", "重跑晚餐候选查询，不复用 v2 午饭结果。"],
    ),
    proposalType: "plan_update",
    answer:
      "这是一条 material UserPatch：SlowTask 从 v2 推进到 v3，并把晚餐作为 current fact。v2 午饭查询即使返回，也只能进入 stale evidence，不能推进当前任务。",
  }),
  buildScenario({
    id: "demo_08_reception_yunnan_final",
    title: "主线：输出最终接待规划",
    shortName: "输出规划",
    demoAction: "send_user_patch",
    mockInput: "信息够了，请输出最终规划。",
    matchKeywords: ["输出最终规划", "最终规划", "信息够了", "给我方案"],
    summary:
      "SlowTask 在当前 plan_version=3 上完成证据审查、参数解析和最终承诺；Composer 只能实现表达，不改写事实。",
    routerDecision: "PATCH_ACTIVE_SLOW_TASK",
    taskFocus: "ACTIVE_TASK_PATCH",
    routerRule:
      "最终回答必须来自 current-plan SemanticCommitment；Composer 不得改写 immutable facts 或风险提示。",
    activeTaskContext: "active SlowTask：slowtask_reception_meal_001，current_plan_version=3。",
    lifecycleState: "COMPLETED",
    currentPlanVersion: 3,
    currentTaskEventSeq: 29,
    planVersions: [
      {
        planVersion: 3,
        status: "completed",
        summary: "晚餐接待规划完成：公司附近云南菜、8 人、人均 200、照顾不吃辣。",
        reason: "user_patch",
        createdByEventId: "evt_reception_v3_plan_version_advanced",
      },
    ],
    evidence: [
      evidence(
        "demo_08_current_memory",
        "Current memory snapshot",
        "current facts：云南菜、晚餐、8 人、公司附近、人均 200、两位不吃辣。",
        3,
        "memory://session/reception_meal/preferences/v3",
      ),
      evidence(
        "demo_08_tool_result",
        "Dinner lookup result",
        "demo sandbox 返回晚餐候选和点单策略：清淡菜品比例、共享菜、预留包间。",
        3,
        "evidence://reception-yunnan/tool-result/dinner-v3",
      ),
    ],
    timeline: [
      event(1, "TOOL_RESULT_RECEIVED", "v3 晚餐候选返回。", [
        "结果绑定 current plan_version=3。",
      ], { owner: "tool_executor", planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(23) }),
      event(2, "EVIDENCE_REVIEWED", "SlowTask 审查候选和 memory。", [
        "只使用 current evidence。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(24) }),
      event(3, "ARGUMENTS_RESOLVED", "最终参数已解析。", [
        "resolved_arguments 不由 Composer 改写。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(25) }),
      event(4, "FINALIZING", "进入最终整理。", [
        "进度反馈可以说正在整理结果。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(26) }),
      event(5, "SEMANTIC_COMMITMENT_EMITTED", "SlowTask 发出最终承诺。", [
        "包含不可变事实、must_say_fields 和风险提示。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(27) }),
      event(6, "SLOWTASK_STATE_CHANGED", "SlowTask 状态进入 COMPLETED。", [
        "state: PLANNING -> COMPLETED。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(28) }),
    ],
    liveProgress: [
      progress(1, "item.completed", "planning_v3", "晚餐候选已返回", "当前 plan_version=3 的工具结果可用于最终规划。", "reception_restaurant_lookup_demo"),
      progress(2, "item.started", "planning_v3", "整理最终规划", "覆盖餐厅选择、到达时间、点单策略和风险提示。", "semantic_commitment_builder"),
      progress(3, "structured_output_emitted", "planning_v3", "SemanticCommitment 已准备", "Composer 只负责表达，不改写事实。", "commitment_coverage_check"),
    ],
    codexProposal: proposal(
      "proposal_reception_yunnan_final",
      "commitment_draft",
      "Codex 可起草最终回答的表达结构，但最终事实来自 SlowTask 的 SemanticCommitment。",
      ["memory://session/reception_meal/preferences/v3", "evidence://reception-yunnan/tool-result/dinner-v3"],
      ["推荐公司附近云南菜晚餐，并注明需二次人工确认实际订位。", "点单保留清淡菜、低辣/不辣选项。", "将预算、人群、时间和风险提示完整说出。"],
    ),
    proposalType: "commitment_draft",
    answer:
      "最终规划：按 8 人晚餐接待处理，选择公司附近云南菜，人均控制在 200 以内；点单以菌菇、汽锅鸡、清炒时蔬、低辣过桥米线/米线小份等照顾不吃辣客人，另保留两道云南特色中辣菜给可吃辣成员。建议 18:30 到店，18:20 前集合；实际订位仍需人工确认，因为 MVP demo 工具不执行真实预订。",
  }),
  buildScenario({
    id: "demo_09_reception_yunnan_cancel",
    title: "主线：最终回答前取消规划",
    shortName: "取消规划",
    demoAction: "request_cancel_confirmation",
    mockInput: "在最终回答之前取消规划吧。",
    matchKeywords: ["取消规划", "最终回答之前取消", "不要输出了", "终止这版规划"],
    summary:
      "用户在最终回答前明确取消；SlowTask 记录 cancel UserPatch，终止当前 plan_version，后续晚到工具结果只能 stale/debug。",
    routerDecision: "PATCH_ACTIVE_SLOW_TASK",
    taskFocus: "CANCEL_OR_PAUSE_CANDIDATE",
    routerRule:
      "ADR-016：取消语义由 SlowTask 拥有；Router 只标注 cancel candidate，不直接 terminal task。",
    activeTaskContext: "active SlowTask：slowtask_reception_meal_001，current_plan_version=3，尚未发出最终 SemanticCommitment。",
    lifecycleState: "CANCELLED",
    currentPlanVersion: 3,
    currentTaskEventSeq: 26,
    planVersions: [
      {
        planVersion: 3,
        status: "cancelled",
        summary: "晚餐接待规划在最终回答前被用户取消，未发出最终承诺。",
        reason: "user_patch",
        createdByEventId: "evt_reception_cancelled",
      },
    ],
    evidence: [
      evidence(
        "demo_09_cancel",
        "Cancel UserPatch",
        "用户明确要求在最终回答前取消规划。",
        3,
        "evidence://reception-yunnan/turn/cancel-before-final",
      ),
    ],
    timeline: [
      event(1, "TURN_INGRESS_COMMITTED", "取消请求被提交。", [
        "取消也不能绕过 ingress。",
      ], { owner: "event_journal" }),
      event(2, "ROUTER_DECISION_EMITTED", "Router 标注 CANCEL_OR_PAUSE_CANDIDATE。", [
        "Router 不直接取消 SlowTask。",
      ], {
        owner: "router",
        routerDecision: "PATCH_ACTIVE_SLOW_TASK",
        taskFocus: "CANCEL_OR_PAUSE_CANDIDATE",
      }),
      event(3, "USER_PATCH_RECEIVED", "取消请求作为 UserPatch evidence。", [
        "绑定 current plan_version=3。",
      ], { owner: "user_patch_pipeline", planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(23) }),
      event(4, "USER_PATCH_INTERPRETED", "SlowTask 解释为 explicit cancel。", [
        "终止当前版本前不发出 SemanticCommitment。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(24) }),
      event(5, "SLOWTASK_CANCEL_REQUESTED", "SlowTask 请求取消当前任务。", [
        "如果存在 in-flight tool，按 Tool Executor cancel/stale policy 处理。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(25) }),
      event(6, "SLOWTASK_CANCELLED", "当前规划任务终止。", [
        "terminal outcome=CANCELLED。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(26) }),
      event(7, "SLOWTASK_STATE_CHANGED", "SlowTask 状态进入 CANCELLED。", [
        "terminal task 不再接受 UserPatch 推进。",
      ], { planVersion: readonlyPlanVersion(3), taskEventSeq: readonlyTaskEventSeq(26) }),
    ],
    liveProgress: [
      progress(1, "item.started", "planning_v3", "收到取消请求", "正在通过 UserPatch 管道解释，不走 raw text shortcut。", "user_patch_interpreter"),
      progress(2, "item.completed", "planning_v3", "取消已记录", "SlowTask 终止当前版本；不会输出最终规划。", "slowtask_cancel_boundary"),
    ],
    codexProposal: proposal(
      "proposal_reception_yunnan_cancel",
      "clarification",
      "Codex 只建议用明确话术告知任务已取消；它没有权限取消 SlowTask，本次终止来自 SlowTask 事件。",
      ["evidence://reception-yunnan/turn/cancel-before-final"],
      ["确认不再输出最终规划。", "说明当前 plan_version 已进入 terminal CANCELLED。", "后续晚到工具结果只能作为 stale/debug evidence。"],
    ),
    proposalType: "clarification",
    answer:
      "已按 SlowTask cancel flow 终止这版规划：当前 plan_version=3 进入 CANCELLED，最终规划不会输出；如果已有晚到工具结果，只能进入 stale/debug evidence，不能恢复推进任务。",
  }),
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
