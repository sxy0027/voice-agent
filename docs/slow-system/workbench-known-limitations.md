# Slow-system Workbench known limitations and provider setup

日期：2026-07-13

## Known limitations

- The session store is process-local `InMemoryEventJournal`. Restarting the Vite
  dev server loses sessions; there is no production persistence or auth.
- The Vite plugin is the HTTP/SSE edge for the local dev Workbench. A separate
  production API deployment is not part of this slice.
- The demo tool backend is deterministic and synthetic. It does not book,
  pay, delete external data, communicate externally, control real devices, or
  call weather/web providers.
- Only one active non-terminal SlowTask is supported, matching ADR-006. There
  is no multi-task scheduling, pause/resume, or background job queue.
- Tool completion is intentionally controllable by the session orchestrator so
  the demo can show a late old-plan result. It is not a production distributed
  tool worker or cancellation proof against a remote provider.
- The UI exposes static scenario buttons as input presets. Their labels are not
  runtime facts; once a Python snapshot arrives it replaces the static scenario
  projection. The browser uses session/SSE endpoints; the compatibility
  proposal endpoint remains only for non-EventSource test surfaces.
- The fake Codex candidate is synthetic. It validates adapter and ownership
  boundaries, not model quality, planning quality, latency quality, or
  provider availability.
- Replay reconstructs recorded reducer state and public digests. It never
  reruns Codex, tools, network, clock, random, or a missing data-plane ref.
- The UI streams provider progress events and high-level tool/status summaries,
  not token-by-token model text or hidden chain-of-thought. The structured
  proposal remains a final adapter output after schema validation.

## Provider setup

### Default local Codex CLI path

No environment variable is required. The Python Workbench session manager defaults to:

```text
provider_mode=codex_cli_local
allow_local_codex_cli=true
```

The local CLI must already be installed and authenticated by the human
developer. It is invoked only through the async adapter. If the CLI is missing,
times out, or returns invalid structured output, the adapter records degraded
metadata and falls back to a deterministic synthetic candidate.

### Explicit fake path for tests

For no-credential, deterministic tests and demos, override the default:

```bash
export VOICE_AGENT_WORKBENCH_CODEX_MODE=fake
export VOICE_AGENT_ALLOW_LOCAL_CODEX_CLI=0
export VOICE_AGENT_CODEX_BIN=codex
# Optional, injected only if configured:
export VOICE_AGENT_CODEX_MODEL=<configured-model-alias>
export VOICE_AGENT_CODEX_REASONING_EFFORT=<configured-effort>
export VOICE_AGENT_CODEX_TIMEOUT_SECONDS=30
```

The project does not install dependencies, fetch a model, inspect auth files,
or read a provider token into the journal. The async adapter uses
read-only/ephemeral execution and bounded JSONL/schema parsing.

### What is recorded

Safe metadata only:

- adapter id/type, configured deployment and output mode;
- request/task/plan binding refs;
- bounded provider event kind/status/usage/latency;
- schema validation/degraded reason category;
- deterministic public context/timing/replay projection.

Not recorded or returned:

- auth files, API keys, tokens, cookies, authorization headers;
- raw CLI stdout/stderr, provider request/response bodies, prompt dumps,
  chain-of-thought, raw audio, raw trace, local file paths;
- unredacted real-user inputs in committed fixtures.

## Test summary

Use the repository’s canonical commands; do not install dependencies from a
slice implementation thread:

```bash
./scripts/test -q
cd frontend
npm test -- --run
npm run build
```

The 2026-07-13 checkpoint passed `1324` Python tests, `4` frontend tests, and
the TypeScript/Vite production build. The dynamic Workbench test module covers
fake session creation, plan 1 -> plan 2 stale evidence, cancellation
confirmation, reset, public safety/replay/context fields, and API manager
dispatch.

## Scope guard for follow-up work

Any change that adds a canonical event, lifecycle state, RouterDecision,
multi-active task, pause/resume, real external side effect, production privacy
policy, or a new owner boundary must stop and update the relevant ADR before
implementation. MVP-3 may replace adapter implementations, but must not use
this Workbench slice to silently add architecture capability.
