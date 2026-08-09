# Stage B ADR 决策登记表

本文件登记 Stage B 已接受 ADR。每个 accepted ADR 的正文位于 `docs/adr/`。

## Status

accepted register。

## ADR Register

| ADR | 标题 | 状态 | MVP 范围 | 文件 |
| --- | --- | --- | --- | --- |
| ADR-001 | Duplex Boundary and Interaction Controller | accepted | MVP-0 | `docs/adr/ADR-001 Duplex Boundary and Interaction Controller.md` |
| ADR-002 | Event Journal, Timing Model, and Replay Foundation | accepted | MVP-0 / MVP-1 / MVP-2 / MVP-3 | `docs/adr/ADR-002 Event Journal, Timing Model, and Replay Foundation.md` |
| ADR-003 | Barge-in and TTS Truncate Contract | accepted | MVP-0 | `docs/adr/ADR-003 Barge-in and TTS Truncate Contract.md` |
| ADR-004 | SlowTask Plan Versioning and Stale Result Policy | accepted | MVP-1 | `docs/adr/ADR-004 SlowTask Plan Versioning and Stale Result Policy.md` |
| ADR-005 | Demo Tool Sandbox, Progressive Tool Invocation, and Side Effect Policy | accepted | MVP-2 | `docs/adr/ADR-005 Demo Tool Sandbox, Progressive Tool Invocation, and Side Effect Policy.md` |
| ADR-006 | Router Task Focus and Single Active SlowTask MVP | accepted | MVP-1 | `docs/adr/ADR-006 Router Task Focus and Single Active SlowTask MVP.md` |
| ADR-007 | UserPatch Evidence Pack | accepted | MVP-1 | `docs/adr/ADR-007 UserPatch Evidence Pack.md` |
| ADR-008 | ASR / Thinker Evidence Fusion and SlowTask-led Conflict Resolution | accepted | MVP-1 / MVP-2 | `docs/adr/ADR-008 ASR Thinker Evidence Fusion and SlowTask-led Conflict Resolution.md` |
| ADR-009 | SemanticCommitment and Thinker-as-Composer Contract | accepted | MVP-2 | `docs/adr/ADR-009 SemanticCommitment and Thinker-as-Composer Contract.md` |
| ADR-010 | Trace / Replay Debug Policy for Web Demo | accepted | MVP-0 / MVP-2 | `docs/adr/ADR-010 Trace Replay Debug Policy for Web Demo.md` |
| ADR-011 | Model Adapter Capability Contract | accepted | MVP-0 / MVP-3 | `docs/adr/ADR-011 Model Adapter Capability Contract.md` |
| ADR-012 | MVP Vertical Slice and Development SLOs | accepted | MVP-0 / MVP-1 / MVP-2 / MVP-3 | `docs/adr/ADR-012 MVP Vertical Slice and Development SLOs.md` |
| ADR-013 | Truthful Progress Feedback | accepted | MVP-2 | `docs/adr/ADR-013 Truthful Progress Feedback.md` |
| ADR-014 | webSearch Evidence Boundary for Demo Tools | accepted | MVP-2 / MVP-3 | `docs/adr/ADR-014 webSearch Evidence Boundary for Demo Tools.md` |
| ADR-015 | Repository Governance and AGENTS.md Rules | accepted | MVP-0 / MVP-1 / MVP-2 / MVP-3 | `docs/adr/ADR-015 Repository Governance and AGENTS.md Rules.md` |
| ADR-016 | SlowTask Lifecycle and Confirmation State Contract | accepted | MVP-1 / MVP-2 | `docs/adr/ADR-016 SlowTask Lifecycle and Confirmation State Contract.md` |
| ADR-017 | Role-Based Slow LLM Orchestration and Dynamic Task Requirement Model | accepted | MVP-1 / MVP-2 / MVP-3 | `docs/adr/ADR-017 Role-Based Slow LLM Orchestration and Dynamic Task Requirement Model.md` |

## 使用规则

- 实现核心边界前，先查本 register，再打开对应 ADR 正文。
- 本 register 只登记状态和路径，不替代 ADR 内容。
- 新增 MVP-relevant 架构能力、事件名或职责边界变更时，必须新增或修改 ADR，并同步更新本登记表。
