# Codex Proposal Provider Record

## Purpose

This record tracks the slow-system Workbench Codex proposal provider boundary.
It is an implementation record, not an accepted ADR.

The current feature adds a backend-local proposal provider option:

- `fake`: deterministic proposal provider for reproducible demos and tests.
- `codex_cli_unavailable`: explicit unavailable local Codex CLI path.
- `codex_cli_local`: local-only Codex CLI provider, disabled unless explicitly
  requested with `allow_local_codex_cli=True`.

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

The provider runs:

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
