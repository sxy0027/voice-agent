# Slow-system Workbench 5-minute demo

This script demonstrates the dynamic Python-owned closed loop. The default path
uses the authenticated local Codex CLI; the explicit fake override below does
not need credentials or network access.

## 0:00–0:30 — start the default local Codex CLI path

From the repository root:

```bash
export VOICE_AGENT_WORKBENCH_CODEX_MODE=codex_cli_local
export VOICE_AGENT_ALLOW_LOCAL_CODEX_CLI=1
cd frontend
npm run dev -- --host 127.0.0.1
```

Open the Vite URL. The default adapter attempts the local `codex` CLI and its
capability matrix is `output_mode=real` when the CLI is configured. If the CLI
is unavailable, the snapshot remains usable and the provider trace is marked
`degraded`.

For a credential-free deterministic run, replace the first two exports with:

```bash
export VOICE_AGENT_WORKBENCH_CODEX_MODE=fake
export VOICE_AGENT_ALLOW_LOCAL_CODEX_CLI=0
```

## 0:30–1:20 — create plan 1

Enter:

```text
帮我规划一个两天的客户来访行程，地点尽量靠近公司。
```

Click `Run Router + Codex analysis`. Point out:

- Router: `SPAWN_SLOW_TASK` / `NEW_TASK_CANDIDATE`;
- task lifecycle: `EXECUTING`, current `plan_version=1`;
- Codex proposal: validated candidate / `proposal_only`, not a fact;
- timeline: `SLOWTASK_CREATED`, `PLANNING_STARTED`, `TOOL_PREVIEW_AVAILABLE`,
  `TOOL_EXECUTION_AUTHORIZED`, `TOOL_EXECUTION_STARTED`, `WAITING_FOR_TOOL`;
- in-flight tool: `demo.itinerary.search` in the demo sandbox.

The context panel shows a context hash, a synthetic/redacted prompt preview,
capability matrices, and timing fields. No provider body is displayed.

## 1:20–2:40 — material patch and stale result

Enter:

```text
改成明天上午，并且预算控制在 500 元以内。
```

Run again. Point out:

- Router: `PATCH_ACTIVE_SLOW_TASK` / `ACTIVE_TASK_PATCH`;
- `USER_PATCH_RECEIVED` is evidence bound to plan 1;
- `PLAN_VERSION_ADVANCED` moves the current task to plan 2;
- the same old tool handle returns a progressive result bound to plan 1;
- `TOOL_RESULT_MARKED_STALE` and `STALE_EVIDENCE_RECORDED` prevent the old
  result from advancing plan 2.

The UI should show one item in the stale evidence bucket while the task remains
in `PLANNING` at plan 2. The provider trace and timing remain separate from
canonical event timeline entries.

## 2:40–4:10 — cancellation confirmation

Start a fresh scenario/session or reload the page, then enter:

```text
规划一个两天的客户来访行程。
```

Run once. Enter:

```text
取消这个任务。
```

Run again. Point out that the task is now
`WAITING_FOR_USER_CONFIRMATION`, not cancelled. Click `Confirm cancellation`.

The final timeline must contain:

```text
CONFIRMATION_ACCEPTED
SLOWTASK_CANCEL_REQUESTED
SLOWTASK_CANCELLED
TOOL_EXECUTION_CANCELLED
```

The router focus is cleared, no in-flight tool is shown, and the task terminal
outcome is `CANCELLED`.

## 4:10–5:00 — API/replay/safety proof

From another terminal, while Vite is running:

```bash
curl -sS -X POST http://127.0.0.1:5173/api/workbench/sessions \
  -H 'content-type: application/json' \
  -d '{"session_id":"five_minute_api"}'

curl -sS -X POST \
  http://127.0.0.1:5173/api/workbench/sessions/five_minute_api/messages \
  -H 'content-type: application/json' \
  -d '{"text":"规划两天客户行程","action":"start"}'

curl -sN \
  http://127.0.0.1:5173/api/workbench/sessions/five_minute_api/stream
```

Show that the SSE payload is a public snapshot with `safety.*` flags, replay
digest, context hash, and capability matrices—not raw journal events. Finish by
running `./scripts/test -q`; replay is deterministic and does not rerun provider
or tool work.
