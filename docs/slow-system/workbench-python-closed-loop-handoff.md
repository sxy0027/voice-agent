# Python-owned Slow-system Workbench handoff

日期：2026-07-13

## 一句话状态

text-first Slow-system Workbench 的闭环已经落在一个长期存活的 Python
session manager 上。React 不再拥有动态 SlowTask facts；它只消费 Python
public snapshot，并通过 Vite endpoint 发送用户消息和 confirmation。

## 当前 ownership

| boundary | owner | 关键产物 |
| --- | --- | --- |
| text ingress / turn commit | `access_layer` + Interaction Controller | `TEXT_INPUT_RECEIVED`, `TURN_*` |
| route classification | MVP-1 Router | `ROUTER_DECISION_EMITTED`, `TASK_FOCUS_STATE_UPDATED` |
| task facts | `MockSlowTaskRuntime` + `SlowTaskState` | lifecycle, plan, evidence, stale, confirmation |
| model/provider output | `CodexSlowLLMAdapter` | validated evidence candidate, provider trace |
| tool lifecycle | `DemoToolExecutor` + in-memory backend | progressive tool events and sandbox result |
| public UI state | snapshot projector | allow-listed task/router/timeline/context/replay/safety + live progress |
| UI controls | React | input, display, provider selection, confirmation button |

No provider endpoint is called from business modules or React. The optional local
Codex CLI is invoked only by the async adapter. No new ADR or canonical event
name was needed; the implementation composes existing ADR-002/004/005/006/007/
011/013/016 events and policies.

## Runtime sequence

```text
create session + open SSE stream
  -> POST message (same session)
  -> TEXT_INPUT_RECEIVED
  -> TURN_INGRESS_COMMITTED
  -> mock ASR / mock Thinker evidence
  -> RouterDecision
  -> SlowTask create / patch / confirmation path
  -> Codex adapter candidate (default local Codex CLI, explicit fake fallback)
  -> SlowTask evidence review
  -> Tool Executor progressive events
  -> reducer projection + context pack/hash + public snapshot
  -> live `streaming` / `live_progress` snapshots while async provider is waiting
```

The first itinerary message creates plan 1 and leaves
`demo.itinerary.search` in-flight. A material second message is recorded as a
plan-1 UserPatch, advances the task to plan 2, then completes the same old tool
handle. The old result is bound to plan 1 and is recorded as stale. A cancel
message produces a `TASK_CANCEL` confirmation; only the confirmation endpoint
can lead to `CANCELLED`.

## Public API

The Vite dev server exposes:

```text
POST /api/workbench/sessions
GET  /api/workbench/sessions/{session_id}/snapshot
POST /api/workbench/sessions/{session_id}/messages
POST /api/workbench/sessions/{session_id}/confirmations/{confirmation_id}
POST /api/workbench/sessions/{session_id}/reset
GET  /api/workbench/sessions/{session_id}/stream
```

`GET /stream` sends only snapshot JSON in SSE `snapshot` events. It polls the
Python-owned session while a message is in flight, including safe
`streaming.active`, `streaming.phase`, and bounded `live_progress` entries. It
never sends raw journal payloads, provider bodies, hidden reasoning, or prompt
text.

## Files to start from

- Python session orchestration: `src/voice_agent/runtime/slow_system_workbench_sessions.py`
- JSONL API process: `src/voice_agent/runtime/slow_system_workbench_api.py`
- context pack: `src/voice_agent/runtime/slow_system_workbench_context.py`
- public projection: `src/voice_agent/runtime/slow_system_workbench_snapshots.py`
- async Codex adapter: `src/voice_agent/adapters/codex_slow_llm.py`
- delayed tool handle: `src/voice_agent/tools/executor.py`
- deterministic itinerary backend/manifests: `src/voice_agent/demo_backend/in_memory.py`, `src/voice_agent/tools/demo_manifests.py`
- Vite worker/routes: `frontend/workbenchCodexPlugin.ts`
- frontend wire adapter: `frontend/src/adapters/codexProposalClient.ts`
- dynamic UI scenario mapping: `frontend/src/App.tsx`
- acceptance tests: `tests/runtime/test_slow_system_workbench_sessions.py`

## Verification checkpoint

```bash
./scripts/test -q
cd frontend && npm test -- --run && npm run build
```

Checkpoint result: Python and frontend tests pass, and Vite
production build succeeded. The new session tests specifically cover fake
start, plan 1 -> plan 2 stale result, cancel confirmation, reset, deterministic
context/state digest, API manager behavior, JSONL progress redaction, and a
snapshot observed while the provider is still waiting.

## Safe next work

- Add a browser-level test that drives the REST/SSE adapter without changing
  Python ownership.
- If real Codex CLI behavior is evaluated, record only synthetic aggregate
  timing/output-mode metadata and use an explicit human-approved local setup.
- Keep real ASR/Thinker/TTS, production persistence, multi-active tasks,
  pause/resume, and external side-effect tools outside this slice unless a new
  ADR changes the scope.
