# Week 2 慢系统可视化和 Demo 故事线笔记

这周的重点不是把慢系统做得更“聪明”，而是让别人能看懂慢系统到底在怎么工作。页面从 Week 1 的基础骨架，变成了一个更像聊天记录的 Workbench：用户每发一条消息，系统都会把 Router 判断、SlowTask 状态、plan version、evidence、Codex proposal 和工具进度放在同一个过程里展示。

React 仍然只负责显示。它不会自己推进 `plan_version`，不会把 webSearch 结果当成指令，也不会替用户确认取消。页面上能看到很多状态，但这些状态的 owner 仍然是 Router、SlowTask、Event Journal、Tool Executor 或 Codex proposal bridge。

## Week 2 要求核查

对照 `docs/implementation/slow-system-demo-workbench-internship-plan.md`，第二周要求基本分成六类。

第一类是 `SlowTaskTimeline`。现在 timeline 可以展示创建任务、开始规划、等待用户补充、等待工具、等待用户确认、最终整理、完成和取消。比如云南菜接待主线里，第一步会看到 `SLOWTASK_CREATED`、`PLANNING_STARTED`、`WAITING_FOR_SLOT`；补充信息后能看到 `TOOL_EXECUTION_STARTED`、`WAITING_FOR_TOOL`；改到晚上后能看到 `TOOL_RESULT_MARKED_STALE`；最终输出场景能看到 `FINALIZING`、`SEMANTIC_COMMITMENT_EMITTED` 和 `COMPLETED`。

第二类是 plan version 对比。右侧 `SlowTask Facts` 里有 `Plan versions` 区块，能看到 current、superseded、completed、cancelled 等状态。用户补充约束时，旧的 `plan_version=1` 会变成 superseded，新的 `plan_version=2` 变成 current；用户中途改到晚上时，午饭版本会被晚餐版本替代。

第三类是 evidence trust label。现在 evidence 会按类型分组展示，不再把 hypothesis 混进 authoritative evidence 里。页面能看到：

- `Authoritative evidence`
- `Non-authoritative hypothesis`
- `Untrusted web evidence`
- `Stale evidence bucket`
- `Codex proposal evidence`

其中 `untrusted_web_evidence` 是专门补上的 Week 2 验收点。云南菜接待场景里有一条模拟 webSearch 候选餐厅证据，它明确标成 `UNTRUSTED_WEB_EVIDENCE`，只能作为候选证据，不能改工具策略、确认策略或 SlowTask 事实。

第四类是 confirmation panel。取消场景里右侧会出现 `Pending confirmation`，展示 confirmation scope、status 和 risk summary。Codex 可以起草确认提示语，但不能代替用户确认。真正的确认状态仍然由 SlowTask snapshot 拥有。

第五类是场景 2 和场景 3 mock data。现在页面里有用户补充约束、旧工具结果 stale evidence 两条场景，并且又加了一条更完整的云南菜接待主线。它覆盖：用户先请求接待午饭，系统发现缺少数据；用户补充人数、预算、忌口和时间；中途把时间改成晚上；旧午饭工具结果进入 stale evidence；最后可以输出规划，也可以在最终回答前取消。

第六类是 demo script 文档。这个文件本身也可以作为第二周 demo script：按下面的演示顺序讲，就能把 Week 2 的 timeline、plan version、evidence trust 和 confirmation 讲清楚。

## 现在页面怎么讲？

建议先讲一句总原则：页面不是另一个 SlowTask runtime，它只是把慢系统账本可视化出来。左边选场景，中间像聊天记录一样显示过程，右边显示 SlowTask facts 和 Codex proposal。

然后按下面顺序演示。

### 1. 创建接待午饭规划

选择左侧 `接待午饭`，发送：

```text
请帮忙规划一个接待午饭，选云南菜。
```

这一步可以讲：

- Router 判断这是新复杂任务，所以是 `SPAWN_SLOW_TASK`。
- SlowTask 创建 `plan_version=1`。
- SlowTask 记录用户已经给出的事实：接待用餐、云南菜、午饭。
- SlowTask 发现还缺人数、地点锚点、预算、忌口和具体时间，所以进入 `WAITING_FOR_SLOT`。
- Codex 只能生成缺失字段 proposal，不能把计划直接变成事实。

