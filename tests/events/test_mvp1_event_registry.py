from __future__ import annotations

import pytest

from voice_agent.events.envelope import EventValidationError, validate_event_envelope
from voice_agent.events.registry import MVP0_EVENT_NAMES, MVP1_EVENT_DEFINITIONS, MVP1_EVENT_NAMES


MVP1_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "TASK_FOCUS_STATE_UPDATED": (
        "foreground_mode",
        "side_conversation_allowed",
        "default_patch_policy",
        "ambiguous_input_policy",
        "last_focus_decision",
        "last_focus_confidence",
        "router_decision_event_id",
    ),
    "SLOWTASK_CREATED": ("task_id", "plan_version", "task_event_seq", "initial_goal_ref"),
    "SLOWTASK_STATE_CHANGED": ("task_id", "plan_version", "task_event_seq", "from_state", "to_state", "reason"),
    "USER_PATCH_RECEIVED": (
        "patch_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "observed_plan_version",
        "evidence_ref",
    ),
    "USER_PATCH_INTERPRETED": (
        "patch_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "observed_plan_version",
        "interpreted_against_plan_version",
        "interpretation_type",
        "materially_changes_task",
    ),
    "PLAN_VERSION_ADVANCED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "from_plan_version",
        "to_plan_version",
        "planning_reason",
    ),
    "TASK_REPLANNED": ("task_id", "plan_version", "task_event_seq", "planning_reason"),
    "TASK_REQUIREMENT_MODEL_ACCEPTED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "model_id",
        "model_version",
        "task_kind",
        "model_ref",
        "source_proposal_ref",
        "accepted_context_hash",
        "model_status",
        "model_confidence",
        "bootstrap_reason",
        "needs_remodeling",
        "model_payload",
    ),
    "TASK_REQUIREMENT_MODEL_INVALIDATED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "model_ref",
        "invalidation_reason",
    ),
    "EVIDENCE_REVIEWED": ("task_id", "plan_version", "task_event_seq", "evidence_refs", "review_result"),
    "AMBIGUITY_DETECTED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "ambiguous_fields",
        "source_evidence_refs",
    ),
    "AMBIGUITY_RESOLVED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "resolved_fields",
        "resolution_reason",
        "source_evidence_refs",
    ),
    "CLARIFICATION_REQUESTED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "missing_or_ambiguous_fields",
        "clarification_prompt_ref",
    ),
    "ARGUMENTS_RESOLVED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "resolved_arguments_ref",
        "provenance_ref",
    ),
    "ARGUMENT_RESOLUTION_PROVENANCE": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "field_provenance_refs",
    ),
    "INSUFFICIENT_EVIDENCE_FOR_ACTION": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "blocking_fields",
        "source_evidence_refs",
    ),
    "PLANNING_STARTED": ("task_id", "plan_version", "task_event_seq", "planning_reason"),
    "PLANNING_RESTARTED": ("task_id", "plan_version", "task_event_seq", "restart_reason"),
    "WAITING_FOR_SLOT": ("task_id", "plan_version", "task_event_seq", "missing_fields"),
    "WAITING_FOR_USER_CONFIRMATION": ("task_id", "plan_version", "task_event_seq", "confirmation_id"),
    "FINALIZING": ("task_id", "plan_version", "task_event_seq", "source_events"),
    "SLOWTASK_DEGRADED": ("task_id", "plan_version", "task_event_seq", "degraded_reason"),
    "SLOWTASK_FAILED": ("task_id", "plan_version", "task_event_seq", "failure_reason"),
    "CONFIRMATION_REQUIRED": (
        "confirmation_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "confirmation_scope",
        "required_for_event_id",
        "prompt_ref",
    ),
    "USER_CONFIRMATION_RECEIVED": (
        "confirmation_id",
        "patch_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "confirmation_signal",
    ),
    "CONFIRMATION_ACCEPTED": (
        "confirmation_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "accepted_scope",
        "authorization_ref",
    ),
    "CONFIRMATION_REJECTED": (
        "confirmation_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "rejection_reason",
    ),
    "SLOWTASK_CANCEL_REQUESTED": ("task_id", "plan_version", "task_event_seq", "cancel_reason"),
    "SLOWTASK_CANCELLED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "cancel_reason",
        "inflight_tool_policy",
    ),
    "TOOL_CALL_STARTED": (
        "tool_call_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "tool_name",
        "idempotency_key",
    ),
    "TOOL_RESULT_RECEIVED": (
        "tool_call_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "result_status",
        "result_ref",
    ),
    "TOOL_RESULT_MARKED_STALE": (
        "tool_call_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "result_plan_version",
        "current_plan_version",
        "stale_reason",
    ),
    "STALE_EVIDENCE_RECORDED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "stale_evidence_ref",
        "source_tool_result_event_id",
    ),
    "STALE_EVIDENCE_ADOPTED": (
        "task_id",
        "plan_version",
        "task_event_seq",
        "stale_evidence_ref",
        "source_tool_result_event_id",
        "adopted_from_plan_version",
        "adoption_reason",
        "adopted_scope",
        "adopted_by_event_id",
    ),
    "SEMANTIC_COMMITMENT_EMITTED": (
        "commitment_id",
        "task_id",
        "plan_version",
        "task_event_seq",
        "source_events",
    ),
}

