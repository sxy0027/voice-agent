# Workbench Codex 进度和用户回复修复记录

## 背景

本次修复针对 Slow-system Workbench 里两个比较明显的体验问题：

1. `Codex 执行进度` 面板在任务还没开始时，就已经显示“分析缺失字段”“分析缺失字段完成”等步骤。这个会让观众误以为 Codex 已经开始工作，但实际上只是页面选中了一个静态 mock 场景。
2. Codex/Workbench 最后的回复更像给开发者看的 debug 摘要，会直接提 `Router`、`plan_version`、`proposal` 等术语。对真实用户来说，它应该更像客服：信息不够时先礼貌追问，等用户补充关键时间、地点后再继续推进。

## 发现的问题

### 1. 静态 mock progress 被当成真实进度

前端的 `runScenario` 在未运行时会指向当前选中的场景，而部分 mock scenario 自带 `liveProgress`。因此只要左侧选中场景，中间的 `CodexProgressFeed` 就可能读到这些静态进度并展示出来。

这违反了 ADR-013 的精神：进度反馈必须来自真实状态事件或当前运行态，而不是提前显示。

修复方式：

- `CodexProgressFeed` 新增 `hasRun` 输入。
- 只有 `hasRun=true` 且正在 loading 或存在本轮 live progress 时，才显示执行进度。
- 前端测试补充了断言：初始页面不应该出现 `Codex 执行进度`。

### 2. 进度信息太粗

原来页面只能看到 `kind/status/tool_name` 这类很短的信息，例如 `item.completed`。这对调试不够友好，无法说明 Codex 当前公开地做到了哪一步。

修复方式：

- 后端 `ProviderTraceItem` 增加 allow-listed 字段：
  - `orchestration_role`
  - `subtask_id`
  - `subtask_goal`
  - `public_thought`
  - `tool_input_summary`
  - `tool_output_summary`
  - `next_step`
  - `blocked_on_user`
- 前端进度面板展示这些字段。
- 这些字段都是公开摘要，不是 chain-of-thought，也不是 raw provider body。

现在可以展示类似：

- Codex 主控已读取受控上下文。
- 子任务 `slot_gap_analysis` 检查时间、地点等关键槽位。
- 子任务 `tool_candidate_review` 审查 demo 工具候选。
- 如果缺槽位，显示 `blocked_on_user=true` 和下一步“等待用户补充”。

### 3. 缺时间/地点时仍继续推进

原来的 fake proposal 默认 `missing_fields=[]`，初始任务路径也会直接进入 `demo.itinerary.search`。这导致用户没给具体时间或地点时，系统仍然继续查行程。

修复方式：

- Workbench session 增加轻量槽位检查：
  - `time_window`
  - `location_anchor`
- 初始复杂任务如果缺关键槽位：
  - SlowTask 写入 `EVIDENCE_REVIEWED`
  - 写入 `INSUFFICIENT_EVIDENCE_FOR_ACTION`
  - 写入 `CLARIFICATION_REQUESTED`
  - 写入 `WAITING_FOR_SLOT`
  - lifecycle 变成 `WAITING_FOR_SLOT`
  - 不启动 `DemoToolExecutor`
- 用户下一句补充时间/地点后：
  - 作为 `UserPatch` 进入 event journal
  - SlowTask 推进 `plan_version`
  - 重新检查槽位
  - 补齐后才启动 demo tool

这不是新增 canonical event name，只是使用已有 ADR-004 / ADR-007 / ADR-016 / ADR-013 规定的事件路径。

## 关于“Codex 主线程调用子线程”的可行性判断

这个想法从交互和调试角度是有价值的：一个主控 Codex 负责保存上下文和任务拆解，再把子任务分给其他 Codex 子线程，可以让复杂任务的执行过程更清晰。

但是在当前 Workbench/MVP 架构里，不适合直接做成真实的“多个 Codex 会话自治调度”，原因是：

