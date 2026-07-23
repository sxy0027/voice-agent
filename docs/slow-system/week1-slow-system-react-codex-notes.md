# Week 1 慢系统 React / SlowTask / Codex 理解笔记

这周的 demo 仍然不让前端改变慢系统事实。它把 internship plan 里的必做 mock 场景做成一个可输入、可点击的 workbench：用户把 mocklist 里的消息输入页面，页面按 Router 规定展示默认分类，再展示 SlowTask 如何处理该类信息，以及 Codex proposal 如何只能作为建议进入系统边界。

## React 拥有什么？

React 只拥有页面展示状态。

比如：当前选中的 mock scenario、mock 输入框内容、点中了哪一条 timeline event、Codex provider 模式、proposal loading/error、以及 accept/reject 这类本地展示状态。这些状态只影响页面怎么看，不影响慢系统事实是什么。

所以 React 可以显示 `plan_version`，但必须说清楚它是 read-only / owned by SlowTask。React 不能自己创建、修改或推进 `plan_version`，也不能解释证据真假、确认状态或工具授权。

## SlowTask 拥有什么？

SlowTask 拥有复杂任务里的事实。

包括 `task_id`、`plan_version`、`task_event_seq`、任务 lifecycle state、evidence review、resolved arguments、confirmation state、stale evidence、adopt/rebase 记录，以及最终的 `SemanticCommitment`。

这些才是慢系统账本里的东西。用户补充、工具结果、证据冲突、确认、取消、旧结果复用，都必须走 SlowTask 和 Event Journal 的边界，不能靠前端按钮或模型文本直接改。

## Codex 拥有什么？

Codex 只拥有 proposal / draft / 建议。

它可以帮我们生成候选实现方案、说明可能的下一步、起草 UI patch 想法，或者提醒哪些边界要注意。但这些内容在被系统边界接收之前，只是草稿。

在这个 Week 1 demo 里，Codex proposal 通过本地 Vite endpoint 调到 Python `slow_system_workbench_codex` bridge。当前页面默认选择 `Local Codex CLI`，Workbench 默认配置也启用本地 CLI；如果本机没有登录或不可用，后端会 fail closed/degraded，页面也可以手动切到 `Python fake provider` 做稳定演示。无论 proposal 来源是 fake 还是 local CLI，页面都明确写了 “Proposal only”。它不能直接写 Event Journal，不能推进 `plan_version`，不能执行工具，不能授权工具，也不能生成最终 `SemanticCommitment`。

## 为什么 Codex 只能做 proposal？

因为 ADR 里已经把复杂任务事实 owner 交给 SlowTask 了。

如果 Codex 可以直接改 `plan_version` 或工具状态，就等于绕过了 Event Journal、replay、stale evidence policy 和 confirmation gate。这样系统之后就很难解释：这个事实是谁决定的？旧工具结果有没有污染当前计划？用户确认到底有没有发生？replay 能不能还原？

所以 mentor 问到这里时，可以直接回答：Codex 的输出必须先被系统边界接收、校验、记录，才可能变成事实。在那之前，它只是 proposal，不是 SlowTask state，也不是 SemanticCommitment。

## 本周 demo 做到了什么？

本周新增了一个独立的 React/Vite workbench。它不执行工具，也不让浏览器直接调用 Codex 或外部模型；即使选择本地 Codex CLI，调用也必须先进入本地开发后端 endpoint，再由 Python adapter/bridge 产生受校验的 proposal。

页面里有四块：conversation/input、SlowTask timeline、plan/evidence、Codex proposal。必做场景覆盖：

- 新复杂任务：Router 输出 `SPAWN_SLOW_TASK` / `NEW_TASK_CANDIDATE`，SlowTask 建 plan_version=1 并请求缺失槽位。
- 重大用户补充：Router 输出 `PATCH_ACTIVE_SLOW_TASK` / `ACTIVE_TASK_PATCH`，SlowTask 接收 UserPatch 并把 plan_version 从 1 推进到 2。
- 旧 plan 工具结果返回：ToolResult 绑定旧 `plan_version=1`，当前已经是 2，因此进入 `stale_evidence`，不会推进当前任务。
- 取消候选：Router 输出 `PATCH_ACTIVE_SLOW_TASK` / `CANCEL_OR_PAUSE_CANDIDATE`，SlowTask 进入 `WAITING_FOR_USER_CONFIRMATION`。

`slowSystem.ts` 里把 ownership 写进类型：React 是 UI state，SlowTask 是事实账本，Codex 是 proposal。所有 `plan_version` 和 `task_event_seq` 都是只读展示，来源写成 SlowTask mock event，不是 React 拥有。

## 如何启动网页？

从仓库根目录进入前端 workspace：

```bash
cd frontend
npm install
npm run dev
```

然后打开 Vite 打印出来的地址，通常是：

```text
http://localhost:5173
```

如果想固定监听本机地址，可以运行：

```bash
npm run dev -- --host 127.0.0.1
```

页面打开后，左侧选择一个 mock 场景，中间输入框会填入对应消息；点击 `Run Router + Codex analysis` 后，浏览器会请求 `/api/workbench/codex-proposal`。这个路径不是远端服务，而是 Vite dev server 里的本地 endpoint：它把请求交给 Python `slow_system_workbench_codex` bridge，再把校验后的 proposal JSON 返回给页面。

如果使用默认的 `Local Codex CLI` provider，需要本机已经能运行并登录 `codex`。如果只是想稳定查看 demo 流程，可以在右侧 provider 下拉框选择 `Python fake provider (manual fallback)`。
