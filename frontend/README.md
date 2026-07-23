# Slow-system Workbench

这是 voice-agent 的 text-first Slow-system Workbench。React 只负责输入、展示和
选择 provider；Python 进程拥有 per-session `InMemoryEventJournal`、Router、
SlowTask、UserPatch、Demo Tool Executor、reducer projection 和 public snapshot。
页面运行后不再把静态 scenario 当作事实源；旧 scenario 只保留为左侧快捷问题和快速填充
输入的 demo preset，以及没有 EventSource 的测试兼容 fallback。

## 启动

从仓库根目录运行：

```bash
cd frontend
npm run dev -- --host 127.0.0.1
```

打开 Vite 打印的地址，通常是 `http://127.0.0.1:5173`。

Python worker 由 Vite plugin 启动一次并保持存活。它使用 `PYTHONPATH=<repo>/src`
启动 `python -m voice_agent.runtime.slow_system_workbench_api`，通过 JSON Lines
接收请求；不会为每个按钮点击重新启动一个 Python 进程。

## Provider 模式

默认 Python 配置是本地 Codex CLI，并且允许 Workbench 调用本机 `codex`：

```text
provider_mode=codex_cli_local
allow_local_codex_cli=true
```

也可以用环境变量显式固定这个默认配置：

```bash
export VOICE_AGENT_WORKBENCH_CODEX_MODE=codex_cli_local
export VOICE_AGENT_ALLOW_LOCAL_CODEX_CLI=1
# 可选：只有设置后才会传给 CLI；不写死未经验证的 model
export VOICE_AGENT_CODEX_MODEL=<your-configured-local-model>
export VOICE_AGENT_CODEX_REASONING_EFFORT=<configured-effort>
export VOICE_AGENT_CODEX_BIN=codex
```

如果本机没有 Codex CLI、CLI 超时或输出无法通过 schema validation，adapter 会
安全地进入 degraded/fallback 路径。需要无 credential、完全确定性的测试或演示时，
显式设置：

```bash
export VOICE_AGENT_WORKBENCH_CODEX_MODE=fake
export VOICE_AGENT_ALLOW_LOCAL_CODEX_CLI=0
```

adapter 使用 read-only、ephemeral、JSONL、output schema 约束；Codex 只能返回
经过 schema validation 的 evidence/proposal candidate。CLI 不可用、超时、JSON
不完整或 schema 失败时会 fallback 到 deterministic fake，并在 snapshot 的
`provider_trace` 和 `ADAPTER_OUTPUT_DEGRADED` 中明确标注 `degraded`。CLI 配置
不会把 auth 文件、token、raw stdout/stderr、prompt dump 或本地路径写进 journal
或浏览器响应。

## HTTP / SSE API

Vite dev server 暴露以下 Python-owned endpoints：

```text
POST /api/workbench/sessions
GET  /api/workbench/sessions/{session_id}/snapshot
POST /api/workbench/sessions/{session_id}/messages
POST /api/workbench/sessions/{session_id}/confirmations/{confirmation_id}
POST /api/workbench/sessions/{session_id}/reset
GET  /api/workbench/sessions/{session_id}/stream
```

最小请求示例：

```bash
curl -sS -X POST http://127.0.0.1:5173/api/workbench/sessions \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo_session"}'

curl -sS -X POST http://127.0.0.1:5173/api/workbench/sessions/demo_session/messages \
  -H 'content-type: application/json' \
  -d '{"text":"规划两天客户来访行程，地点靠近公司。","action":"start"}'

curl -sN http://127.0.0.1:5173/api/workbench/sessions/demo_session/stream
```

浏览器实际运行时会先创建 session、打开 `/stream`，再发送 `/messages`。这样 Codex
CLI 还在运行时，SSE 就能把当前 snapshot 推回页面；Python worker 会并发处理
snapshot 轮询和正在执行的消息请求。

`POST /messages` 的 `action` 是可选的 demo/test hint；真实文本仍先进入
`TEXT_INPUT_RECEIVED`、`TURN_INGRESS_COMMITTED`、mock ASR/Thinker frame 和
MVP-1 Router。可用 hint 包括 `start_new_task`、`send_user_patch`、
`request_cancel_confirmation`、`adopt_stale_evidence` 和 `complete_current`。

## 动态闭环

输入第一条复杂任务时，Python 会生成 `plan_version=1`，让 fake/real/degraded
Codex adapter 产生 proposal-only structured evidence，SlowTask 审阅参数
provenance，然后由 Demo Tool Executor 发出 manifest、arguments、preview、
authorization、started 和 waiting events。

工具处于 in-flight 时再输入 material patch，Python 会：

- 通过 `UserPatchEvidencePackRuntime` 记录绑定在 plan 1 的 `USER_PATCH_RECEIVED`；
- 由 `MockSlowTaskRuntime` 推进 `PLAN_VERSION_ADVANCED` 到 plan 2；
- 让同一个已开始的 Tool Executor handle 晚到返回旧 plan 的 progressive result；
- 记录 `TOOL_RESULT_MARKED_STALE` -> `STALE_EVIDENCE_RECORDED`，不推进 plan 2。

取消消息不会直接写 `CANCELLED`。它先进入 `TASK_CANCEL` confirmation；只有
confirmation endpoint 的 accepted response 才会生成 `CONFIRMATION_ACCEPTED`、
`SLOWTASK_CANCEL_REQUESTED`、`SLOWTASK_CANCELLED`，并通过 Tool Executor 关闭
in-flight demo call。

snapshot 只返回 allow-listed projection：task/router/conversation/timeline、
provider trace、当前回合的 `live_progress`、`streaming` 状态、Codex proposal、
context pack/hash、synthetic prompt preview、timing、capability matrices、replay
digest 和 safety flags。`live_progress` 只保留阶段、事件状态、工具名称、usage
和校验结果等高层信息；不会返回 raw event payload、raw provider body、chain-of-thought、
raw audio、credential 或本地路径。页面因此可以显示“Codex 正在执行哪个步骤”，但不会
伪装成展示隐藏思维原文。

## 测试

Python 统一入口：

```bash
./scripts/test -q
```

前端：

```bash
cd frontend
npm test -- --run
npm run build
```

本次闭环验收包含动态 session 的创建、SSE 中间 snapshot、Codex JSONL 逐行进度、
plan 1/plan 2、stale result、取消 confirmation、context hash/prompt preview、
provider/tool timing、fake/degraded 标记、deterministic snapshot replay，以及现有
MVP-0/MVP-1/MVP-2 回归。

## 边界和 non-goals

- 没有新增 canonical event name 或 ADR；所有状态迁移使用现有 ADR-002 registry。
- 工具仍只运行在 deterministic in-memory demo backend；没有预订、支付、删除、
  外部写操作、外部通信或真实设备副作用。
- `pause/resume`、多 active SlowTask、生产持久化、生产 auth/privacy policy 和
  浏览器直连 provider 不在本次 scope。
- replay 只重建已经记录的 reducer state，不重新运行 provider、tool、network、
  clock 或 random。
- 本地真实 Codex CLI 是 Workbench 默认 path，但 CI/无 credential 的稳定验收路径
  应显式设置 `VOICE_AGENT_WORKBENCH_CODEX_MODE=fake`。
