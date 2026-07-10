# Week 1 Slow-System React Prototype

This is an independent Vite + React + TypeScript prototype for the slow-system
Demo Workbench. It runs the required mock scenarios locally, classifies them
with ADR-006 Router vocabulary, and shows how SlowTask would handle each case.
The React UI does not own or mutate SlowTask facts.

## Local Commands

```bash
cd frontend
npm install
npm run dev
npm run test
npm run build
```

Open the Vite URL printed by `npm run dev`, usually:

```text
http://localhost:5173
```

## Boundary Notes

- React owns display-only UI state: selected scenario, selected timeline item,
  mock input text, proposal panel state, and selected provider mode.
- SlowTask mock data owns `task_id`, `current_plan_version`,
  `current_task_event_seq`, lifecycle state, evidence review, confirmation
  status, stale evidence, and SemanticCommitment ownership.
- Codex output is not pre-rendered from static mock data. The UI shows an empty
  Codex state until `Run Router + Codex analysis` or `Request backend proposal`
  calls the local Vite development endpoint, which invokes the Python
  `slow_system_workbench_codex` bridge.
- The default provider is `Local Codex CLI` with explicit local opt-in enabled.
  `Python fake provider` remains available only as a manual fallback for
  deterministic demos.
- Validated proposals still cannot mutate SlowTask facts, advance
  `plan_version`, authorize tools, emit canonical events, or write Event
  Journal entries.

The page covers the required demo scenarios from
`docs/implementation/slow-system-demo-workbench-internship-plan.md`:

- new complex task -> `SPAWN_SLOW_TASK`
- material user patch -> `PATCH_ACTIVE_SLOW_TASK`
- late old-plan tool result -> stale evidence
- cancel candidate -> `WAITING_FOR_USER_CONFIRMATION`