### 2. 用户补充约束

选择 `补接待数据`，发送：

```text
8个人，客户住在公司附近，人均200以内，有两位不吃辣，最好12点半。
```

这一步可以讲：

- 这不是新任务，而是当前 active SlowTask 的 UserPatch。
- UserPatch 先作为 evidence 进入系统，不是直接改 plan。
- SlowTask 判断它是 material change 后，才推进到 `plan_version=2`。
- 右侧可以看到 memory evidence 合并了人数、预算、忌口和时间。
- 这里还有一条 `Untrusted web evidence`，说明网页或搜索结果只能作为候选证据，不能控制系统策略。

### 3. 中途把时间改到晚上

选择 `改晚上`，发送：

```text
中间我插一句，把时间修改到晚上吧。
```

这一步可以讲：

- 用户改变了核心时间约束，所以 SlowTask 推进到 `plan_version=3`。
- 原来的午饭工具查询结果即使返回，也仍然绑定旧的 `plan_version=2`。
- 旧结果会进入 `Stale evidence bucket`，不会偷偷污染当前晚餐计划。
- Codex 可以建议是否重做晚餐版本，但不能自己 adopt stale evidence。

### 4. 输出最终规划

选择 `输出规划`，发送：

```text
信息够了，请输出最终规划。
```

这一步可以讲：

- 当前版本是晚餐计划，也就是 `plan_version=3`。
- SlowTask 审查当前 evidence 和工具结果后进入 `FINALIZING`。
- 最终回答必须来自 `SEMANTIC_COMMITMENT_EMITTED`。
- Composer 只负责把事实说清楚，不能改写人数、预算、忌口、时间这些事实。

### 5. 最终回答前取消

也可以不输出最终规划，选择 `取消规划`，发送：

```text
在最终回答之前取消规划吧。
```

这一步可以讲：

- 取消请求仍然要走 turn ingress 和 UserPatch。
- Router 只标注 `CANCEL_OR_PAUSE_CANDIDATE`，不能直接取消任务。
- SlowTask 解释为 explicit cancel 后，才进入 `SLOWTASK_CANCELLED`。
- 取消后不会再输出最终规划，晚到的工具结果也只能作为 stale/debug evidence。

## 本周补齐的关键点

这周重点补了三个体验问题。

第一个是聊天记录不再刷新。以前页面更像“输入一个问题，中心区域换一次结果”。现在它更像一个过程流：用户消息、系统分析、Codex 调用进度、工具状态、最终回答都会留在同一条 transcript 里。

第二个是 evidence trust label 更清楚。之前 hypothesis 和 authoritative evidence 容易被放在同一个区域里，外部观众不一定能看懂哪些是事实、哪些只是推测。现在 evidence 按 trust 分组展示，`untrusted_web_evidence` 也有单独区域。

第三个是 demo 故事线更完整。云南菜接待规划比单独的客户行程 mock 更容易讲：先缺数据，再补数据，中途改时间，旧工具结果 stale，最后输出或取消。这个流程比较接近真实对话，也更容易展示慢系统为什么需要 `plan_version` 和 evidence journal。

## 如何启动网页？

从仓库根目录进入前端 workspace：

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

然后打开：

```text
http://127.0.0.1:5173/
```

如果只是想稳定演示，不想依赖本地 Codex CLI，可以在右侧 provider 下拉框选择 `Python fake provider (manual fallback)`。无论 fake provider 还是 local Codex CLI，Codex 都只返回 proposal，不会直接推进 SlowTask facts。

## 本周验证

前端测试覆盖了 Week 2 关键 UI 行为：

- mock 场景可以选择并发送；
- 聊天记录会保留历史消息；
- 用户补充约束能看到 `plan_version=2`；
- 旧工具结果能进入 `Stale evidence bucket`；
- 取消场景能看到 pending confirmation；
- evidence trust label 能区分 authoritative 和 untrusted web evidence。

后端相关 replay/session 测试也继续通过，说明 stale evidence 和 UserPatch 的基础边界没有被前端改动破坏。