- Codex 不能成为事实源。真实多线程 Codex 如果自己维护上下文和子任务状态，容易绕过 SlowTask 和 Event Journal。
- 子线程之间的上下文传递、取消、超时、成本、权限、provider trace 都需要新的 runtime contract。
- 如果让 Codex 子线程直接调用工具，会违反 ADR-005，工具必须走 Tool Executor。
- 如果让 Codex 子线程推进 plan 或确认，会违反 ADR-004 / ADR-016。
- 这属于新增架构能力，严格来说需要先写 ADR。

因此这次采用了一个安全可落地的版本：

```text
Python Workbench Session
  -> Event Journal / SlowTask 仍然是事实源
  -> CodexSlowLLMAdapter 作为一个 adapter
  -> adapter 内展示 public orchestration trace
     -> main_thread: 读取受控上下文
     -> subtask: 检查缺槽位
     -> subtask: 审查工具候选
     -> provider: 真实/假 Codex 输出结构化候选
  -> Tool Executor 决定是否真正启动 demo tool
```

也就是说，现在实现的是“主控/子任务的公开执行轨迹”，不是“Codex 自己拥有任务状态”。这样既能满足演示和调试需要，也不会破坏慢系统边界。

后续如果要做真实多 Codex 子线程，应先新增 ADR，至少定义：

- 子线程生命周期和取消策略。
- 子线程输出 schema。
- 子线程 trace redaction。
- 主线程如何引用子线程结果。
- 子线程结果如何作为 evidence candidate 进入 SlowTask。
- 子线程不得授权工具、不得推进 plan_version 的验证方法。

## 面向用户回复的变化

现在聊天区的最终回复更偏用户侧：

- 缺信息时：告诉用户已经记录任务，但还缺具体时间或地点，所以先等用户补充。
- 信息够时：告诉用户会先按范围查一个沙盒里的候选方案。
- 需要确认时：展示确认提示和风险说明。
- 已取消时：告诉用户任务已按确认取消。

调试信息仍然保留在：

- SlowTask timeline
- SlowTask facts
- Codex proposal panel
- live progress panel

这样用户看到的是自然交互，开发者仍然能调试底层状态。

## 人工测试指令

从仓库根目录运行：

```bash
cd /Users/shixinyue/Documents/本科/暑期实习/voice-agent
./scripts/test tests/runtime/test_slow_system_workbench_sessions.py tests/runtime/test_slow_system_workbench_codex.py
```

前端测试和构建：

```bash
cd /Users/shixinyue/Documents/本科/暑期实习/voice-agent/frontend
npm test -- --run
npm run build
```

本地人工打开页面：

```bash
cd /Users/shixinyue/Documents/本科/暑期实习/voice-agent/frontend
npm run dev -- --host 127.0.0.1
```

打开 Vite 输出的地址，一般是：

```text
http://127.0.0.1:5173
```

建议人工测试流程：

1. 刚打开页面，不要点击运行。
   - 预期：页面不显示 `Codex 执行进度`。
   - 预期：右侧显示 `No SlowTask facts yet`。

2. 输入缺时间的任务：

```text
帮我规划一个两天的客户来访行程，地点尽量靠近公司。
```

   - 预期：SlowTask lifecycle 进入 `WAITING_FOR_SLOT`。
   - 预期：不会出现 in-flight tool call。
   - 预期：回复面向用户，提示还缺“具体时间”。
   - 预期：进度里能看到 `slot_gap_analysis`，并且 `blocked_on_user=true`。

3. 继续补充：

```text
明天上午。
```

   - 预期：这条输入作为 `UserPatch`。
   - 预期：`plan_version` 推进。
   - 预期：信息补齐后才启动 `demo.itinerary.search`。

4. 输入完整任务：

```text
明天上午在公司附近规划两天客户来访行程。
```

   - 预期：可以进入工具候选审查和 demo tool running。
   - 预期：进度中能看到工具名、工具输入摘要、下一步。

