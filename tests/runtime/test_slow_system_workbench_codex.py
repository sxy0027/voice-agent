from __future__ import annotations

import json

import pytest

from voice_agent.runtime.slow_system_workbench_codex import (
    CodexProposalBridge,
    CodexProposalError,
    CodexProposalRequest,
    FakeCodexProposalProvider,
    UnavailableCodexProposalProvider,
    mark_codex_proposal_status,
    request_codex_proposal,
    validate_codex_proposal,
)


def test_fake_provider_returns_validated_proposal_without_fact_ownership() -> None:
    proposal = request_codex_proposal(
        snapshot=_snapshot(),
        intent="user changed the visit time",
        proposal_type="plan_update",
        source_evidence_refs=("evidence://demo/user-patch/change-time",),
    )

    rendered = json.dumps(proposal, sort_keys=True)
    assert proposal["proposal_id"] == "proposal_demo_plan_update"
    assert proposal["proposal_type"] == "plan_update"
    assert proposal["status"] == "validated"
    assert proposal["source_evidence_refs"] == ["evidence://demo/user-patch/change-time"]
    assert proposal["safety"]["codex_is_fact_owner"] is False
    assert proposal["safety"]["advances_plan_version"] is False
    assert proposal["safety"]["authorizes_tool"] is False
    assert proposal["safety"]["mutates_task_snapshot"] is False
    assert "PLAN_VERSION_ADVANCED" not in rendered
    assert "TOOL_EXECUTION_AUTHORIZED" not in rendered
    assert "/Users/" not in rendered
    assert "Bearer " not in rendered


def test_bridge_returns_rejected_when_codex_provider_unavailable() -> None:
    bridge = CodexProposalBridge(UnavailableCodexProposalProvider())

    proposal = bridge.request_proposal(
        CodexProposalRequest(
            snapshot=_snapshot(),
            intent="request a proposal",
            proposal_type="evidence_review",
            source_evidence_refs=("evidence://demo/asr/request",),
        )
    )

    assert proposal["status"] == "rejected"
    assert proposal["proposal_type"] == "evidence_review"
    assert "not configured" in proposal["risk_notes"][0]
    assert all(value is False for value in proposal["safety"].values())


def test_bridge_fail_closes_invalid_provider_output() -> None:
    class UnsafeProvider:
        def propose(self, request: CodexProposalRequest) -> dict[str, object]:
            proposal = _valid_draft()
            proposal["event_name"] = "PLAN_VERSION_ADVANCED"
            return proposal

    bridge = CodexProposalBridge(UnsafeProvider())

    proposal = bridge.request_proposal(
        CodexProposalRequest(
            snapshot=_snapshot(),
            intent="request an unsafe proposal",
            proposal_type="plan_update",
            source_evidence_refs=("evidence://demo/user-patch/change-time",),
        )
    )

    assert proposal["status"] == "rejected"
    assert "forbidden proposal field" in proposal["risk_notes"][0]
    assert all(value is False for value in proposal["safety"].values())


@pytest.mark.parametrize(
    "flag",
    [
        "codex_is_fact_owner",
        "advances_plan_version",
        "authorizes_tool",
        "contains_secret",
        "mutates_task_snapshot",
        "emits_canonical_event",
        "executes_external_tool",
        "contains_raw_provider_body",
    ],
)
def test_validation_rejects_unsafe_safety_flags(flag: str) -> None:
    proposal = _valid_draft()
    proposal["safety"][flag] = True

    with pytest.raises(CodexProposalError, match="unsafe safety flags"):
        validate_codex_proposal(
            proposal,
            snapshot=_snapshot(),
            requested_source_evidence_refs=("evidence://demo/user-patch/change-time",),
        )


@pytest.mark.parametrize(
    "field",
    [
        "event_name",
        "task_snapshot_patch",
        "tool_execution",
        "ui_patch",
        "raw_provider_body",
        "headers",
        "authorization",
        "prompt_dump",
    ],
)
def test_validation_rejects_forbidden_mutation_or_raw_provider_fields(field: str) -> None:
    proposal = _valid_draft()
    proposal[field] = "PLAN_VERSION_ADVANCED"

    with pytest.raises(CodexProposalError, match="forbidden proposal field"):
        validate_codex_proposal(
            proposal,
            snapshot=_snapshot(),
            requested_source_evidence_refs=("evidence://demo/user-patch/change-time",),
        )


