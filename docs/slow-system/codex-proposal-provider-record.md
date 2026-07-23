# Codex Proposal Provider Record

## Purpose

This record tracks the slow-system Workbench Codex proposal provider boundary.
It is an implementation record, not an accepted ADR.

The current feature adds a backend-local proposal provider option:

- `fake`: deterministic proposal provider for reproducible demos and tests.
- `codex_cli_unavailable`: explicit unavailable local Codex CLI path.
- `codex_cli_local`: local-only Codex CLI provider. It is the Workbench default;
  the explicit `allow_local_codex_cli` setting remains available for callers
  that need to disable local execution.

All provider modes return proposal JSON through the same bridge and validator.
Codex never becomes the SlowTask fact owner.

The provider path exposes `build_workbench_codex_proposal_capability()` so the
Workbench can show whether the proposal provider is `mock`, `real`, or
`degraded`:

- `fake` -> `output_mode=mock`
- enabled `codex_cli_local` -> `output_mode=real`
- unavailable or disabled provider -> `output_mode=degraded`

## ADR Requirements

Before implementation, check `stage_b_adr_register.md` and the accepted ADRs
that own the touched boundary.

For this feature the relevant rules are:

- `AGENTS.md`: external models must go through adapters; no secrets or raw
  provider bodies in trace or repo; every critical state transition must remain
  journaled.
- ADR-011: model access must be adapter/provider-boundary mediated, provider
  output must be normalized and schema-validated, and real/mock/degraded modes
  must be distinguishable.
- ADR-015: new architecture capability, new event names, or responsibility
  boundary changes require a new or updated ADR before implementation.
- ADR-002 / ADR-016: if a future proposal acceptance emits SlowTask events,
  those events must use canonical names and preserve `task_id`,
  `plan_version`, and `task_event_seq` ownership.

This implementation does not require a new ADR because it does not add a new
canonical event, does not change SlowTask ownership, does not authorize tools,
does not mutate `TaskSnapshot`, and does not change Event Journal semantics.
It is an optional backend provider behind the existing proposal contract.

Write or update an ADR before adding any of these:

- a new MVP-relevant canonical event name
- a new SlowTask lifecycle state
- Codex-driven plan version advancement
- Codex-driven `SemanticCommitment`
- Codex-driven tool authorization or UI state patching
- frontend direct access to Codex, provider credentials, or model output
- persistent storage of raw Codex CLI output, prompts, provider bodies, or
  local Codex auth material

## Local Codex CLI Boundary

`codex_cli_local` is for local developer demos only. It uses the authenticated
local Codex CLI session created by:

```bash
codex login
```

The historical compatibility provider runs:

```bash
codex exec --sandbox read-only --ephemeral --color never
```

The provider sends a constrained prompt asking for one proposal JSON object,
parses only a JSON object from stdout, and then returns it to
`validate_workbench_codex_proposal()`.

The provider must not:

- expose `~/.codex/auth.json`
- expose `CODEX_ACCESS_TOKEN`
- expose OpenAI API keys or provider credentials
- write raw prompt dumps, raw Codex output, or local paths into committed files
- let Codex emit canonical events
- let Codex mutate SlowTask state
- let Codex execute external tools for the slow-system Workbench

For the current persistent Python Workbench, use the async adapter record in
the section below; the historical bridge is retained only for compatibility
with the earlier static proposal contract.

## How To Keep Records

For each future change to this provider path, update this record with:

- provider mode added or changed
- whether it is fake, local-only, real, fallback, or degraded
- capability matrix impact
- required local commands or environment variables
- safety gates and validation tests
- test command and result
- ADR impact assessment

If a change is only a provider implementation detail and remains behind the
proposal contract, record it here.

If a change alters architecture ownership, canonical events, SlowTask state,
Tool Executor authorization, frontend authority, replay policy, or secret
handling, stop and write or update an ADR first.

## 2026-07-13 Workbench closed-loop record

The Workbench now has a persistent Python-owned session path in addition to the
historical proposal bridge. The new runtime is implemented by:

