from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from voice_agent.events.journal import InMemoryEventJournal


MOCK_SLOWTASK_SOURCE_MODULE = "slowtask_runtime"
MOCK_TOOL_EVENT_SOURCE_MODULE = "mock_tool_event_emitter"
INITIAL_PLAN_VERSION = 1
MATERIAL_PATCH_CANDIDATES = {
    "slot_update_candidate": ("slot_update", "mock_slot_update_candidate"),
    "constraint_update_candidate": ("constraint_update", "mock_constraint_update_candidate"),
    "goal_rewrite_candidate": ("goal_rewrite", "mock_goal_rewrite_candidate"),
}
NON_MATERIAL_PATCH_CANDIDATES = {
    "feedback_candidate": ("feedback", "mock_feedback_candidate"),
    "irrelevant_candidate": ("irrelevant", "mock_irrelevant_candidate"),
}
CONTROL_PATCH_INTERPRETATIONS = {
    "cancel_candidate": ("cancel", "mock_cancel_candidate"),
    "switch_task_candidate": ("switch_task", "mock_switch_task_candidate"),
    "confirmation_candidate": ("confirmation", "mock_confirmation_candidate"),
}
CONFIRMATION_SCOPES_BY_INTERPRETATION = {
    "cancel": "TASK_CANCEL",
    "switch_task": "SWITCH_TASK",
}
CANCEL_REASONS_BY_CONFIRMATION_SCOPE = {
    "TASK_CANCEL": "task_cancel_accepted",
    "SWITCH_TASK": "switch_task_accepted",
}
REJECTION_REASONS_BY_CONFIRMATION_SCOPE = {
    "TASK_CANCEL": "user_rejected_task_cancel",
    "SWITCH_TASK": "user_rejected_switch_task",
}


@dataclass(frozen=True)
class MockSlowTaskRunResult:
    task_id: str
    plan_version: int
    produced_events: tuple[dict[str, Any], ...]


