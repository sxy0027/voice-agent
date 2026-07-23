# Local uv Environment

This document records the local reproducible environment used for slow-system
Workbench / Codex proposal bridge development.

## Scope

The environment is for local development only. It installs:

- project package in editable mode
- pytest for the repository test entrypoint
- OpenAI Python SDK for future adapter-internal Codex-backed proposal work

The Python Slow-system Workbench defaults to
`provider_mode="codex_cli_local"` with local execution enabled. The
deterministic fake provider remains available as an explicit test/demo
fallback for reproducible runs.

## Create The Environment

From the repository root:

```bash
uv venv .venv --python python3
uv pip install -e '.[dev,codex]'
```

The `.venv/` directory is local-only and ignored by Git.

## Reproduce With Pinned Dependencies

For a pinned dependency set, use:

```bash
uv venv .venv --python python3
uv pip sync requirements/uv-dev-codex.txt
uv pip install -e .
```

The pinned file was generated with:

```bash
uv pip compile pyproject.toml --extra dev --extra codex -o requirements/uv-dev-codex.txt
```

Regenerate it after changing the `dev` or `codex` optional dependencies.

## Test Entry Point

Use the repository test wrapper:

```bash
./scripts/test -q
```

The wrapper discovers `.venv/bin/python` when it has pytest installed. To be
explicit:

```bash
VOICE_AGENT_PYTHON=.venv/bin/python ./scripts/test -q
```

Run only the Codex proposal bridge tests:

```bash
VOICE_AGENT_PYTHON=.venv/bin/python ./scripts/test tests/runtime/test_slow_system_workbench_codex.py -q
```

## Call The Current Proposal Bridge

This calls the backend proposal bridge using the deterministic fake provider.
It does not require an API key and does not call an external model.

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from voice_agent.runtime.slow_system_workbench_codex import request_codex_proposal

snapshot = {
    "task": {
        "evidence": [
            {"evidence_id": "evidence://demo/asr/request"}
        ]
    }
}

proposal = request_codex_proposal(
    snapshot=snapshot,
    intent="demo request",
    proposal_type="evidence_review",
    source_evidence_refs=("evidence://demo/asr/request",),
)

print(proposal["proposal_id"])
print(proposal["status"])
print(proposal["proposal_type"])
print(proposal["safety"])
PY
```

Expected status:

```text
validated
```

## Codex Provider Boundary

There are three separate provider paths. Do not mix their credentials or
architecture boundaries.

### Path 1: deterministic fake provider

Use this for reproducible backend tests and demos that should not depend on a
local Codex CLI login. It requires no API key, does not call a model, and keeps
the demo deterministic. In the React Workbench this path is available through
the `Python fake provider (manual fallback)` dropdown option.

### Path 2: local Codex Pro account via Codex CLI

Use this only for local developer demos after explicit mentor approval. This is
the current React Workbench default configuration. It is implemented as
`provider_mode="codex_cli_local"` with `allow_local_codex_cli=True`, and uses
the local Codex authentication session created by:

```bash
codex login
```

The Codex Pro / ChatGPT login can run local Codex CLI workflows, but it is not a
Platform API key and should not be treated as a backend service credential.
Never copy `~/.codex/auth.json` into this repository or expose it to a frontend.

The provider lives behind the backend proposal bridge and runs in constrained
local mode:

```bash
codex exec --sandbox read-only --ephemeral --color never
```

The provider must parse only the final proposal JSON and must pass
`validate_workbench_codex_proposal()` before the frontend can display it.

Minimal local call:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from voice_agent.runtime.slow_system_workbench_codex import request_codex_proposal

snapshot = {
    "task": {
        "evidence": [
            {"evidence_id": "evidence://demo/asr/request"}
        ]
    }
}

proposal = request_codex_proposal(
    snapshot=snapshot,
    intent="demo request",
    proposal_type="evidence_review",
    source_evidence_refs=("evidence://demo/asr/request",),
    provider_mode="codex_cli_local",
    allow_local_codex_cli=True,
)

print(proposal["status"])
print(proposal["summary"])
PY
```

### Path 3: OpenAI Platform API key via OpenAI SDK

Use this only if a real API key is approved for the project. The OpenAI SDK must
be used only inside a backend adapter/provider implementation. Frontend code,
SlowTask, Router, Tool Executor, reducers, and replay must not call the provider
directly.

Runtime credentials must stay local:

```bash
export OPENAI_API_KEY="..."
```

Do not write API keys, bearer tokens, provider request bodies, provider response
bodies, prompt dumps, local paths, raw audio, or raw traces into committed files,
Event Journal payloads, snapshots, fixtures, or proposal JSON.

The real provider must still return proposal JSON and pass
`validate_workbench_codex_proposal()` before the frontend can display it.