- `voice_agent.adapters.codex_slow_llm.CodexSlowLLMAdapter`
- `voice_agent.runtime.slow_system_workbench_sessions.WorkbenchSession`
- `voice_agent.runtime.slow_system_workbench_api.WorkbenchApi`
- `frontend/workbenchCodexPlugin.ts`'s long-lived JSONL Python worker

The adapter modes are explicit:

| configuration | capability output | runtime trace | behavior |
| --- | --- | --- | --- |
| `provider_mode=fake` | `mock` | `mock` request plus `fallback` contract output | deterministic structured candidate; no credential or network |
| `provider_mode=codex_cli_local`, local opt-in enabled and CLI succeeds | `real` | `real` | async `codex exec` with read-only/ephemeral/JSON/schema flags |
| local CLI disabled, unavailable, timeout, invalid JSONL, or schema failure | `degraded` or configured-real capability plus `degraded` trace | `degraded` | bounded fallback to the same deterministic fake candidate |

The browser-visible proposal is always `proposal_only=true`. SlowTask still owns
evidence review, resolved arguments, `plan_version`, confirmation state,
SemanticCommitment, and tool authorization. The adapter emits only the existing
`SLOW_LLM_STRUCTURED_OUTPUT_EMITTED`, `ADAPTER_OUTPUT_VALIDATION_FAILED`, and
`ADAPTER_OUTPUT_DEGRADED` registry events through
`AdapterCallbackAppendBoundary`; no new event name was added.

The local CLI command is intentionally async and bounded:

```text
codex exec
  --ignore-user-config
  --skip-git-repo-check
  --sandbox read-only
  --ephemeral
  --json
  --output-schema <schema>
  [--model <injected-configured-model>]
  [-c model_reasoning_effort=<injected-configured-effort>]
```

The adapter uses `asyncio.create_subprocess_exec` and `communicate()` with a
timeout. It does not use `subprocess.run` in the live Workbench path. The model
is never hardcoded as a claim about the local environment; `--model` is sent
only when `VOICE_AGENT_CODEX_MODEL` is supplied. Stdout is parsed as JSONL and
reduced to allow-listed event/status/usage metadata plus one validated
structured candidate. The adapter writes the strict JSON schema to a short-lived
temporary file because `--output-schema` expects a file path; the file is
deleted after the CLI process exits. Codex CLI 0.141's final JSON response is
extracted only from the allow-listed `item.completed.item.text` field. Raw
stdout, stderr, prompt text, auth files, cookies, tokens, local paths, and
chain-of-thought are not stored or returned.

## Session/API integration

The Vite plugin starts one Python process per dev server, not one process per
HTTP request. Python owns the session dictionary and serializes each session
with an `asyncio.Lock`. The public endpoints are documented in
`frontend/README.md` and include session creation, snapshot, message, reset,
confirmation, and SSE stream operations.

The compatibility `/api/workbench/codex-proposal` endpoint now creates or reuses
a Python session and returns the dynamic public snapshot alongside the proposal.
The old static scenario payload remains accepted only as a UI/test fallback; it
is not used as the runtime fact source when the Python snapshot is present.

## Validation record

The closed-loop path has deterministic tests for:

- fake provider with no credential or network;
- provider CLI unavailable/degraded fallback;
- `SLOWTASK_CREATED` -> `PLANNING` -> progressive demo tool start;
- material UserPatch from plan 1 to plan 2;
- late old-plan ToolResult and stale evidence chain;
- cancellation confirmation and current-focus clearing;
- context pack hash, synthetic prompt preview, capability matrix, timing and
  replay digest projections;
- public snapshot/SSE safety flags and no provider rerun during replay.

The canonical local commands are:

```bash
./scripts/test -q
cd frontend && npm test -- --run && npm run build
```

On the 2026-07-13 implementation checkpoint, Python reported `1324 passed` and
the frontend reported `4 passed`; the production TypeScript/Vite build passed.
