# Local uv Environment

This document records the local reproducible environment used for slow-system
Workbench / Codex proposal bridge development.

## Scope

The environment is for local development only. It installs:

- project package in editable mode
- pytest for the repository test entrypoint
- OpenAI Python SDK for future adapter-internal Codex-backed proposal work

The current committed Codex proposal bridge does not call a real external model
by default. It uses a deterministic fake provider unless a future approved
adapter-internal provider is implemented.

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

## Future Real Codex Provider Boundary

If a real Codex-backed provider is approved, the OpenAI SDK must be used only
inside a backend adapter/provider implementation. Frontend code, SlowTask,
Router, Tool Executor, reducers, and replay must not call the provider directly.

Runtime credentials must stay local:

```bash
export OPENAI_API_KEY="..."
```

Do not write API keys, bearer tokens, provider request bodies, provider response
bodies, prompt dumps, local paths, raw audio, or raw traces into committed files,
Event Journal payloads, snapshots, fixtures, or proposal JSON.

The real provider must still return proposal JSON and pass
`validate_workbench_codex_proposal()` before the frontend can display it.