MVP1_LITERAL_FIELDS: dict[str, dict[str, object]] = {
    "STALE_EVIDENCE_ADOPTED": {"adoption_mode": "adopt_or_rebase"},
}

NON_CANONICAL_EVENT_NAMES = (
    "SEMANTIC_COMMITMENT_CREATED",
    "STALE_TOOL_RESULT_RECORDED",
    "SPOKEN_PLAN_CREATED",
)
MVP2_ONLY_EVENT_NAMES = (
    "TOOL_MANIFEST_LOADED",
    "TOOL_ARGUMENTS_PARTIAL",
    "TOOL_ARGUMENTS_READY",
    "TOOL_PREVIEW_AVAILABLE",
    "TOOL_EXECUTION_AUTHORIZED",
    "TOOL_EXECUTION_STARTED",
    "WAITING_FOR_TOOL",
    "TOOL_PROGRESS_UPDATED",
    "TOOL_UI_STATE_PATCHED",
    "TOOL_EXECUTION_FAILED",
    "TOOL_CALL_RETRYING",
    "TOOL_EXECUTION_CANCEL_REQUESTED",
    "TOOL_EXECUTION_CANCELLED",
    "TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS",
    "SPOKEN_PLAN_EMITTED",
    "COMMITMENT_COVERAGE_CHECK_PASSED",
    "COMMITMENT_COVERAGE_CHECK_FAILED",
    "PROGRESS_TRUTHFULNESS_CHECK_PASSED",
    "PROGRESS_TRUTHFULNESS_CHECK_FAILED",
)