5. 在工具还未完成时修改任务：

```text
改成明天下午，预算控制在 500 元以内。
```

   - 预期：旧 plan 的工具结果如果返回，只进入 stale evidence。
   - 预期：不会偷偷推进 current plan。

## 当前限制

- 现在的“主控/子任务”是 adapter 内部公开 trace，不是真实多个 Codex 会话。
- 页面展示的是安全摘要，不展示隐藏思维链、原始 provider body、stderr 原文、prompt dump、本地路径或凭据。
- 槽位检查目前是 Workbench demo 规则，只覆盖时间和地点锚点，后续可以扩展人数、预算、偏好等字段。
- 真实生产级多 agent / 多 Codex 子线程需要新增 ADR 后再做。

## 追加修复：多轮上下文里反复追问的问题

后续人工测试发现一个更严重的问题：用户明明已经在第二轮补充了时间和地点，Workbench 仍然继续追问“具体时间、地点范围”。典型对话是：

```text
用户：请帮忙规划一个接待午饭，选云南菜。
系统：还缺 company_location / days / 具体时间。
用户：公司位置在北京中关村领展附近，具体时间为 7 月 25 号中午 12 点。
系统：还缺具体时间、地点范围。
```

根因有两个：

1. `Codex proposal.missing_fields` 被前端当成了用户回复的事实来源。真实 Codex 或 fallback proposal 可能会基于旧 context、自己的推断或过度谨慎继续返回 missing fields。
2. UserPatch 之后调用 Codex 的时间太早。当时 SlowTask 还没有重新 review 当前槽位，所以 `TaskContextPack.missing_fields` 可能仍然保留上一轮的缺槽位状态。

修复原则：

- 缺什么由 Workbench/SlowTask 的当前槽位状态决定，不由 Codex proposal 决定。
- Codex 可以给建议，但不能驱动最终追问。
- 前端只有在 `lifecycle=WAITING_FOR_SLOT` 时，才允许展示缺字段追问。
- 如果当前 task 已经进入 `EXECUTING`，即使 proposal 里带了旧 `missing_fields`，也不能再次追问用户。

代码层修复：

- `CodexSlowLLMAdapter.propose(...)` 增加 `authoritative_missing_fields` 参数。
- Workbench session 在调用 Codex 前先计算 `_missing_critical_slots(self._slot_values)`，并把这个结果显式传给 adapter。
- `_proposal_from_structured_output(...)` 只使用这组 authoritative missing fields；不会让 provider 自己发明的缺字段进入用户追问路径。
- `SlowTaskSnapshot` 前端类型增加 `missingFields` / `conflictingFields`，由 Python snapshot 映射而来。
- `assistantTranscriptTurn(...)` 只在 `WAITING_FOR_SLOT` 状态下使用 `scenario.slowTask.missingFields` 生成追问。
- 增强 demo 槽位抽取，覆盖：
  - `北京中关村领展附近`
  - `7 月 25 号中午 12 点`
  - `8个人`
  - `人均200以内`
  - `不吃辣`
  - `不需要发票`

另外加了一个反馈类路由规则：用户说“我不是已经给过信息了吗”时，不再当成 material patch 推进 plan，而是作为 foreground feedback 回复：

```text
你说得对，我会沿用前面已经记录的信息，不会要求你重复补充。
```

新增回归测试：

```bash
cd /Users/shixinyue/Documents/本科/暑期实习/voice-agent
./scripts/test tests/runtime/test_slow_system_workbench_sessions.py -q
```

重点覆盖：

1. 第一轮 `请帮忙规划一个接待午饭，选云南菜。` 缺地点时进入 `WAITING_FOR_SLOT`。
2. 第二轮 `公司位置在北京中关村领展附近，具体时间为 7 月 25 号中午 12 点` 后，进入 `EXECUTING`，`missing_fields=[]`。
3. 第三轮 `我不是已经把信息给你了吗` 不推进 `plan_version`，不重复追问。