class MockSlowTaskRuntime:
    """Minimal MVP-1 Slice 4 mock lifecycle.

    The runtime only records deterministic happy-path lifecycle events. It does
    not call models, tools, Composer, or external systems.
    """

    def __init__(self, journal: InMemoryEventJournal) -> None:
        self._journal = journal

    def run_spawn_planning_completed(
        self,
        *,
        router_decision_event: Mapping[str, Any],
        task_id: str,
        initial_goal_ref: str,
        commitment_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        source_evidence_refs: Sequence[str] = (),
        evidence_refs: Sequence[str] = (),
        resolved_arguments_ref: str | None = None,
        provenance_ref: str | None = None,
        field_provenance_refs: Sequence[str] = (),
        commitment_ref: str | None = None,
    ) -> MockSlowTaskRunResult:
        created_result = self.create_from_router_spawn(
            router_decision_event=router_decision_event,
            task_id=task_id,
            initial_goal_ref=initial_goal_ref,
            event_id_prefix=event_id_prefix,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            source_evidence_refs=source_evidence_refs,
        )
        planning_result = self.run_planning_completed(
            task_id=task_id,
            plan_version=created_result.plan_version,
            caused_by_event_id=str(created_result.produced_events[0]["event_id"]),
            event_id_prefix=event_id_prefix,
            created_monotonic_ms=created_monotonic_ms + 2,
            created_wall_clock_ms=created_wall_clock_ms + 2,
            start_task_event_seq=3,
            evidence_refs=evidence_refs or source_evidence_refs,
            resolved_arguments_ref=resolved_arguments_ref,
            provenance_ref=provenance_ref,
            field_provenance_refs=field_provenance_refs,
            commitment_id=commitment_id,
            commitment_ref=commitment_ref,
        )

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=created_result.plan_version,
            produced_events=(*created_result.produced_events, *planning_result.produced_events),
        )

    def create_from_router_spawn(
        self,
        *,
        router_decision_event: Mapping[str, Any],
        task_id: str,
        initial_goal_ref: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        source_evidence_refs: Sequence[str] = (),
    ) -> MockSlowTaskRunResult:
        _validate_spawn_router_decision(router_decision_event)
        if not task_id:
            raise ValueError("task_id is required")
        if not event_id_prefix:
            raise ValueError("event_id_prefix is required")

        source_refs = tuple(str(ref) for ref in source_evidence_refs)
        plan_version = INITIAL_PLAN_VERSION
        produced_events: list[dict[str, Any]] = []

        created = self._append_slowtask_event(
            event_name="SLOWTASK_CREATED",
            event_id=f"{event_id_prefix}_slowtask_created",
            caused_by_event_id=str(router_decision_event["event_id"]),
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=1,
            initial_goal_ref=initial_goal_ref,
            source_evidence_refs=list(source_refs),
        )
        produced_events.append(created)

        created_state = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_created",
            caused_by_event_id=str(created["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=2,
            from_state="CREATED",
            to_state="CREATED",
            reason="created_snapshot",
        )
        produced_events.append(created_state)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=tuple(produced_events),
        )

    def accept_task_requirement_model(
        self,
        *,
        task_id: str,
        plan_version: int,
        task_event_seq: int,
        caused_by_event_id: str,
        event_id: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        model_payload: Mapping[str, Any],
        source_proposal_ref: str,
        accepted_context_hash: str,
    ) -> Mapping[str, Any]:
        return self._append_slowtask_event(
            event_name="TASK_REQUIREMENT_MODEL_ACCEPTED",
            event_id=event_id,
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=task_event_seq,
            model_id=str(model_payload["model_id"]),
            model_version=int(model_payload["model_version"]),
            task_kind=str(model_payload["task_kind"]),
            model_ref=f"requirement-model://{task_id}/{model_payload['model_id']}/v{model_payload['model_version']}",
            source_proposal_ref=source_proposal_ref,
            accepted_context_hash=accepted_context_hash,
            model_status=str(model_payload["model_status"]),
            model_confidence=str(model_payload["model_confidence"]),
            bootstrap_reason=model_payload.get("bootstrap_reason") or "not_applicable",
            needs_remodeling=bool(model_payload["needs_remodeling"]),
            model_payload=dict(model_payload),
        )

    def invalidate_task_requirement_model(
        self,
        *,
        task_id: str,
        plan_version: int,
        task_event_seq: int,
        caused_by_event_id: str,
        event_id: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        model_ref: str,
        invalidation_reason: str,
    ) -> Mapping[str, Any]:
        return self._append_slowtask_event(
            event_name="TASK_REQUIREMENT_MODEL_INVALIDATED",
            event_id=event_id,
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=task_event_seq,
            model_ref=model_ref,
            invalidation_reason=invalidation_reason,
        )

    def accept_requirement_states(
        self,
        *,
        task_id: str,
        plan_version: int,
        task_event_seq: int,
        caused_by_event_id: str,
        event_id: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        evidence_refs: Sequence[str],
        accepted_requirement_states: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        return self._append_slowtask_event(
            event_name="EVIDENCE_REVIEWED",
            event_id=event_id,
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=task_event_seq,
            evidence_refs=list(evidence_refs),
            review_result="accepted_requirement_assessment",
            accepted_requirement_states=[dict(item) for item in accepted_requirement_states],
        )

    def run_planning_started(
        self,
        *,
        task_id: str,
        plan_version: int,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        from_state: str = "CREATED",
        planning_reason: str = "initial_goal_accepted",
    ) -> MockSlowTaskRunResult:
        """Enter PLANNING without completing the task.

        The original MVP-1 helper only emitted a planning-to-commitment happy
        path.  Workbench sessions need the same canonical events while keeping
        the task open for a tool phase and later UserPatch.
        """

        planning_started = self._append_slowtask_event(
            event_name="PLANNING_STARTED",
            event_id=f"{event_id_prefix}_planning_started",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            planning_reason=planning_reason,
        )
        state_changed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_planning",
            caused_by_event_id=str(planning_started["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 1,
            from_state=from_state,
            to_state="PLANNING",
            reason=planning_reason,
        )
        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=(planning_started, state_changed),
        )

    def emit_waiting_for_tool(
        self,
        *,
        task_id: str,
        plan_version: int,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        tool_call_id: str,
        from_state: str = "PLANNING",
    ) -> MockSlowTaskRunResult:
        waiting = self._append_slowtask_event(
            event_name="WAITING_FOR_TOOL",
            event_id=f"{event_id_prefix}_waiting_for_tool",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            tool_call_id=tool_call_id,
        )
        state_changed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_executing",
            caused_by_event_id=str(waiting["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 1,
            from_state=from_state,
            to_state="EXECUTING",
            reason="waiting_for_demo_tool_result",
        )
        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=(waiting, state_changed),
        )

    def mark_tool_result_stale(
        self,
        *,
        task_id: str,
        current_plan_version: int,
        result_plan_version: int,
        tool_call_id: str,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        stale_evidence_ref: str,
        stale_reason: str,
    ) -> MockSlowTaskRunResult:
        if result_plan_version >= current_plan_version:
            raise ValueError("mark_tool_result_stale requires an older result_plan_version")
        marked = self._append_slowtask_event(
            event_name="TOOL_RESULT_MARKED_STALE",
            event_id=f"{event_id_prefix}_tool_result_marked_stale",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=current_plan_version,
            task_event_seq=start_task_event_seq,
            tool_call_id=tool_call_id,
            result_plan_version=result_plan_version,
            current_plan_version=current_plan_version,
            stale_reason=stale_reason,
        )
        recorded = self._append_slowtask_event(
            event_name="STALE_EVIDENCE_RECORDED",
            event_id=f"{event_id_prefix}_stale_evidence_recorded",
            caused_by_event_id=str(marked["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=current_plan_version,
            task_event_seq=start_task_event_seq + 1,
            stale_evidence_ref=stale_evidence_ref,
            source_tool_result_event_id=caused_by_event_id,
        )
        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=current_plan_version,
            produced_events=(marked, recorded),
        )

    def finalize_current_task(
        self,
        *,
        task_id: str,
        plan_version: int,
        current_lifecycle_state: str,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        commitment_id: str,
        commitment_ref: str | None = None,
    ) -> MockSlowTaskRunResult:
        commitment_fields: dict[str, Any] = {}
        if commitment_ref is not None:
            commitment_fields["commitment_ref"] = commitment_ref
        finalizing = self._append_slowtask_event(
            event_name="FINALIZING",
            event_id=f"{event_id_prefix}_finalizing",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            source_events=[caused_by_event_id],
        )
        commitment = self._append_slowtask_event(
            event_name="SEMANTIC_COMMITMENT_EMITTED",
            event_id=f"{event_id_prefix}_semantic_commitment",
            caused_by_event_id=str(finalizing["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 1,
            commitment_id=commitment_id,
            source_events=[str(finalizing["event_id"])],
            **commitment_fields,
        )
        completed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_completed",
            caused_by_event_id=str(commitment["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 2,
            created_wall_clock_ms=created_wall_clock_ms + 2,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 2,
            from_state=current_lifecycle_state,
            to_state="COMPLETED",
            reason="workbench_current_plan_finalized",
        )
        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=(finalizing, commitment, completed),
        )

    def mark_current_plan_ready_for_revision(
        self,
        *,
        task_id: str,
        plan_version: int,
        current_lifecycle_state: str,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        task_event_seq: int,
    ) -> MockSlowTaskRunResult:
        """Return from a finished read-only lookup to a mutable planning state.

        This intentionally emits no SemanticCommitment and no terminal state.
        It uses the existing canonical SLOWTASK_STATE_CHANGED event so a
        following material UserPatch is processed against the same task and
        advances its plan_version rather than creating a replacement task.
        """

        state_changed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_planning",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=task_event_seq,
            from_state=current_lifecycle_state,
            to_state="PLANNING",
            reason="read_only_evidence_ready_for_current_plan_revision",
        )
        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=(state_changed,),
        )

    def run_planning_completed(
        self,
        *,
        task_id: str,
        plan_version: int,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        commitment_id: str,
        evidence_refs: Sequence[str] = (),
        resolved_arguments_ref: str | None = None,
        provenance_ref: str | None = None,
        field_provenance_refs: Sequence[str] = (),
        commitment_ref: str | None = None,
    ) -> MockSlowTaskRunResult:
        produced_events: list[dict[str, Any]] = []
        reviewed_refs = tuple(str(ref) for ref in evidence_refs)

        planning_started = self._append_slowtask_event(
            event_name="PLANNING_STARTED",
            event_id=f"{event_id_prefix}_planning_started",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            planning_reason="initial_goal_accepted",
        )
        produced_events.append(planning_started)

        planning_state = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_planning",
            caused_by_event_id=str(planning_started["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 1,
            from_state="CREATED",
            to_state="PLANNING",
            reason="initial_planning_started",
        )
        produced_events.append(planning_state)

        evidence_reviewed = self._append_slowtask_event(
            event_name="EVIDENCE_REVIEWED",
            event_id=f"{event_id_prefix}_evidence_reviewed",
            caused_by_event_id=str(planning_state["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 2,
            created_wall_clock_ms=created_wall_clock_ms + 2,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 2,
            evidence_refs=list(reviewed_refs),
            review_result="sufficient",
        )
        produced_events.append(evidence_reviewed)

        previous_event_id = str(evidence_reviewed["event_id"])
        next_task_event_seq = start_task_event_seq + 3
        next_time_offset = 3
        if resolved_arguments_ref is not None and provenance_ref is not None:
            arguments_resolved = self._append_slowtask_event(
                event_name="ARGUMENTS_RESOLVED",
                event_id=f"{event_id_prefix}_arguments_resolved",
                caused_by_event_id=previous_event_id,
                created_monotonic_ms=created_monotonic_ms + next_time_offset,
                created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                task_id=task_id,
                plan_version=plan_version,
                task_event_seq=next_task_event_seq,
                resolved_arguments_ref=resolved_arguments_ref,
                provenance_ref=provenance_ref,
            )
            produced_events.append(arguments_resolved)
            previous_event_id = str(arguments_resolved["event_id"])
            next_task_event_seq += 1
            next_time_offset += 1

            provenance = self._append_slowtask_event(
                event_name="ARGUMENT_RESOLUTION_PROVENANCE",
                event_id=f"{event_id_prefix}_argument_provenance",
                caused_by_event_id=previous_event_id,
                created_monotonic_ms=created_monotonic_ms + next_time_offset,
                created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                task_id=task_id,
                plan_version=plan_version,
                task_event_seq=next_task_event_seq,
                field_provenance_refs=list(str(ref) for ref in field_provenance_refs),
            )
            produced_events.append(provenance)
            previous_event_id = str(provenance["event_id"])
            next_task_event_seq += 1
            next_time_offset += 1

        finalizing = self._append_slowtask_event(
            event_name="FINALIZING",
            event_id=f"{event_id_prefix}_finalizing",
            caused_by_event_id=previous_event_id,
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            source_events=[previous_event_id],
        )
        produced_events.append(finalizing)
        next_task_event_seq += 1
        next_time_offset += 1

        commitment_fields: dict[str, Any] = {}
        if commitment_ref is not None:
            commitment_fields["commitment_ref"] = commitment_ref
        commitment = self._append_slowtask_event(
            event_name="SEMANTIC_COMMITMENT_EMITTED",
            event_id=f"{event_id_prefix}_semantic_commitment",
            caused_by_event_id=str(finalizing["event_id"]),
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            commitment_id=commitment_id,
            source_events=[str(finalizing["event_id"])],
            **commitment_fields,
        )
        produced_events.append(commitment)
        next_task_event_seq += 1
        next_time_offset += 1

        completed_state = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_completed",
            caused_by_event_id=str(commitment["event_id"]),
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            from_state="PLANNING",
            to_state="COMPLETED",
            reason="synthetic_commitment_complete",
        )
        produced_events.append(completed_state)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=tuple(produced_events),
        )

    def interpret_user_patch(
        self,
        *,
        user_patch_event: Mapping[str, Any],
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        current_lifecycle_state: str = "PLANNING",
        supersedes_event_id: str | None = None,
        confirmation_id: str | None = None,
        prompt_ref: str | None = None,
        pending_confirmation_id: str | None = None,
        pending_confirmation_scope: str | None = None,
        confirmation_signal: str | None = None,
        authorization_ref: str | None = None,
        return_to_state: str = "PLANNING",
        current_resolved_slots: Mapping[str, str] | None = None,
        incoming_slot_values: Mapping[str, str] | None = None,
        next_task_event_seq: int | None = None,
    ) -> MockSlowTaskRunResult:
        _validate_user_patch_received_event(user_patch_event)
        if not event_id_prefix:
            raise ValueError("event_id_prefix is required")

        task_id = str(user_patch_event["task_id"])
        patch_id = str(user_patch_event["patch_id"])
        observed_plan_version = _int_field(user_patch_event, "observed_plan_version")
        current_plan_version = _int_field(user_patch_event, "plan_version")
        received_task_event_seq = _int_field(user_patch_event, "task_event_seq")
        next_task_event_seq = (
            received_task_event_seq + 1
            if next_task_event_seq is None
            else next_task_event_seq
        )
        if next_task_event_seq <= received_task_event_seq:
            raise ValueError("next_task_event_seq must follow USER_PATCH_RECEIVED")
        interpretation_type, materially_changes_task, interpretation_reason = (
            _mock_interpretation_from_user_patch(
                user_patch_event,
                current_resolved_slots=current_resolved_slots,
                incoming_slot_values=incoming_slot_values,
            )
        )
        if interpretation_type == "confirmation":
            _validate_confirmation_response_inputs(
                pending_confirmation_id=pending_confirmation_id,
                pending_confirmation_scope=pending_confirmation_scope,
                confirmation_signal=confirmation_signal,
            )
        if (
            current_lifecycle_state == "WAITING_FOR_USER_CONFIRMATION"
            and interpretation_type in CONFIRMATION_SCOPES_BY_INTERPRETATION
        ):
            _validate_confirmation_response_inputs(
                pending_confirmation_id=pending_confirmation_id,
                pending_confirmation_scope=pending_confirmation_scope,
                confirmation_signal="rejected",
            )
        source_evidence_refs = _source_evidence_refs_from_user_patch(user_patch_event)
        produced_events: list[dict[str, Any]] = []

        interpreted = self._append_slowtask_event(
            event_name="USER_PATCH_INTERPRETED",
            event_id=f"{event_id_prefix}_user_patch_interpreted",
            caused_by_event_id=str(user_patch_event["event_id"]),
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            patch_id=patch_id,
            task_id=task_id,
            plan_version=current_plan_version,
            task_event_seq=next_task_event_seq,
            observed_plan_version=observed_plan_version,
            interpreted_against_plan_version=observed_plan_version,
            interpretation_type=interpretation_type,
            materially_changes_task=materially_changes_task,
            interpretation_reason=interpretation_reason,
            source_evidence_refs=list(source_evidence_refs),
        )
        produced_events.append(interpreted)

        if interpretation_type in CONFIRMATION_SCOPES_BY_INTERPRETATION:
            if current_lifecycle_state == "WAITING_FOR_USER_CONFIRMATION":
                return self._apply_pending_confirmation_signal(
                    task_id=task_id,
                    plan_version=current_plan_version,
                    user_patch_event=user_patch_event,
                    interpreted_event=interpreted,
                    event_id_prefix=event_id_prefix,
                    created_monotonic_ms=created_monotonic_ms + 1,
                    created_wall_clock_ms=created_wall_clock_ms + 1,
                    start_task_event_seq=next_task_event_seq + 1,
                    current_lifecycle_state=current_lifecycle_state,
                    pending_confirmation_id=pending_confirmation_id,
                    pending_confirmation_scope=pending_confirmation_scope,
                    confirmation_signal="rejected",
                    authorization_ref=None,
                    return_to_state=return_to_state,
                    produced_events=produced_events,
                )
            return self._require_control_confirmation(
                task_id=task_id,
                plan_version=current_plan_version,
                interpreted_event=interpreted,
                event_id_prefix=event_id_prefix,
                created_monotonic_ms=created_monotonic_ms + 1,
                created_wall_clock_ms=created_wall_clock_ms + 1,
                start_task_event_seq=next_task_event_seq + 1,
                current_lifecycle_state=current_lifecycle_state,
                confirmation_id=confirmation_id,
                confirmation_scope=CONFIRMATION_SCOPES_BY_INTERPRETATION[interpretation_type],
                prompt_ref=prompt_ref,
                produced_events=produced_events,
            )

        if interpretation_type == "confirmation":
            return self._apply_pending_confirmation_signal(
                task_id=task_id,
                plan_version=current_plan_version,
                user_patch_event=user_patch_event,
                interpreted_event=interpreted,
                event_id_prefix=event_id_prefix,
                created_monotonic_ms=created_monotonic_ms + 1,
                created_wall_clock_ms=created_wall_clock_ms + 1,
                start_task_event_seq=next_task_event_seq + 1,
                current_lifecycle_state=current_lifecycle_state,
                pending_confirmation_id=pending_confirmation_id,
                pending_confirmation_scope=pending_confirmation_scope,
                confirmation_signal=confirmation_signal,
                authorization_ref=authorization_ref,
                return_to_state=return_to_state,
                produced_events=produced_events,
            )

        if not materially_changes_task:
            return MockSlowTaskRunResult(
                task_id=task_id,
                plan_version=current_plan_version,
                produced_events=tuple(produced_events),
            )

        planning_reason = f"material_user_patch:{interpretation_type}"
        advance_task_event_seq = next_task_event_seq + 1
        time_offset = 1
        advance_fields: dict[str, Any] = {}
        if supersedes_event_id is not None:
            advance_fields["supersedes_event_id"] = supersedes_event_id
        advanced = self._append_slowtask_event(
            event_name="PLAN_VERSION_ADVANCED",
            event_id=f"{event_id_prefix}_plan_version_advanced",
            caused_by_event_id=str(interpreted["event_id"]),
            created_monotonic_ms=created_monotonic_ms + time_offset,
            created_wall_clock_ms=created_wall_clock_ms + time_offset,
            task_id=task_id,
            plan_version=current_plan_version + 1,
            task_event_seq=advance_task_event_seq,
            from_plan_version=current_plan_version,
            to_plan_version=current_plan_version + 1,
            planning_reason=planning_reason,
            caused_by_user_patch_event_id=str(user_patch_event["event_id"]),
            **advance_fields,
        )
        produced_events.append(advanced)

        restarted = self._append_slowtask_event(
            event_name="PLANNING_RESTARTED",
            event_id=f"{event_id_prefix}_planning_restarted",
            caused_by_event_id=str(advanced["event_id"]),
            created_monotonic_ms=created_monotonic_ms + time_offset + 1,
            created_wall_clock_ms=created_wall_clock_ms + time_offset + 1,
            task_id=task_id,
            plan_version=current_plan_version + 1,
            task_event_seq=advance_task_event_seq + 1,
            restart_reason=planning_reason,
        )
        produced_events.append(restarted)

        replanned = self._append_slowtask_event(
            event_name="TASK_REPLANNED",
            event_id=f"{event_id_prefix}_task_replanned",
            caused_by_event_id=str(advanced["event_id"]),
            created_monotonic_ms=created_monotonic_ms + time_offset + 2,
            created_wall_clock_ms=created_wall_clock_ms + time_offset + 2,
            task_id=task_id,
            plan_version=current_plan_version + 1,
            task_event_seq=advance_task_event_seq + 2,
            planning_reason=planning_reason,
            superseded_plan_version=current_plan_version,
        )
        produced_events.append(replanned)

        planning_state = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_planning",
            caused_by_event_id=str(replanned["event_id"]),
            created_monotonic_ms=created_monotonic_ms + time_offset + 3,
            created_wall_clock_ms=created_wall_clock_ms + time_offset + 3,
            task_id=task_id,
            plan_version=current_plan_version + 1,
            task_event_seq=advance_task_event_seq + 3,
            from_state=current_lifecycle_state,
            to_state="PLANNING",
            reason="material_user_patch_replanning",
        )
        produced_events.append(planning_state)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=current_plan_version + 1,
            produced_events=tuple(produced_events),
        )

    def _require_control_confirmation(
        self,
        *,
        task_id: str,
        plan_version: int,
        interpreted_event: Mapping[str, Any],
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        current_lifecycle_state: str,
        confirmation_id: str | None,
        confirmation_scope: str,
        prompt_ref: str | None,
        produced_events: list[dict[str, Any]],
    ) -> MockSlowTaskRunResult:
        resolved_confirmation_id = confirmation_id or f"confirmation_{event_id_prefix}"
        resolved_prompt_ref = prompt_ref or f"prompt://synthetic/mvp1/control-confirmation/{event_id_prefix}"
        required = self._append_slowtask_event(
            event_name="CONFIRMATION_REQUIRED",
            event_id=f"{event_id_prefix}_confirmation_required",
            caused_by_event_id=str(interpreted_event["event_id"]),
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            confirmation_id=resolved_confirmation_id,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            confirmation_scope=confirmation_scope,
            required_for_event_id=str(interpreted_event["event_id"]),
            prompt_ref=resolved_prompt_ref,
        )
        produced_events.append(required)

        waiting = self._append_slowtask_event(
            event_name="WAITING_FOR_USER_CONFIRMATION",
            event_id=f"{event_id_prefix}_waiting_for_user_confirmation",
            caused_by_event_id=str(required["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 1,
            confirmation_id=resolved_confirmation_id,
        )
        produced_events.append(waiting)

        state_changed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_waiting_for_user_confirmation",
            caused_by_event_id=str(waiting["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 2,
            created_wall_clock_ms=created_wall_clock_ms + 2,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 2,
            from_state=current_lifecycle_state,
            to_state="WAITING_FOR_USER_CONFIRMATION",
            reason="control_confirmation_required",
        )
        produced_events.append(state_changed)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=tuple(produced_events),
        )

    def _apply_pending_confirmation_signal(
        self,
        *,
        task_id: str,
        plan_version: int,
        user_patch_event: Mapping[str, Any],
        interpreted_event: Mapping[str, Any],
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        current_lifecycle_state: str,
        pending_confirmation_id: str | None,
        pending_confirmation_scope: str | None,
        confirmation_signal: str | None,
        authorization_ref: str | None,
        return_to_state: str,
        produced_events: list[dict[str, Any]],
    ) -> MockSlowTaskRunResult:
        _validate_confirmation_response_inputs(
            pending_confirmation_id=pending_confirmation_id,
            pending_confirmation_scope=pending_confirmation_scope,
            confirmation_signal=confirmation_signal,
        )

        user_confirmation = self._append_slowtask_event(
            event_name="USER_CONFIRMATION_RECEIVED",
            event_id=f"{event_id_prefix}_user_confirmation_received",
            caused_by_event_id=str(interpreted_event["event_id"]),
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            confirmation_id=str(pending_confirmation_id),
            patch_id=str(user_patch_event["patch_id"]),
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            confirmation_signal=confirmation_signal,
        )
        produced_events.append(user_confirmation)

        if confirmation_signal == "accepted":
            return self._accept_control_confirmation(
                task_id=task_id,
                plan_version=plan_version,
                user_patch_event=user_patch_event,
                user_confirmation_event=user_confirmation,
                event_id_prefix=event_id_prefix,
                created_monotonic_ms=created_monotonic_ms + 1,
                created_wall_clock_ms=created_wall_clock_ms + 1,
                start_task_event_seq=start_task_event_seq + 1,
                current_lifecycle_state=current_lifecycle_state,
                pending_confirmation_id=str(pending_confirmation_id),
                pending_confirmation_scope=str(pending_confirmation_scope),
                authorization_ref=authorization_ref,
                produced_events=produced_events,
            )

        return self._reject_control_confirmation(
            task_id=task_id,
            plan_version=plan_version,
            user_confirmation_event=user_confirmation,
            event_id_prefix=event_id_prefix,
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            start_task_event_seq=start_task_event_seq + 1,
            current_lifecycle_state=current_lifecycle_state,
            return_to_state=return_to_state,
            pending_confirmation_id=str(pending_confirmation_id),
            pending_confirmation_scope=str(pending_confirmation_scope),
            produced_events=produced_events,
        )

    def _accept_control_confirmation(
        self,
        *,
        task_id: str,
        plan_version: int,
        user_patch_event: Mapping[str, Any],
        user_confirmation_event: Mapping[str, Any],
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        current_lifecycle_state: str,
        pending_confirmation_id: str,
        pending_confirmation_scope: str,
        authorization_ref: str | None,
        produced_events: list[dict[str, Any]],
    ) -> MockSlowTaskRunResult:
        resolved_authorization_ref = (
            authorization_ref
            or f"authorization://synthetic/mvp1/{pending_confirmation_scope.lower()}/accepted"
        )
        accepted = self._append_slowtask_event(
            event_name="CONFIRMATION_ACCEPTED",
            event_id=f"{event_id_prefix}_confirmation_accepted",
            caused_by_event_id=str(user_confirmation_event["event_id"]),
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            confirmation_id=pending_confirmation_id,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            accepted_scope=pending_confirmation_scope,
            authorization_ref=resolved_authorization_ref,
        )
        produced_events.append(accepted)

        cancel_reason = CANCEL_REASONS_BY_CONFIRMATION_SCOPE[pending_confirmation_scope]
        cancel_requested = self._append_slowtask_event(
            event_name="SLOWTASK_CANCEL_REQUESTED",
            event_id=f"{event_id_prefix}_cancel_requested",
            caused_by_event_id=str(accepted["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 1,
            cancel_reason=cancel_reason,
            source_user_patch_event_id=str(user_patch_event["event_id"]),
        )
        produced_events.append(cancel_requested)

        cancelled = self._append_slowtask_event(
            event_name="SLOWTASK_CANCELLED",
            event_id=f"{event_id_prefix}_cancelled",
            caused_by_event_id=str(cancel_requested["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 2,
            created_wall_clock_ms=created_wall_clock_ms + 2,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 2,
            cancel_reason=cancel_reason,
            inflight_tool_policy="no_inflight_tools",
        )
        produced_events.append(cancelled)

        state_changed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_cancelled",
            caused_by_event_id=str(cancelled["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 3,
            created_wall_clock_ms=created_wall_clock_ms + 3,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 3,
            from_state=current_lifecycle_state,
            to_state="CANCELLED",
            reason=cancel_reason,
        )
        produced_events.append(state_changed)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=tuple(produced_events),
        )

    def _reject_control_confirmation(
        self,
        *,
        task_id: str,
        plan_version: int,
        user_confirmation_event: Mapping[str, Any],
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        current_lifecycle_state: str,
        return_to_state: str,
        pending_confirmation_id: str,
        pending_confirmation_scope: str,
        produced_events: list[dict[str, Any]],
    ) -> MockSlowTaskRunResult:
        rejection_reason = REJECTION_REASONS_BY_CONFIRMATION_SCOPE[pending_confirmation_scope]
        rejected = self._append_slowtask_event(
            event_name="CONFIRMATION_REJECTED",
            event_id=f"{event_id_prefix}_confirmation_rejected",
            caused_by_event_id=str(user_confirmation_event["event_id"]),
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            confirmation_id=pending_confirmation_id,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            rejection_reason=rejection_reason,
        )
        produced_events.append(rejected)

        state_changed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_{return_to_state.lower()}",
            caused_by_event_id=str(rejected["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 1,
            from_state=current_lifecycle_state,
            to_state=return_to_state,
            reason="confirmation_rejected",
        )
        produced_events.append(state_changed)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=tuple(produced_events),
        )

    def record_mock_tool_call(
        self,
        *,
        task_id: str,
        plan_version: int,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        task_event_seq: int,
        tool_call_id: str,
        tool_name: str,
        idempotency_key: str,
    ) -> MockSlowTaskRunResult:
        if not task_id:
            raise ValueError("task_id is required")
        if not event_id_prefix:
            raise ValueError("event_id_prefix is required")
        if not tool_call_id:
            raise ValueError("tool_call_id is required")
        if not tool_name:
            raise ValueError("tool_name is required")
        if not idempotency_key:
            raise ValueError("idempotency_key is required")

        tool_call = self._append_mock_tool_event(
            event_name="TOOL_CALL_STARTED",
            event_id=f"{event_id_prefix}_tool_call_started",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=task_event_seq,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
        )

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=(tool_call,),
        )

    def record_old_plan_tool_result(
        self,
        *,
        task_id: str,
        current_plan_version: int,
        result_plan_version: int,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        tool_call_id: str,
        result_status: str,
        result_ref: str,
        stale_evidence_ref: str,
        stale_reason: str,
    ) -> MockSlowTaskRunResult:
        if not task_id:
            raise ValueError("task_id is required")
        if not event_id_prefix:
            raise ValueError("event_id_prefix is required")
        if not tool_call_id:
            raise ValueError("tool_call_id is required")
        if not result_status:
            raise ValueError("result_status is required")
        if not result_ref:
            raise ValueError("result_ref is required")
        if not stale_evidence_ref:
            raise ValueError("stale_evidence_ref is required")
        if not stale_reason:
            raise ValueError("stale_reason is required")
        if result_plan_version >= current_plan_version:
            raise ValueError("record_old_plan_tool_result requires result_plan_version older than current_plan_version")

        produced_events: list[dict[str, Any]] = []
        late_result = self._append_mock_tool_event(
            event_name="TOOL_RESULT_RECEIVED",
            event_id=f"{event_id_prefix}_tool_result_received",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=result_plan_version,
            task_event_seq=start_task_event_seq,
            tool_call_id=tool_call_id,
            result_status=result_status,
            result_ref=result_ref,
        )
        produced_events.append(late_result)

        marked_stale = self._append_slowtask_event(
            event_name="TOOL_RESULT_MARKED_STALE",
            event_id=f"{event_id_prefix}_tool_result_marked_stale",
            caused_by_event_id=str(late_result["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=current_plan_version,
            task_event_seq=start_task_event_seq + 1,
            tool_call_id=tool_call_id,
            result_plan_version=result_plan_version,
            current_plan_version=current_plan_version,
            stale_reason=stale_reason,
        )
        produced_events.append(marked_stale)

        recorded = self._append_slowtask_event(
            event_name="STALE_EVIDENCE_RECORDED",
            event_id=f"{event_id_prefix}_stale_evidence_recorded",
            caused_by_event_id=str(marked_stale["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 2,
            created_wall_clock_ms=created_wall_clock_ms + 2,
            task_id=task_id,
            plan_version=current_plan_version,
            task_event_seq=start_task_event_seq + 2,
            stale_evidence_ref=stale_evidence_ref,
            source_tool_result_event_id=str(late_result["event_id"]),
        )
        produced_events.append(recorded)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=current_plan_version,
            produced_events=tuple(produced_events),
        )

    def adopt_stale_evidence_for_commitment(
        self,
        *,
        task_id: str,
        plan_version: int,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        stale_evidence_ref: str,
        source_tool_result_event_id: str,
        adopted_from_plan_version: int,
        adoption_reason: str,
        adopted_scope: Sequence[str],
        adopted_by_event_id: str,
        resolved_arguments_ref: str,
        provenance_ref: str,
        field_provenance_refs: Sequence[str],
        commitment_id: str,
        commitment_ref: str | None = None,
        current_lifecycle_state: str = "PLANNING",
    ) -> MockSlowTaskRunResult:
        if not task_id:
            raise ValueError("task_id is required")
        if not event_id_prefix:
            raise ValueError("event_id_prefix is required")
        if not stale_evidence_ref:
            raise ValueError("stale_evidence_ref is required")
        if not source_tool_result_event_id:
            raise ValueError("source_tool_result_event_id is required")
        if not adoption_reason:
            raise ValueError("adoption_reason is required")
        bounded_scope = _string_tuple(adopted_scope)
        if not bounded_scope:
            raise ValueError("adopted_scope is required")
        if not adopted_by_event_id:
            raise ValueError("adopted_by_event_id is required")
        if not resolved_arguments_ref:
            raise ValueError("resolved_arguments_ref is required")
        if not provenance_ref:
            raise ValueError("provenance_ref is required")
        if not commitment_id:
            raise ValueError("commitment_id is required")

        produced_events: list[dict[str, Any]] = []
        adopted = self._append_slowtask_event(
            event_name="STALE_EVIDENCE_ADOPTED",
            event_id=f"{event_id_prefix}_stale_evidence_adopted",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            stale_evidence_ref=stale_evidence_ref,
            source_tool_result_event_id=source_tool_result_event_id,
            adopted_from_plan_version=adopted_from_plan_version,
            adoption_mode="adopt_or_rebase",
            adoption_reason=adoption_reason,
            adopted_scope=list(bounded_scope),
            adopted_by_event_id=adopted_by_event_id,
        )
        produced_events.append(adopted)

        evidence_reviewed = self._append_slowtask_event(
            event_name="EVIDENCE_REVIEWED",
            event_id=f"{event_id_prefix}_evidence_reviewed",
            caused_by_event_id=str(adopted["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 1,
            evidence_refs=[stale_evidence_ref],
            review_result="adopted_stale_evidence_sufficient",
        )
        produced_events.append(evidence_reviewed)

        arguments = self._append_slowtask_event(
            event_name="ARGUMENTS_RESOLVED",
            event_id=f"{event_id_prefix}_arguments_resolved",
            caused_by_event_id=str(evidence_reviewed["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 2,
            created_wall_clock_ms=created_wall_clock_ms + 2,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 2,
            resolved_arguments_ref=resolved_arguments_ref,
            provenance_ref=provenance_ref,
        )
        produced_events.append(arguments)

        provenance = self._append_slowtask_event(
            event_name="ARGUMENT_RESOLUTION_PROVENANCE",
            event_id=f"{event_id_prefix}_argument_provenance",
            caused_by_event_id=str(arguments["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 3,
            created_wall_clock_ms=created_wall_clock_ms + 3,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 3,
            field_provenance_refs=list(_string_tuple(field_provenance_refs)),
        )
        produced_events.append(provenance)

        finalizing = self._append_slowtask_event(
            event_name="FINALIZING",
            event_id=f"{event_id_prefix}_finalizing",
            caused_by_event_id=str(provenance["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 4,
            created_wall_clock_ms=created_wall_clock_ms + 4,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 4,
            source_events=[str(adopted["event_id"]), str(provenance["event_id"])],
        )
        produced_events.append(finalizing)

        commitment_fields: dict[str, Any] = {}
        if commitment_ref is not None:
            commitment_fields["commitment_ref"] = commitment_ref
        commitment = self._append_slowtask_event(
            event_name="SEMANTIC_COMMITMENT_EMITTED",
            event_id=f"{event_id_prefix}_semantic_commitment",
            caused_by_event_id=str(finalizing["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 5,
            created_wall_clock_ms=created_wall_clock_ms + 5,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 5,
            commitment_id=commitment_id,
            source_events=[str(adopted["event_id"]), str(finalizing["event_id"])],
            **commitment_fields,
        )
        produced_events.append(commitment)

        completed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_completed",
            caused_by_event_id=str(commitment["event_id"]),
            created_monotonic_ms=created_monotonic_ms + 6,
            created_wall_clock_ms=created_wall_clock_ms + 6,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq + 6,
            from_state=current_lifecycle_state,
            to_state="COMPLETED",
            reason="synthetic_adopted_stale_evidence_commitment_complete",
        )
        produced_events.append(completed)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=tuple(produced_events),
        )

    def review_evidence(
        self,
        *,
        task_id: str,
        plan_version: int,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        evidence_refs: Sequence[str],
        required_fields: Sequence[str],
        resolved_fields: Sequence[str] = (),
        ambiguous_fields: Sequence[str] = (),
        context_resolved_fields: Sequence[str] = (),
        missing_fields: Sequence[str] = (),
        resolved_arguments_ref: str | None = None,
        provenance_ref: str | None = None,
        field_provenance_refs: Sequence[str] = (),
        clarification_prompt_ref: str | None = None,
        resolution_reason: str = "mock_context_resolution",
    ) -> MockSlowTaskRunResult:
        if not task_id:
            raise ValueError("task_id is required")
        if not event_id_prefix:
            raise ValueError("event_id_prefix is required")

        reviewed_refs = _string_tuple(evidence_refs)
        required = _string_tuple(required_fields)
        resolved = _string_tuple(resolved_fields)
        ambiguous = _string_tuple(ambiguous_fields)
        context_resolved = _string_tuple(context_resolved_fields)
        missing = _string_tuple(missing_fields)
        field_provenance = _string_tuple(field_provenance_refs)
        ambiguity_fully_resolved = bool(ambiguous) and _fields_cover(ambiguous, context_resolved)

        if missing:
            return self._review_missing_slot(
                task_id=task_id,
                plan_version=plan_version,
                caused_by_event_id=caused_by_event_id,
                event_id_prefix=event_id_prefix,
                created_monotonic_ms=created_monotonic_ms,
                created_wall_clock_ms=created_wall_clock_ms,
                start_task_event_seq=start_task_event_seq,
                evidence_refs=reviewed_refs,
                missing_fields=missing,
                ambiguous_fields=ambiguous,
                context_resolved_fields=context_resolved,
                clarification_prompt_ref=clarification_prompt_ref,
                resolution_reason=resolution_reason,
            )

        if ambiguous and not ambiguity_fully_resolved:
            _validate_clarification_prompt_ref(clarification_prompt_ref)
        if not ambiguous or ambiguity_fully_resolved:
            _validate_resolved_arguments_inputs(
                required_fields=required,
                resolved_fields=resolved,
                resolved_arguments_ref=resolved_arguments_ref,
                provenance_ref=provenance_ref,
            )

        produced_events: list[dict[str, Any]] = []
        if ambiguity_fully_resolved:
            review_result = "context_resolvable_ambiguity"
        elif ambiguous:
            review_result = "ambiguous"
        else:
            review_result = "sufficient"

        evidence_reviewed = self._append_slowtask_event(
            event_name="EVIDENCE_REVIEWED",
            event_id=f"{event_id_prefix}_evidence_reviewed",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            evidence_refs=list(reviewed_refs),
            review_result=review_result,
        )
        produced_events.append(evidence_reviewed)

        previous_event_id = str(evidence_reviewed["event_id"])
        next_task_event_seq = start_task_event_seq + 1
        next_time_offset = 1

        if ambiguous:
            ambiguity_detected = self._append_slowtask_event(
                event_name="AMBIGUITY_DETECTED",
                event_id=f"{event_id_prefix}_ambiguity_detected",
                caused_by_event_id=previous_event_id,
                created_monotonic_ms=created_monotonic_ms + next_time_offset,
                created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                task_id=task_id,
                plan_version=plan_version,
                task_event_seq=next_task_event_seq,
                ambiguous_fields=list(ambiguous),
                source_evidence_refs=list(reviewed_refs),
            )
            produced_events.append(ambiguity_detected)
            previous_event_id = str(ambiguity_detected["event_id"])
            next_task_event_seq += 1
            next_time_offset += 1

            if not ambiguity_fully_resolved:
                insufficient = self._append_slowtask_event(
                    event_name="INSUFFICIENT_EVIDENCE_FOR_ACTION",
                    event_id=f"{event_id_prefix}_insufficient_evidence",
                    caused_by_event_id=previous_event_id,
                    created_monotonic_ms=created_monotonic_ms + next_time_offset,
                    created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                    task_id=task_id,
                    plan_version=plan_version,
                    task_event_seq=next_task_event_seq,
                    blocking_fields=list(ambiguous),
                    source_evidence_refs=list(reviewed_refs),
                )
                produced_events.append(insufficient)
                previous_event_id = str(insufficient["event_id"])
                next_task_event_seq += 1
                next_time_offset += 1

                clarification = self._append_slowtask_event(
                    event_name="CLARIFICATION_REQUESTED",
                    event_id=f"{event_id_prefix}_clarification_requested",
                    caused_by_event_id=previous_event_id,
                    created_monotonic_ms=created_monotonic_ms + next_time_offset,
                    created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                    task_id=task_id,
                    plan_version=plan_version,
                    task_event_seq=next_task_event_seq,
                    missing_or_ambiguous_fields=list(ambiguous),
                    clarification_prompt_ref=clarification_prompt_ref,
                )
                produced_events.append(clarification)
                previous_event_id = str(clarification["event_id"])
                next_task_event_seq += 1
                next_time_offset += 1

                waiting = self._append_slowtask_event(
                    event_name="WAITING_FOR_SLOT",
                    event_id=f"{event_id_prefix}_waiting_for_slot",
                    caused_by_event_id=previous_event_id,
                    created_monotonic_ms=created_monotonic_ms + next_time_offset,
                    created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                    task_id=task_id,
                    plan_version=plan_version,
                    task_event_seq=next_task_event_seq,
                    missing_fields=list(ambiguous),
                )
                produced_events.append(waiting)
                previous_event_id = str(waiting["event_id"])
                next_task_event_seq += 1
                next_time_offset += 1

                state_changed = self._append_slowtask_event(
                    event_name="SLOWTASK_STATE_CHANGED",
                    event_id=f"{event_id_prefix}_state_waiting_for_slot",
                    caused_by_event_id=previous_event_id,
                    created_monotonic_ms=created_monotonic_ms + next_time_offset,
                    created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                    task_id=task_id,
                    plan_version=plan_version,
                    task_event_seq=next_task_event_seq,
                    from_state="PLANNING",
                    to_state="WAITING_FOR_SLOT",
                    reason="unresolved_ambiguity",
                )
                produced_events.append(state_changed)
                return MockSlowTaskRunResult(
                    task_id=task_id,
                    plan_version=plan_version,
                    produced_events=tuple(produced_events),
                )

            ambiguity_resolved = self._append_slowtask_event(
                event_name="AMBIGUITY_RESOLVED",
                event_id=f"{event_id_prefix}_ambiguity_resolved",
                caused_by_event_id=previous_event_id,
                created_monotonic_ms=created_monotonic_ms + next_time_offset,
                created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                task_id=task_id,
                plan_version=plan_version,
                task_event_seq=next_task_event_seq,
                resolved_fields=list(context_resolved),
                resolution_reason=resolution_reason,
                source_evidence_refs=list(reviewed_refs),
            )
            produced_events.append(ambiguity_resolved)
            previous_event_id = str(ambiguity_resolved["event_id"])
            next_task_event_seq += 1
            next_time_offset += 1

        arguments_resolved = self._append_slowtask_event(
            event_name="ARGUMENTS_RESOLVED",
            event_id=f"{event_id_prefix}_arguments_resolved",
            caused_by_event_id=previous_event_id,
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            resolved_arguments_ref=resolved_arguments_ref,
            provenance_ref=provenance_ref,
        )
        produced_events.append(arguments_resolved)
        previous_event_id = str(arguments_resolved["event_id"])
        next_task_event_seq += 1
        next_time_offset += 1

        provenance = self._append_slowtask_event(
            event_name="ARGUMENT_RESOLUTION_PROVENANCE",
            event_id=f"{event_id_prefix}_argument_provenance",
            caused_by_event_id=previous_event_id,
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            field_provenance_refs=list(field_provenance),
        )
        produced_events.append(provenance)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=tuple(produced_events),
        )

    def _review_missing_slot(
        self,
        *,
        task_id: str,
        plan_version: int,
        caused_by_event_id: str,
        event_id_prefix: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        start_task_event_seq: int,
        evidence_refs: tuple[str, ...],
        missing_fields: tuple[str, ...],
        ambiguous_fields: tuple[str, ...],
        context_resolved_fields: tuple[str, ...],
        clarification_prompt_ref: str | None,
        resolution_reason: str,
    ) -> MockSlowTaskRunResult:
        _validate_clarification_prompt_ref(clarification_prompt_ref)

        produced_events: list[dict[str, Any]] = []
        ambiguity_fully_resolved = bool(ambiguous_fields) and _fields_cover(
            ambiguous_fields,
            context_resolved_fields,
        )
        blocking_fields = missing_fields
        if ambiguous_fields and not ambiguity_fully_resolved:
            blocking_fields = _merge_field_tuples(missing_fields, ambiguous_fields)

        evidence_reviewed = self._append_slowtask_event(
            event_name="EVIDENCE_REVIEWED",
            event_id=f"{event_id_prefix}_evidence_reviewed",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=start_task_event_seq,
            evidence_refs=list(evidence_refs),
            review_result="insufficient",
        )
        produced_events.append(evidence_reviewed)

        previous_event_id = str(evidence_reviewed["event_id"])
        next_task_event_seq = start_task_event_seq + 1
        next_time_offset = 1

        if ambiguous_fields:
            ambiguity_detected = self._append_slowtask_event(
                event_name="AMBIGUITY_DETECTED",
                event_id=f"{event_id_prefix}_ambiguity_detected",
                caused_by_event_id=previous_event_id,
                created_monotonic_ms=created_monotonic_ms + next_time_offset,
                created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                task_id=task_id,
                plan_version=plan_version,
                task_event_seq=next_task_event_seq,
                ambiguous_fields=list(ambiguous_fields),
                source_evidence_refs=list(evidence_refs),
            )
            produced_events.append(ambiguity_detected)
            previous_event_id = str(ambiguity_detected["event_id"])
            next_task_event_seq += 1
            next_time_offset += 1

            if ambiguity_fully_resolved:
                ambiguity_resolved = self._append_slowtask_event(
                    event_name="AMBIGUITY_RESOLVED",
                    event_id=f"{event_id_prefix}_ambiguity_resolved",
                    caused_by_event_id=previous_event_id,
                    created_monotonic_ms=created_monotonic_ms + next_time_offset,
                    created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
                    task_id=task_id,
                    plan_version=plan_version,
                    task_event_seq=next_task_event_seq,
                    resolved_fields=list(context_resolved_fields),
                    resolution_reason=resolution_reason,
                    source_evidence_refs=list(evidence_refs),
                )
                produced_events.append(ambiguity_resolved)
                previous_event_id = str(ambiguity_resolved["event_id"])
                next_task_event_seq += 1
                next_time_offset += 1

        insufficient = self._append_slowtask_event(
            event_name="INSUFFICIENT_EVIDENCE_FOR_ACTION",
            event_id=f"{event_id_prefix}_insufficient_evidence",
            caused_by_event_id=previous_event_id,
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            blocking_fields=list(blocking_fields),
            source_evidence_refs=list(evidence_refs),
        )
        produced_events.append(insufficient)
        previous_event_id = str(insufficient["event_id"])
        next_task_event_seq += 1
        next_time_offset += 1

        clarification = self._append_slowtask_event(
            event_name="CLARIFICATION_REQUESTED",
            event_id=f"{event_id_prefix}_clarification_requested",
            caused_by_event_id=previous_event_id,
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            missing_or_ambiguous_fields=list(blocking_fields),
            clarification_prompt_ref=clarification_prompt_ref,
        )
        produced_events.append(clarification)
        previous_event_id = str(clarification["event_id"])
        next_task_event_seq += 1
        next_time_offset += 1

        waiting = self._append_slowtask_event(
            event_name="WAITING_FOR_SLOT",
            event_id=f"{event_id_prefix}_waiting_for_slot",
            caused_by_event_id=previous_event_id,
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            missing_fields=list(blocking_fields),
        )
        produced_events.append(waiting)
        previous_event_id = str(waiting["event_id"])
        next_task_event_seq += 1
        next_time_offset += 1

        state_changed = self._append_slowtask_event(
            event_name="SLOWTASK_STATE_CHANGED",
            event_id=f"{event_id_prefix}_state_waiting_for_slot",
            caused_by_event_id=previous_event_id,
            created_monotonic_ms=created_monotonic_ms + next_time_offset,
            created_wall_clock_ms=created_wall_clock_ms + next_time_offset,
            task_id=task_id,
            plan_version=plan_version,
            task_event_seq=next_task_event_seq,
            from_state="PLANNING",
            to_state="WAITING_FOR_SLOT",
            reason="missing_critical_slot",
        )
        produced_events.append(state_changed)

        return MockSlowTaskRunResult(
            task_id=task_id,
            plan_version=plan_version,
            produced_events=tuple(produced_events),
        )

    def _append_slowtask_event(
        self,
        *,
        event_name: str,
        event_id: str,
        caused_by_event_id: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        **fields: Any,
    ) -> dict[str, Any]:
        return self._journal.append(
            event_name=event_name,
            event_id=event_id,
            source_module=MOCK_SLOWTASK_SOURCE_MODULE,
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            trace_redaction_level="metadata_only",
            **fields,
        )

    def _append_mock_tool_event(
        self,
        *,
        event_name: str,
        event_id: str,
        caused_by_event_id: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
        **fields: Any,
    ) -> dict[str, Any]:
        return self._journal.append(
            event_name=event_name,
            event_id=event_id,
            source_module=MOCK_TOOL_EVENT_SOURCE_MODULE,
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
            trace_redaction_level="metadata_only",
            **fields,
        )


def _validate_spawn_router_decision(event: Mapping[str, Any]) -> None:
    if event.get("event_name") != "ROUTER_DECISION_EMITTED":
        raise ValueError("MockSlowTaskRuntime requires a ROUTER_DECISION_EMITTED event")
    if event.get("router_decision") != "SPAWN_SLOW_TASK":
        raise ValueError("MockSlowTaskRuntime requires router_decision=SPAWN_SLOW_TASK")


def _validate_user_patch_received_event(event: Mapping[str, Any]) -> None:
    if event.get("event_name") != "USER_PATCH_RECEIVED":
        raise ValueError("MockSlowTaskRuntime requires a USER_PATCH_RECEIVED event")
    for field in ("event_id", "patch_id", "task_id", "plan_version", "task_event_seq", "observed_plan_version"):
        if field not in event:
            raise ValueError(f"USER_PATCH_RECEIVED missing required field: {field}")


def _validate_confirmation_response_inputs(
    *,
    pending_confirmation_id: str | None,
    pending_confirmation_scope: str | None,
    confirmation_signal: str | None,
) -> None:
    if pending_confirmation_id in (None, ""):
        raise ValueError("confirmation patch requires pending_confirmation_id")
    if pending_confirmation_scope not in CANCEL_REASONS_BY_CONFIRMATION_SCOPE:
        raise ValueError("confirmation patch requires TASK_CANCEL or SWITCH_TASK pending_confirmation_scope")
    if confirmation_signal not in {"accepted", "rejected"}:
        raise ValueError("confirmation patch requires confirmation_signal accepted or rejected")


def _mock_interpretation_from_user_patch(
    event: Mapping[str, Any],
    *,
    current_resolved_slots: Mapping[str, str] | None = None,
    incoming_slot_values: Mapping[str, str] | None = None,
) -> tuple[str, bool, str]:
    candidate_types = _string_tuple(event.get("candidate_patch_types", ()))
    for candidate_type in ("cancel_candidate", "switch_task_candidate", "confirmation_candidate"):
        if candidate_type in candidate_types:
            interpretation_type, reason = CONTROL_PATCH_INTERPRETATIONS[candidate_type]
            return interpretation_type, False, reason

    # A candidate type is only a Router/Thinker hypothesis.  When the
    # Workbench provides the current resolved slots and the newly extracted
    # slot values, SlowTask owns the semantic diff: filling a missing field
    # makes the current plan executable but does not replace that plan.
    # Replacing an established value does require a new plan_version.
    if current_resolved_slots is not None and incoming_slot_values:
        changed_fields = tuple(
            field
            for field, incoming_value in incoming_slot_values.items()
            if current_resolved_slots.get(field) not in (None, "")
            and _normalized_slot_value(current_resolved_slots[field])
            != _normalized_slot_value(incoming_value)
            and not _is_slot_refinement(
                field=field,
                current_value=current_resolved_slots[field],
                incoming_value=incoming_value,
            )
        )
        if changed_fields:
            return (
                "constraint_update",
                True,
                "established_slot_values_replaced:" + ",".join(changed_fields),
            )
        newly_resolved_fields = tuple(
            field
            for field, incoming_value in incoming_slot_values.items()
            if incoming_value not in (None, "") and current_resolved_slots.get(field) in (None, "")
        )
        if newly_resolved_fields:
            return (
                "slot_update",
                False,
                "previously_unresolved_slots_filled:" + ",".join(newly_resolved_fields),
            )
        return "slot_update", False, "incoming_slot_values_equivalent_to_current_plan"

    for candidate_type, (interpretation_type, reason) in MATERIAL_PATCH_CANDIDATES.items():
        if candidate_type in candidate_types:
            return interpretation_type, True, reason
    for candidate_type, (interpretation_type, reason) in NON_MATERIAL_PATCH_CANDIDATES.items():
        if candidate_type in candidate_types:
            return interpretation_type, False, reason
    return "irrelevant", False, "mock_no_material_candidate"


def _normalized_slot_value(value: object) -> str:
    """Compare compact slot values without treating formatting as a revision."""

    return "".join(str(value).lower().split()).replace("，", "").replace("。", "")


def _is_slot_refinement(*, field: str, current_value: object, incoming_value: object) -> bool:
    """Return true when a vague meal period is narrowed, not replaced.

    The Workbench slot extractor intentionally accepts “午饭/晚饭” as a
    usable broad time window.  A later 12:00 or 18:30 therefore refines the
    same meal period; it must not be mistaken for a user reversing an already
    settled constraint.  A cross-period change such as lunch to evening is
    still material and advances plan_version.
    """

    if field != "time_window":
        return False
    current = _normalized_slot_value(current_value)
    incoming = _normalized_slot_value(incoming_value)
    hour_match = re.search(r"(\d{1,2}):\d{2}", incoming)
    if hour_match is None:
        return False
    hour = int(hour_match.group(1))
    is_lunch_period = any(marker in current for marker in ("午饭", "午餐", "中午"))
    is_dinner_period = any(marker in current for marker in ("晚饭", "晚餐", "晚上"))
    return (is_lunch_period and 10 <= hour < 16) or (is_dinner_period and 16 <= hour <= 23)


def _source_evidence_refs_from_user_patch(event: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for field in ("evidence_ref", "authoritative_evidence_refs", "non_authoritative_hypothesis_refs"):
        refs.extend(_string_tuple(event.get(field)))
    return tuple(dict.fromkeys(ref for ref in refs if ref))


def _fields_cover(required_fields: tuple[str, ...], candidate_fields: tuple[str, ...]) -> bool:
    return set(required_fields).issubset(set(candidate_fields))


def _merge_field_tuples(*field_groups: tuple[str, ...]) -> tuple[str, ...]:
    fields: list[str] = []
    seen: set[str] = set()
    for group in field_groups:
        for field in group:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    return tuple(fields)


def _validate_resolved_arguments_inputs(
    *,
    required_fields: tuple[str, ...],
    resolved_fields: tuple[str, ...],
    resolved_arguments_ref: str | None,
    provenance_ref: str | None,
) -> None:
    if not _fields_cover(required_fields, resolved_fields):
        raise ValueError("sufficient evidence review requires resolved_fields to cover required_fields")
    if not resolved_arguments_ref:
        raise ValueError("sufficient evidence review requires resolved_arguments_ref")
    if not provenance_ref:
        raise ValueError("sufficient evidence review requires provenance_ref")


def _validate_clarification_prompt_ref(clarification_prompt_ref: str | None) -> None:
    if not clarification_prompt_ref:
        raise ValueError("evidence review requires clarification_prompt_ref")


def _int_field(event: Mapping[str, Any], field: str) -> int:
    value = event[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (str(value),)
    if not isinstance(value, Sequence):
        raise ValueError("expected a sequence of strings")
    return tuple(str(item) for item in value)