def mvp1_event(event_name: str, **overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "event_name": event_name,
        "event_id": f"evt_synthetic_{event_name.lower()}",
        "event_seq": 2,
        "event_schema_version": "1.0",
        "session_id": "sess_mvp1_synthetic_001",
        "conversation_id": "conv_mvp1_synthetic_001",
        "source_module": "mvp1_registry_test",
        "created_monotonic_ms": 20,
        "created_wall_clock_ms": 1700000000020,
        "caused_by_event_id": "evt_synthetic_prior",
        "trace_redaction_level": "metadata_only",
        "active_task_id": "task_synthetic_001",
        "adapter_id": "adapter_synthetic",
        "adapter_type": "mock",
        "adopted_by_event_id": "evt_synthetic_adoption_decision",
        "adopted_from_plan_version": 1,
        "adopted_scope": ["synthetic_field"],
        "adoption_mode": "adopt_or_rebase",
        "adoption_reason": "synthetic_rebase",
        "ambiguous_fields": ["synthetic_slot"],
        "ambiguous_input_policy": "CLARIFY",
        "authorization_ref": "authorization://synthetic/mvp1/current-plan",
        "blocking_fields": ["synthetic_slot"],
        "cancel_reason": "synthetic_cancel",
        "accepted_scope": "TASK_CANCEL",
        "clarification_prompt_ref": "prompt://synthetic/mvp1/clarification",
        "commitment_id": "commitment_synthetic_001",
        "confirmation_id": "confirmation_synthetic_001",
        "confirmation_scope": "TASK_CANCEL",
        "confirmation_signal": "accepted",
        "current_plan_version": 2,
        "default_patch_policy": "ACTIVE_TASK_PATCH_ONLY",
        "degraded_reason": "synthetic_degraded",
        "evidence_ref": "evidence://synthetic/mvp1/user-patch",
        "evidence_refs": ["evidence://synthetic/mvp1/reviewed"],
        "failure_reason": "synthetic_unrecoverable_failure",
        "field_provenance_refs": ["provenance://synthetic/mvp1/field"],
        "foreground_mode": "FAST_RESPONSE",
        "from_plan_version": 1,
        "from_state": "PLANNING",
        "idempotency_key": "idem_synthetic_tool_call_001",
        "inflight_tool_policy": "no_inflight_tools",
        "initial_goal_ref": "goal://synthetic/mvp1/initial",
        "interpretation_type": "constraint_update",
        "interpreted_against_plan_version": 1,
        "last_focus_confidence": 0.91,
        "last_focus_decision": "ACTIVE_TASK_PATCH",
        "materially_changes_task": True,
        "model_id": "model_synthetic",
        "model_version": 1,
        "task_kind": "synthetic_task",
        "model_ref": "requirement-model://synthetic/task/v1",
        "source_proposal_ref": "proposal://synthetic/task-modeler",
        "accepted_context_hash": "sha256:synthetic-context",
        "model_status": "ACCEPTED_SPECIFIC",
        "model_confidence": "high",
        "bootstrap_reason": "not_applicable",
        "needs_remodeling": False,
        "model_payload": {"model_id": "model_synthetic", "task_kind": "synthetic_task"},
        "invalidation_reason": "synthetic_goal_rewrite",
        "missing_fields": ["synthetic_slot"],
        "missing_or_ambiguous_fields": ["synthetic_slot"],
        "observed_plan_version": 1,
        "partial_arguments_ref": "args://synthetic/mvp1/partial",
        "patch_id": "patch_synthetic_001",
        "plan_version": 1,
        "planning_reason": "synthetic_material_patch",
        "prompt_ref": "prompt://synthetic/mvp1/confirmation",
        "provenance_ref": "provenance://synthetic/mvp1/arguments",
        "reason": "synthetic_transition",
        "rejection_reason": "synthetic_rejected",
        "required_for_event_id": "evt_synthetic_required_action",
        "resolution_reason": "synthetic_resolution",
        "resolved_arguments_ref": "args://synthetic/mvp1/resolved",
        "resolved_fields": ["synthetic_slot"],
        "restart_reason": "synthetic_plan_advanced",
        "result_plan_version": 1,
        "result_ref": "tool-result://synthetic/mvp1/minimal",
        "result_status": "succeeded",
        "review_result": "sufficient",
        "router_decision_event_id": "evt_synthetic_router_decision",
        "side_conversation_allowed": True,
        "source_evidence_refs": ["evidence://synthetic/mvp1/source"],
        "source_events": ["evt_synthetic_source"],
        "source_tool_result_event_id": "evt_synthetic_tool_result",
        "stale_evidence_ref": "stale-evidence://synthetic/mvp1/result",
        "stale_reason": "old_plan_result",
        "superseded_plan_version": 1,
        "task_event_seq": 1,
        "task_id": "task_synthetic_001",
        "to_plan_version": 2,
        "to_state": "COMPLETED",
        "tool_call_id": "tool_call_synthetic_001",
        "tool_name": "synthetic_fixture_tool",
    }
    event.update(overrides)
    return event