def test_validation_requires_source_evidence_refs() -> None:
    proposal = _valid_draft()
    proposal["source_evidence_refs"] = []

    with pytest.raises(CodexProposalError, match="must not be empty"):
        validate_codex_proposal(
            proposal,
            snapshot=_snapshot(),
            requested_source_evidence_refs=("evidence://demo/user-patch/change-time",),
        )


def test_validation_rejects_source_ref_not_requested() -> None:
    proposal = _valid_draft()
    proposal["source_evidence_refs"] = ["evidence://demo/asr/request"]

    with pytest.raises(CodexProposalError, match="must come from the request"):
        validate_codex_proposal(
            proposal,
            snapshot=_snapshot(),
            requested_source_evidence_refs=("evidence://demo/user-patch/change-time",),
        )


def test_validation_rejects_source_ref_not_in_snapshot() -> None:
    proposal = _valid_draft()
    proposal["source_evidence_refs"] = ["evidence://demo/missing"]

    with pytest.raises(CodexProposalError, match="must exist in snapshot evidence"):
        validate_codex_proposal(
            proposal,
            snapshot=_snapshot(),
            requested_source_evidence_refs=("evidence://demo/missing",),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", "/Users/shixinyue/.env should never be exposed"),
        ("risk_notes", ["Bearer unsafe-token-value"]),
        ("suggested_next_steps", ["read traces/debug.jsonl"]),
    ],
)
def test_validation_rejects_unsafe_local_paths_and_secret_like_text(
    field: str,
    value: object,
) -> None:
    proposal = _valid_draft()
    proposal[field] = value

    with pytest.raises(CodexProposalError, match="unsafe"):
        validate_codex_proposal(
            proposal,
            snapshot=_snapshot(),
            requested_source_evidence_refs=("evidence://demo/user-patch/change-time",),
        )


def test_mark_proposal_status_records_fact_owner_without_mutating_snapshot() -> None:
    proposal = request_codex_proposal(
        snapshot=_snapshot(),
        intent="user changed the visit time",
        proposal_type="plan_update",
        source_evidence_refs=("evidence://demo/user-patch/change-time",),
    )

    accepted = mark_codex_proposal_status(proposal, status="accepted")

    assert proposal["status"] == "validated"
    assert "fact_owner" not in proposal
    assert accepted["status"] == "accepted"
    assert accepted["fact_owner"] == "slowtask_event_journal"
    assert accepted["mutates_task_snapshot"] is False


def test_documented_aliases_are_callable() -> None:
    bridge = CodexProposalBridge(FakeCodexProposalProvider())

    proposal = bridge.request_proposal(
        CodexProposalRequest(
            snapshot=_snapshot(),
            intent="request a documented alias proposal",
            proposal_type="clarification",
            source_evidence_refs=("evidence://demo/asr/request",),
        )
    )

    assert proposal["status"] == "validated"
    assert proposal["proposal_type"] == "clarification"
    assert proposal["missing_fields"] == ["budget_or_time_preference"]


def _snapshot() -> dict[str, object]:
    return {
        "snapshot_id": "snapshot_demo_001",
        "task": {
            "task_id": "task_demo",
            "current_plan_version": 1,
            "evidence": [
                {
                    "evidence_id": "evidence://demo/user-patch/change-time",
                    "summary": "user changed time",
                },
                {
                    "evidence_id": "evidence://demo/asr/request",
                    "summary": "user asked to plan a visit",
                },
            ],
        },
    }


def _valid_draft() -> dict[str, object]:
    return {
        "proposal_id": "proposal_demo_001",
        "proposal_type": "plan_update",
        "status": "draft",
        "summary": "Codex suggests updating the plan after user constraints changed.",
        "suggested_next_steps": [
            "Keep existing location constraints.",
            "Ask SlowTask to decide whether the plan should restart.",
        ],
        "missing_fields": [],
        "requires_confirmation": True,
        "risk_notes": [
            "This proposal is not a SemanticCommitment until SlowTask accepts it.",
        ],
        "source_evidence_refs": ["evidence://demo/user-patch/change-time"],
        "safety": {
            "codex_is_fact_owner": False,
            "advances_plan_version": False,
            "authorizes_tool": False,
            "contains_secret": False,
        },
    }