def test_registry_exposes_mvp1_names_separately_from_mvp0() -> None:
    assert set(MVP1_REQUIRED_FIELDS) == MVP1_EVENT_NAMES
    assert MVP1_EVENT_NAMES.isdisjoint(MVP0_EVENT_NAMES)
    assert set(MVP1_EVENT_DEFINITIONS) == MVP1_EVENT_NAMES


@pytest.mark.parametrize("event_name,required_fields", sorted(MVP1_REQUIRED_FIELDS.items()))
def test_mvp1_canonical_events_validate_with_required_fields(
    event_name: str,
    required_fields: tuple[str, ...],
) -> None:
    validated = validate_event_envelope(mvp1_event(event_name))

    assert validated["event_name"] == event_name
    for required_field in required_fields:
        assert required_field in validated


@pytest.mark.parametrize("event_name,required_fields", sorted(MVP1_REQUIRED_FIELDS.items()))
def test_mvp1_registry_required_fields_match_event_registry_spec(
    event_name: str,
    required_fields: tuple[str, ...],
) -> None:
    assert MVP1_EVENT_DEFINITIONS[event_name].required_fields == required_fields


@pytest.mark.parametrize(
    "event_name,missing_field",
    [
        ("USER_PATCH_INTERPRETED", "task_id"),
        ("USER_PATCH_INTERPRETED", "plan_version"),
        ("USER_PATCH_INTERPRETED", "task_event_seq"),
        ("USER_PATCH_INTERPRETED", "observed_plan_version"),
        ("USER_PATCH_INTERPRETED", "interpreted_against_plan_version"),
        ("PLAN_VERSION_ADVANCED", "task_id"),
        ("PLAN_VERSION_ADVANCED", "plan_version"),
        ("PLAN_VERSION_ADVANCED", "task_event_seq"),
        ("PLAN_VERSION_ADVANCED", "from_plan_version"),
        ("PLAN_VERSION_ADVANCED", "to_plan_version"),
        ("PLAN_VERSION_ADVANCED", "planning_reason"),
        ("TOOL_RESULT_MARKED_STALE", "task_id"),
        ("TOOL_RESULT_MARKED_STALE", "plan_version"),
        ("TOOL_RESULT_MARKED_STALE", "task_event_seq"),
        ("TOOL_RESULT_MARKED_STALE", "result_plan_version"),
        ("TOOL_RESULT_MARKED_STALE", "current_plan_version"),
        ("STALE_EVIDENCE_RECORDED", "task_id"),
        ("STALE_EVIDENCE_RECORDED", "plan_version"),
        ("STALE_EVIDENCE_RECORDED", "task_event_seq"),
        ("STALE_EVIDENCE_RECORDED", "source_tool_result_event_id"),
    ],
)
def test_adr_004_and_adr_016_binding_refinements_are_required(
    event_name: str,
    missing_field: str,
) -> None:
    event = mvp1_event(event_name)
    event.pop(missing_field)

    with pytest.raises(EventValidationError, match=missing_field):
        validate_event_envelope(event)


@pytest.mark.parametrize("event_name,literal_fields", sorted(MVP1_LITERAL_FIELDS.items()))
def test_mvp1_literal_fields_are_enforced(event_name: str, literal_fields: dict[str, object]) -> None:
    for literal_field, expected in literal_fields.items():
        event = mvp1_event(event_name, **{literal_field: "wrong_literal"})

        with pytest.raises(EventValidationError, match=f"{literal_field}={expected}"):
            validate_event_envelope(event)


@pytest.mark.parametrize("event_name", NON_CANONICAL_EVENT_NAMES)
def test_non_canonical_relationship_labels_are_rejected_as_journal_event_names(event_name: str) -> None:
    with pytest.raises(EventValidationError, match="Unknown event_name"):
        validate_event_envelope(mvp1_event(event_name))


@pytest.mark.parametrize("event_name", MVP2_ONLY_EVENT_NAMES)
def test_mvp2_only_events_are_not_required_or_accepted_by_mvp1_registry(event_name: str) -> None:
    assert event_name not in MVP1_EVENT_NAMES
