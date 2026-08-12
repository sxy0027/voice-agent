from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections.abc import Sequence
from typing import Any, Mapping


class SlowTaskStateError(ValueError):
    pass


SLOWTASK_EVENT_NAMES = frozenset(
    {
        "SLOWTASK_CREATED",
        "SLOWTASK_STATE_CHANGED",
        "USER_PATCH_RECEIVED",
        "USER_PATCH_INTERPRETED",
        "PLAN_VERSION_ADVANCED",
        "TASK_REPLANNED",
        "TASK_REQUIREMENT_MODEL_ACCEPTED",
        "TASK_REQUIREMENT_MODEL_INVALIDATED",
        "EVIDENCE_REVIEWED",
        "AMBIGUITY_DETECTED",
        "AMBIGUITY_RESOLVED",
        "CLARIFICATION_REQUESTED",
        "ARGUMENTS_RESOLVED",
        "ARGUMENT_RESOLUTION_PROVENANCE",
        "INSUFFICIENT_EVIDENCE_FOR_ACTION",
        "PLANNING_STARTED",
        "PLANNING_RESTARTED",
        "WAITING_FOR_SLOT",
        "WAITING_FOR_TOOL",
        "WAITING_FOR_USER_CONFIRMATION",
        "FINALIZING",
        "SLOWTASK_DEGRADED",
        "SLOWTASK_FAILED",
        "CONFIRMATION_REQUIRED",
        "USER_CONFIRMATION_RECEIVED",
        "CONFIRMATION_ACCEPTED",
        "CONFIRMATION_REJECTED",
        "SLOWTASK_CANCEL_REQUESTED",
        "SLOWTASK_CANCELLED",
        "TOOL_CALL_STARTED",
        "TOOL_EXECUTION_STARTED",
        "TOOL_RESULT_RECEIVED",
        "TOOL_RESULT_MARKED_STALE",
        "STALE_EVIDENCE_RECORDED",
        "STALE_EVIDENCE_ADOPTED",
        "SEMANTIC_COMMITMENT_EMITTED",
    }
)
LIFECYCLE_STATES = frozenset(
    {
        "CREATED",
        "WAITING_FOR_SLOT",
        "PLANNING",
        "EXECUTING",
        "WAITING_FOR_USER_CONFIRMATION",
        "COMPLETED",
        "CANCELLED",
        "FAILED",
    }
)
TERMINAL_STATES = frozenset({"COMPLETED", "CANCELLED", "FAILED"})
LATE_TERMINAL_EVIDENCE_EVENTS = frozenset(
    {
        "USER_PATCH_RECEIVED",
        "USER_PATCH_INTERPRETED",
        "TOOL_RESULT_RECEIVED",
        "TOOL_RESULT_MARKED_STALE",
        "STALE_EVIDENCE_RECORDED",
        "CONFIRMATION_REQUIRED",
        "USER_CONFIRMATION_RECEIVED",
        "CONFIRMATION_ACCEPTED",
        "CONFIRMATION_REJECTED",
    }
)
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"CREATED", "PLANNING", "CANCELLED", "FAILED"}),
    "PLANNING": frozenset(
        {
            "PLANNING",
            "WAITING_FOR_SLOT",
            "WAITING_FOR_USER_CONFIRMATION",
            "EXECUTING",
            "COMPLETED",
            "CANCELLED",
            "FAILED",
        }
    ),
    "WAITING_FOR_SLOT": frozenset(
        {
            "WAITING_FOR_SLOT",
            "PLANNING",
            "WAITING_FOR_USER_CONFIRMATION",
            "CANCELLED",
            "FAILED",
        }
    ),
    "EXECUTING": frozenset(
        {
            "EXECUTING",
            "PLANNING",
            "WAITING_FOR_USER_CONFIRMATION",
            "COMPLETED",
            "CANCELLED",
            "FAILED",
        }
    ),
    "WAITING_FOR_USER_CONFIRMATION": frozenset(
        {
            "WAITING_FOR_USER_CONFIRMATION",
            "PLANNING",
            "EXECUTING",
            "CANCELLED",
            "FAILED",
        }
    ),
    "COMPLETED": frozenset({"COMPLETED"}),
    "CANCELLED": frozenset({"CANCELLED"}),
    "FAILED": frozenset({"FAILED"}),
}


@dataclass(frozen=True)
class UserPatchEvidence:
    event_id: str
    patch_id: str
    plan_version: int
    task_event_seq: int
    observed_plan_version: int
    evidence_ref: str
    turn_id: str | None = None
    utterance_id: str | None = None


@dataclass(frozen=True)
class UserPatchInterpretation:
    event_id: str
    caused_by_event_id: str | None
    patch_id: str
    plan_version: int
    task_event_seq: int
    observed_plan_version: int
    interpreted_against_plan_version: int
    interpretation_type: str
    materially_changes_task: bool
    interpretation_reason: str | None = None
    source_evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class StateTransition:
    event_id: str
    plan_version: int
    task_event_seq: int
    from_state: str
    to_state: str
    reason: str


@dataclass(frozen=True)
class PlanAdvance:
    event_id: str
    task_event_seq: int
    from_plan_version: int
    to_plan_version: int
    planning_reason: str
    caused_by_user_patch_event_id: str | None = None


@dataclass(frozen=True)
class RefEvent:
    event_id: str
    event_name: str
    plan_version: int
    task_event_seq: int
    refs: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class ToolCallMetadata:
    event_id: str
    source_event_name: str
    tool_call_id: str
    plan_version: int
    task_event_seq: int
    tool_name: str | None
    idempotency_key: str


@dataclass(frozen=True)
class ToolResultMetadata:
    event_id: str
    tool_call_id: str
    plan_version: int
    task_event_seq: int
    result_status: str
    result_ref: str
    is_current_plan: bool


@dataclass(frozen=True)
class PendingStaleToolResult:
    source_tool_result_event_id: str
    tool_call_id: str
    result_plan_version: int
    task_event_seq: int
    marked_stale_event_id: str | None = None
    stale_evidence_ref: str | None = None
    stale_evidence_recorded_event_id: str | None = None


@dataclass(frozen=True)
class StaleMark:
    event_id: str
    tool_call_id: str
    plan_version: int
    task_event_seq: int
    result_plan_version: int
    current_plan_version: int
    stale_reason: str


@dataclass(frozen=True)
class AdoptedEvidence:
    event_id: str
    plan_version: int
    task_event_seq: int
    stale_evidence_ref: str
    source_tool_result_event_id: str
    adopted_from_plan_version: int
    adoption_mode: str
    adoption_reason: str
    adopted_scope: tuple[str, ...]
    adopted_by_event_id: str


@dataclass(frozen=True)
class ConfirmationState:
    pending_confirmation_id: str | None = None
    status: str | None = None
    patch_id: str | None = None
    confirmation_scope: str | None = None
    required_for_event_id: str | None = None
    prompt_ref: str | None = None
    accepted_scope: str | None = None
    authorization_ref: str | None = None
    rejection_reason: str | None = None
    last_confirmation_event_id: str | None = None


@dataclass(frozen=True)
class SemanticCommitment:
    event_id: str
    commitment_id: str
    plan_version: int
    task_event_seq: int
    source_events: tuple[str, ...]
    commitment_ref: str | None = None


@dataclass(frozen=True)
class LateEvent:
    event_id: str
    event_name: str
    plan_version: int
    task_event_seq: int
    reason: str


@dataclass
class SlowTaskRecord:
    task_id: str
    lifecycle_state: str
    current_plan_version: int
    current_task_event_seq: int
    initial_goal_ref: str
    source_evidence_refs: tuple[str, ...] = ()
    constraints_ref: str | None = None
    task_requirement_model_ref: str | None = None
    task_requirement_model_version: int | None = None
    task_kind: str | None = None
    task_requirement_model: Mapping[str, Any] | None = None
    task_model_status: str | None = None
    task_model_confidence: str | None = None
    task_model_bootstrap_reason: str | None = None
    task_model_needs_remodeling: bool = False
    invalidated_requirement_model_refs: tuple[str, ...] = ()
    requirement_states: dict[str, str] = field(default_factory=dict)
    requirement_state_evidence_refs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    user_patch_evidence: tuple[UserPatchEvidence, ...] = ()
    user_patch_interpretations: tuple[UserPatchInterpretation, ...] = ()
    state_transitions: tuple[StateTransition, ...] = ()
    plan_advances: tuple[PlanAdvance, ...] = ()
    progress_events: tuple[RefEvent, ...] = ()
    evidence_events: tuple[RefEvent, ...] = ()
    resolved_arguments_refs: tuple[str, ...] = ()
    argument_provenance_refs: tuple[str, ...] = ()
    tool_validation_reports: tuple[Mapping[str, Any], ...] = ()
    tool_calls: tuple[ToolCallMetadata, ...] = ()
    tool_results: tuple[ToolResultMetadata, ...] = ()
    pending_stale_tool_results: tuple[PendingStaleToolResult, ...] = ()
    stale_marks: tuple[StaleMark, ...] = ()
    stale_evidence_refs: tuple[str, ...] = ()
    adopted_evidence: tuple[AdoptedEvidence, ...] = ()
    confirmation_state: ConfirmationState = field(default_factory=ConfirmationState)
    cancel_request_event_id: str | None = None
    cancelled_event_id: str | None = None
    cancel_reason: str | None = None
    failure_reason: str | None = None
    degraded_reasons: tuple[str, ...] = ()
    semantic_commitments: tuple[SemanticCommitment, ...] = ()
    late_events: tuple[LateEvent, ...] = ()
    terminal_outcome: str | None = None
    completed_event_id: str | None = None
    last_slowtask_event_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.lifecycle_state in TERMINAL_STATES


@dataclass
class SlowTaskState:
    tasks: dict[str, SlowTaskRecord] = field(default_factory=dict)
    last_task_id: str | None = None

    def reduce_event(self, event: Mapping[str, Any]) -> bool:
        event_name = str(event["event_name"])
        if event_name not in SLOWTASK_EVENT_NAMES:
            return False

        if event_name == "SLOWTASK_CREATED":
            self._handle_created(event)
            return True

        task = self._task_for_event(event)
        self._require_next_task_event_seq(task, event)
        if task.is_terminal:
            if event_name in LATE_TERMINAL_EVIDENCE_EVENTS:
                self._record_late_event(task, event)
                return True
            if event_name == "SLOWTASK_STATE_CHANGED":
                self._handle_state_changed(event, task)
                return True
            raise SlowTaskStateError(f"{event_name} cannot advance terminal SlowTask {task.task_id}")

        if event_name == "SLOWTASK_STATE_CHANGED":
            self._handle_state_changed(event, task)
        elif event_name == "PLAN_VERSION_ADVANCED":
            self._handle_plan_version_advanced(event, task)
        elif event_name == "USER_PATCH_RECEIVED":
            self._handle_user_patch_received(event, task)
        elif event_name == "USER_PATCH_INTERPRETED":
            self._handle_user_patch_interpreted(event, task)
        elif event_name == "TASK_REQUIREMENT_MODEL_ACCEPTED":
            self._handle_requirement_model_accepted(event, task)
        elif event_name == "TASK_REQUIREMENT_MODEL_INVALIDATED":
            self._handle_requirement_model_invalidated(event, task)
        elif event_name in {
            "TASK_REPLANNED",
            "PLANNING_STARTED",
            "PLANNING_RESTARTED",
            "WAITING_FOR_SLOT",
            "WAITING_FOR_TOOL",
            "WAITING_FOR_USER_CONFIRMATION",
            "FINALIZING",
        }:
            self._handle_progress_event(event, task)
        elif event_name in {
            "EVIDENCE_REVIEWED",
            "AMBIGUITY_DETECTED",
            "AMBIGUITY_RESOLVED",
            "CLARIFICATION_REQUESTED",
            "ARGUMENTS_RESOLVED",
            "ARGUMENT_RESOLUTION_PROVENANCE",
            "INSUFFICIENT_EVIDENCE_FOR_ACTION",
        }:
            self._handle_evidence_event(event, task)
        elif event_name == "SLOWTASK_DEGRADED":
            self._handle_degraded(event, task)
        elif event_name == "SLOWTASK_FAILED":
            self._handle_failed(event, task)
        elif event_name in {
            "CONFIRMATION_REQUIRED",
            "USER_CONFIRMATION_RECEIVED",
            "CONFIRMATION_ACCEPTED",
            "CONFIRMATION_REJECTED",
        }:
            self._handle_confirmation_event(event, task)
        elif event_name in {"SLOWTASK_CANCEL_REQUESTED", "SLOWTASK_CANCELLED"}:
            self._handle_cancel_event(event, task)
        elif event_name == "TOOL_CALL_STARTED":
            self._handle_tool_call_started(event, task)
        elif event_name == "TOOL_EXECUTION_STARTED":
            self._handle_tool_execution_started(event, task)
        elif event_name == "TOOL_RESULT_RECEIVED":
            self._handle_tool_result_received(event, task)
        elif event_name == "TOOL_RESULT_MARKED_STALE":
            self._handle_tool_result_marked_stale(event, task)
        elif event_name == "STALE_EVIDENCE_RECORDED":
            self._handle_stale_evidence_recorded(event, task)
        elif event_name == "STALE_EVIDENCE_ADOPTED":
            self._handle_stale_evidence_adopted(event, task)
        elif event_name == "SEMANTIC_COMMITMENT_EMITTED":
            self._handle_semantic_commitment(event, task)
        else:
            raise SlowTaskStateError(f"Unhandled SlowTask event: {event_name}")

        task.last_slowtask_event_id = str(event["event_id"])
        return True

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "last_task_id": self.last_task_id,
            "tasks": {
                task_id: asdict(self.tasks[task_id])
                for task_id in sorted(self.tasks)
            },
        }

    def validate_replay_complete(self) -> None:
        incomplete = [
            pending.source_tool_result_event_id
            for task in self.tasks.values()
            for pending in task.pending_stale_tool_results
            if pending.stale_evidence_ref is None
        ]
        if incomplete:
            raise SlowTaskStateError(
                "old-plan TOOL_RESULT_RECEIVED requires stale evidence chain before replay completes: "
                f"{sorted(incomplete)}"
            )

    def _handle_created(self, event: Mapping[str, Any]) -> None:
        task_id = str(event["task_id"])
        if task_id in self.tasks:
            raise SlowTaskStateError(f"Duplicate SLOWTASK_CREATED for task_id={task_id}")
        active_task_ids = sorted(
            existing_task_id
            for existing_task_id, existing_task in self.tasks.items()
            if not existing_task.is_terminal
        )
        if active_task_ids:
            raise SlowTaskStateError(
                "SLOWTASK_CREATED violates single active SlowTask invariant: "
                f"active_task_ids={active_task_ids}"
            )

        task = SlowTaskRecord(
            task_id=task_id,
            lifecycle_state="CREATED",
            current_plan_version=_int_field(event, "plan_version"),
            current_task_event_seq=_int_field(event, "task_event_seq"),
            initial_goal_ref=str(event["initial_goal_ref"]),
            source_evidence_refs=_string_tuple(event.get("source_evidence_refs", ())),
            constraints_ref=_optional_str(event.get("constraints_ref")),
            last_slowtask_event_id=str(event["event_id"]),
        )
        self.tasks[task_id] = task
        self.last_task_id = task_id

    def _handle_state_changed(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        from_state = str(event["from_state"])
        to_state = str(event["to_state"])
        if from_state != task.lifecycle_state:
            raise SlowTaskStateError(
                f"SLOWTASK_STATE_CHANGED from_state={from_state} does not match current "
                f"state={task.lifecycle_state}"
            )
        if to_state not in LIFECYCLE_STATES:
            raise SlowTaskStateError(f"Unknown SlowTask state: {to_state}")
        if to_state not in LEGAL_TRANSITIONS[from_state]:
            raise SlowTaskStateError(f"Illegal SlowTask transition: {from_state} -> {to_state}")
        if to_state == "CANCELLED" and task.cancelled_event_id is None:
            raise SlowTaskStateError(
                "SLOWTASK_STATE_CHANGED to_state=CANCELLED requires prior SLOWTASK_CANCELLED"
            )

        task.state_transitions = (
            *task.state_transitions,
            StateTransition(
                event_id=str(event["event_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                from_state=from_state,
                to_state=to_state,
                reason=str(event["reason"]),
            ),
        )
        task.lifecycle_state = to_state
        self._advance_task_event_seq(task, event)
        if to_state in TERMINAL_STATES:
            task.terminal_outcome = to_state
            task.completed_event_id = str(event["event_id"])
            if to_state == "CANCELLED" and task.cancel_reason is None:
                task.cancel_reason = str(event["reason"])
            if to_state == "FAILED" and task.failure_reason is None:
                task.failure_reason = str(event["reason"])

    def _handle_requirement_model_accepted(
        self, event: Mapping[str, Any], task: SlowTaskRecord
    ) -> None:
        self._require_current_plan(event, task)
        payload = event.get("model_payload")
        if not isinstance(payload, Mapping):
            raise SlowTaskStateError("TASK_REQUIREMENT_MODEL_ACCEPTED requires model_payload object")
        model_ref = str(event["model_ref"])
        if task.task_requirement_model_ref is not None and task.task_requirement_model_ref != model_ref:
            raise SlowTaskStateError("accepted requirement model must be invalidated before replacement")
        task.task_requirement_model_ref = model_ref
        task.task_requirement_model_version = _int_field(event, "model_version")
        task.task_kind = str(event["task_kind"])
        task.task_requirement_model = dict(payload)
        task.task_model_status = str(event["model_status"])
        task.task_model_confidence = str(event["model_confidence"])
        bootstrap_reason = _optional_str(event.get("bootstrap_reason"))
        task.task_model_bootstrap_reason = (
            None if bootstrap_reason == "not_applicable" else bootstrap_reason
        )
        task.task_model_needs_remodeling = bool(event["needs_remodeling"])
        self._advance_task_event_seq(task, event)

    def _handle_requirement_model_invalidated(
        self, event: Mapping[str, Any], task: SlowTaskRecord
    ) -> None:
        self._require_current_plan(event, task)
        model_ref = str(event["model_ref"])
        if task.task_requirement_model_ref != model_ref:
            raise SlowTaskStateError("TASK_REQUIREMENT_MODEL_INVALIDATED must reference current model")
        task.invalidated_requirement_model_refs = (*task.invalidated_requirement_model_refs, model_ref)
        task.task_requirement_model_ref = None
        task.task_requirement_model_version = None
        task.task_kind = None
        task.task_requirement_model = None
        task.task_model_status = None
        task.task_model_confidence = None
        task.task_model_bootstrap_reason = None
        task.task_model_needs_remodeling = False
        task.requirement_states.clear()
        task.requirement_state_evidence_refs.clear()
        self._advance_task_event_seq(task, event)

    def _handle_plan_version_advanced(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        from_plan_version = _int_field(event, "from_plan_version")
        to_plan_version = _int_field(event, "to_plan_version")
        event_plan_version = _int_field(event, "plan_version")
        caused_by_user_patch_event_id = _optional_str(event.get("caused_by_user_patch_event_id"))
        planning_reason = str(event["planning_reason"])
        if from_plan_version != task.current_plan_version:
            raise SlowTaskStateError(
                f"PLAN_VERSION_ADVANCED from_plan_version={from_plan_version} does not match "
                f"current plan_version={task.current_plan_version}"
            )
        if event_plan_version != to_plan_version:
            raise SlowTaskStateError("PLAN_VERSION_ADVANCED plan_version must equal to_plan_version")
        if to_plan_version <= from_plan_version:
            raise SlowTaskStateError("PLAN_VERSION_ADVANCED must increase plan_version")
        if task.confirmation_state.pending_confirmation_id is not None:
            raise SlowTaskStateError(
                "PLAN_VERSION_ADVANCED requires pending confirmation to be rejected or superseded first"
            )
        if _is_material_user_patch_planning_reason(planning_reason) and caused_by_user_patch_event_id is None:
            raise SlowTaskStateError(
                "PLAN_VERSION_ADVANCED planning_reason=material_user_patch requires "
                "caused_by_user_patch_event_id"
            )
        if caused_by_user_patch_event_id is not None and not _has_material_user_patch_interpretation_for_event(
            task,
            user_patch_event_id=caused_by_user_patch_event_id,
            plan_version=from_plan_version,
        ):
            raise SlowTaskStateError(
                "PLAN_VERSION_ADVANCED caused_by_user_patch_event_id requires prior material "
                "USER_PATCH_INTERPRETED"
            )

        task.plan_advances = (
            *task.plan_advances,
            PlanAdvance(
                event_id=str(event["event_id"]),
                task_event_seq=_int_field(event, "task_event_seq"),
                from_plan_version=from_plan_version,
                to_plan_version=to_plan_version,
                planning_reason=planning_reason,
                caused_by_user_patch_event_id=caused_by_user_patch_event_id,
            ),
        )
        task.current_plan_version = to_plan_version
        self._advance_task_event_seq(task, event)

    def _handle_user_patch_received(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        observed_plan_version = _int_field(event, "observed_plan_version")
        if observed_plan_version != task.current_plan_version:
            raise SlowTaskStateError(
                "USER_PATCH_RECEIVED observed_plan_version must match current plan_version"
            )
        task.user_patch_evidence = (
            *task.user_patch_evidence,
            UserPatchEvidence(
                event_id=str(event["event_id"]),
                patch_id=str(event["patch_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                observed_plan_version=observed_plan_version,
                evidence_ref=str(event["evidence_ref"]),
                turn_id=_optional_str(event.get("turn_id")),
                utterance_id=_optional_str(event.get("utterance_id")),
            ),
        )
        self._advance_task_event_seq(task, event)

    def _handle_user_patch_interpreted(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        interpreted_against = _int_field(event, "interpreted_against_plan_version")
        observed_plan_version = _int_field(event, "observed_plan_version")
        if observed_plan_version != task.current_plan_version:
            raise SlowTaskStateError(
                "USER_PATCH_INTERPRETED observed_plan_version must match current plan_version"
            )
        if interpreted_against != observed_plan_version:
            raise SlowTaskStateError(
                "USER_PATCH_INTERPRETED interpreted_against_plan_version must match observed_plan_version"
            )
        patch_id = str(event["patch_id"])
        if not _has_received_user_patch_evidence(task, patch_id=patch_id):
            raise SlowTaskStateError(
                "USER_PATCH_INTERPRETED requires prior USER_PATCH_RECEIVED evidence for patch_id"
            )
        task.user_patch_interpretations = (
            *task.user_patch_interpretations,
            UserPatchInterpretation(
                event_id=str(event["event_id"]),
                caused_by_event_id=_optional_str(event.get("caused_by_event_id")),
                patch_id=patch_id,
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                observed_plan_version=observed_plan_version,
                interpreted_against_plan_version=interpreted_against,
                interpretation_type=str(event["interpretation_type"]),
                materially_changes_task=bool(event["materially_changes_task"]),
                interpretation_reason=_optional_str(event.get("interpretation_reason")),
                source_evidence_refs=_string_tuple(event.get("source_evidence_refs", ())),
            ),
        )
        self._advance_task_event_seq(task, event)

    def _handle_progress_event(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        event_name = str(event["event_name"])
        refs: tuple[str, ...] = ()
        reason = _optional_str(event.get("planning_reason") or event.get("restart_reason"))
        if event_name == "TASK_REPLANNED":
            refs = _optional_ref_tuple(event.get("superseded_plan_version"))
        elif event_name == "WAITING_FOR_SLOT":
            refs = _string_tuple(event.get("missing_fields", ()))
        elif event_name == "WAITING_FOR_TOOL":
            tool_call_id = str(event["tool_call_id"])
            if not _has_matching_tool_execution_start(
                task,
                tool_call_id=tool_call_id,
                plan_version=_int_field(event, "plan_version"),
            ):
                raise SlowTaskStateError("WAITING_FOR_TOOL requires prior matching TOOL_EXECUTION_STARTED")
            refs = (tool_call_id,)
        elif event_name == "WAITING_FOR_USER_CONFIRMATION":
            confirmation_id = str(event["confirmation_id"])
            if task.confirmation_state.pending_confirmation_id != confirmation_id:
                raise SlowTaskStateError(
                    "WAITING_FOR_USER_CONFIRMATION requires matching pending confirmation"
                )
            refs = (confirmation_id,)
        elif event_name == "FINALIZING":
            refs = _string_tuple(event.get("source_events", ()))
            _require_stale_evidence_adopted_for_advancement(
                task,
                event,
                source_event_ids=refs,
            )
        task.progress_events = (*task.progress_events, _ref_event(event, refs=refs, reason=reason))
        self._advance_task_event_seq(task, event)

    def _handle_evidence_event(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        event_name = str(event["event_name"])
        refs: tuple[str, ...] = ()
        reason: str | None = None
        if event_name == "EVIDENCE_REVIEWED":
            refs = _string_tuple(event.get("evidence_refs", ()))
            _require_stale_evidence_adopted_for_advancement(task, event, refs=refs)
            reason = str(event["review_result"])
            accepted_states = event.get("accepted_requirement_states", ())
            if isinstance(accepted_states, Sequence) and not isinstance(accepted_states, (str, bytes)):
                for item in accepted_states:
                    if not isinstance(item, Mapping):
                        raise SlowTaskStateError("accepted_requirement_states must contain objects")
                    requirement_id = str(item.get("requirement_id", ""))
                    status = str(item.get("status", ""))
                    if not requirement_id or status not in {
                        "UNKNOWN", "CANDIDATE", "RESOLVED", "AMBIGUOUS", "CONFLICTING",
                        "NOT_APPLICABLE", "DEFAULTED",
                    }:
                        raise SlowTaskStateError("invalid accepted requirement state")
                    task.requirement_states[requirement_id] = status
                    task.requirement_state_evidence_refs[requirement_id] = _string_tuple(
                        item.get("source_evidence_refs", ())
                    )
        elif event_name == "AMBIGUITY_DETECTED":
            refs = (*_string_tuple(event.get("ambiguous_fields", ())), *_string_tuple(event.get("source_evidence_refs", ())))
            _require_stale_evidence_adopted_for_advancement(task, event, refs=refs)
        elif event_name == "AMBIGUITY_RESOLVED":
            refs = (*_string_tuple(event.get("resolved_fields", ())), *_string_tuple(event.get("source_evidence_refs", ())))
            _require_stale_evidence_adopted_for_advancement(task, event, refs=refs)
            reason = str(event["resolution_reason"])
        elif event_name == "CLARIFICATION_REQUESTED":
            refs = (
                *_string_tuple(event.get("missing_or_ambiguous_fields", ())),
                str(event["clarification_prompt_ref"]),
            )
        elif event_name == "ARGUMENTS_RESOLVED":
            resolved_arguments_ref = str(event["resolved_arguments_ref"])
            provenance_ref = str(event["provenance_ref"])
            _require_stale_evidence_adopted_for_advancement(
                task,
                event,
                refs=(resolved_arguments_ref, provenance_ref),
            )
            task.resolved_arguments_refs = _append_unique(task.resolved_arguments_refs, resolved_arguments_ref)
            task.argument_provenance_refs = _append_unique(task.argument_provenance_refs, provenance_ref)
            report = event.get("tool_validation_report")
            if isinstance(report, Mapping):
                task.tool_validation_reports = (*task.tool_validation_reports, dict(report))
            refs = (resolved_arguments_ref, provenance_ref)
        elif event_name == "ARGUMENT_RESOLUTION_PROVENANCE":
            refs = _string_tuple(event.get("field_provenance_refs", ()))
            _require_stale_evidence_adopted_for_advancement(task, event, refs=refs)
            task.argument_provenance_refs = _append_many_unique(task.argument_provenance_refs, refs)
        elif event_name == "INSUFFICIENT_EVIDENCE_FOR_ACTION":
            refs = (*_string_tuple(event.get("blocking_fields", ())), *_string_tuple(event.get("source_evidence_refs", ())))
            _require_stale_evidence_adopted_for_advancement(task, event, refs=refs)
        task.evidence_events = (*task.evidence_events, _ref_event(event, refs=refs, reason=reason))
        self._advance_task_event_seq(task, event)

    def _handle_degraded(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        degraded_reason = str(event["degraded_reason"])
        task.degraded_reasons = (*task.degraded_reasons, degraded_reason)
        task.progress_events = (
            *task.progress_events,
            _ref_event(
                event,
                refs=_optional_ref_tuple(event.get("capability_or_tool_ref")),
                reason=degraded_reason,
            ),
        )
        self._advance_task_event_seq(task, event)

    def _handle_failed(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        task.failure_reason = str(event["failure_reason"])
        task.progress_events = (
            *task.progress_events,
            _ref_event(event, reason=task.failure_reason),
        )
        self._advance_task_event_seq(task, event)

    def _handle_confirmation_event(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        event_name = str(event["event_name"])
        confirmation_id = str(event["confirmation_id"])
        if event_name == "CONFIRMATION_REQUIRED":
            task.confirmation_state = ConfirmationState(
                pending_confirmation_id=confirmation_id,
                status="required",
                confirmation_scope=str(event["confirmation_scope"]),
                required_for_event_id=str(event["required_for_event_id"]),
                prompt_ref=str(event["prompt_ref"]),
                last_confirmation_event_id=str(event["event_id"]),
            )
        elif event_name == "USER_CONFIRMATION_RECEIVED":
            if task.confirmation_state.pending_confirmation_id != confirmation_id:
                raise SlowTaskStateError(
                    "USER_CONFIRMATION_RECEIVED requires matching pending confirmation"
                )
            patch_id = str(event["patch_id"])
            if not _has_interpreted_user_patch(task, patch_id=patch_id):
                raise SlowTaskStateError(
                    "USER_CONFIRMATION_RECEIVED requires prior USER_PATCH_INTERPRETED for patch_id"
                )
            task.confirmation_state = ConfirmationState(
                pending_confirmation_id=confirmation_id,
                status=str(event["confirmation_signal"]),
                patch_id=patch_id,
                confirmation_scope=task.confirmation_state.confirmation_scope,
                required_for_event_id=task.confirmation_state.required_for_event_id,
                prompt_ref=task.confirmation_state.prompt_ref,
                last_confirmation_event_id=str(event["event_id"]),
            )
        elif event_name == "CONFIRMATION_ACCEPTED":
            if task.confirmation_state.pending_confirmation_id != confirmation_id:
                raise SlowTaskStateError("CONFIRMATION_ACCEPTED requires matching pending confirmation")
            if task.confirmation_state.status != "accepted":
                raise SlowTaskStateError(
                    "CONFIRMATION_ACCEPTED requires prior USER_CONFIRMATION_RECEIVED accepted signal"
                )
            accepted_scope = str(event["accepted_scope"])
            if accepted_scope != task.confirmation_state.confirmation_scope:
                raise SlowTaskStateError(
                    "CONFIRMATION_ACCEPTED accepted_scope must match pending confirmation_scope"
                )
            task.confirmation_state = ConfirmationState(
                pending_confirmation_id=None,
                status="accepted",
                patch_id=task.confirmation_state.patch_id,
                confirmation_scope=task.confirmation_state.confirmation_scope,
                required_for_event_id=task.confirmation_state.required_for_event_id,
                prompt_ref=task.confirmation_state.prompt_ref,
                accepted_scope=accepted_scope,
                authorization_ref=str(event["authorization_ref"]),
                last_confirmation_event_id=str(event["event_id"]),
            )
        elif event_name == "CONFIRMATION_REJECTED":
            if task.confirmation_state.pending_confirmation_id != confirmation_id:
                raise SlowTaskStateError("CONFIRMATION_REJECTED requires matching pending confirmation")
            task.confirmation_state = ConfirmationState(
                pending_confirmation_id=None,
                status="rejected",
                patch_id=task.confirmation_state.patch_id,
                confirmation_scope=task.confirmation_state.confirmation_scope,
                required_for_event_id=task.confirmation_state.required_for_event_id,
                prompt_ref=task.confirmation_state.prompt_ref,
                rejection_reason=str(event["rejection_reason"]),
                last_confirmation_event_id=str(event["event_id"]),
            )
        self._advance_task_event_seq(task, event)

    def _handle_cancel_event(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        event_name = str(event["event_name"])
        if event_name == "SLOWTASK_CANCELLED" and task.cancel_request_event_id is None:
            raise SlowTaskStateError("SLOWTASK_CANCELLED requires prior SLOWTASK_CANCEL_REQUESTED")
        if event_name == "SLOWTASK_CANCEL_REQUESTED":
            task.cancel_request_event_id = str(event["event_id"])
        elif event_name == "SLOWTASK_CANCELLED":
            task.cancelled_event_id = str(event["event_id"])
        task.cancel_reason = str(event["cancel_reason"])
        task.progress_events = (
            *task.progress_events,
            _ref_event(event, reason=task.cancel_reason),
        )
        self._advance_task_event_seq(task, event)

    def _handle_tool_call_started(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        task.tool_calls = (
            *task.tool_calls,
            ToolCallMetadata(
                event_id=str(event["event_id"]),
                source_event_name=str(event["event_name"]),
                tool_call_id=str(event["tool_call_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                tool_name=str(event["tool_name"]),
                idempotency_key=str(event["idempotency_key"]),
            ),
        )
        self._advance_task_event_seq(task, event)

    def _handle_tool_execution_started(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        tool_call_id = str(event["tool_call_id"])
        plan_version = _int_field(event, "plan_version")
        if not _has_matching_tool_execution_start(task, tool_call_id=tool_call_id, plan_version=plan_version):
            task.tool_calls = (
                *task.tool_calls,
                ToolCallMetadata(
                    event_id=str(event["event_id"]),
                    source_event_name=str(event["event_name"]),
                    tool_call_id=tool_call_id,
                    plan_version=plan_version,
                    task_event_seq=_int_field(event, "task_event_seq"),
                    tool_name=_optional_str(event.get("tool_name")),
                    idempotency_key=str(event["idempotency_key"]),
                ),
            )
        self._advance_task_event_seq(task, event)

    def _handle_tool_result_received(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        event_plan_version = _int_field(event, "plan_version")
        if event_plan_version > task.current_plan_version:
            raise SlowTaskStateError("TOOL_RESULT_RECEIVED cannot come from a future plan_version")
        tool_call_id = str(event["tool_call_id"])
        if not _has_matching_tool_call(task, tool_call_id=tool_call_id, plan_version=event_plan_version):
            raise SlowTaskStateError(
                "TOOL_RESULT_RECEIVED requires prior matching TOOL_CALL_STARTED or TOOL_EXECUTION_STARTED"
            )
        task.tool_results = (
            *task.tool_results,
            ToolResultMetadata(
                event_id=str(event["event_id"]),
                tool_call_id=tool_call_id,
                plan_version=event_plan_version,
                task_event_seq=_int_field(event, "task_event_seq"),
                result_status=str(event["result_status"]),
                result_ref=str(event["result_ref"]),
                is_current_plan=event_plan_version == task.current_plan_version,
            ),
        )
        if event_plan_version < task.current_plan_version:
            task.pending_stale_tool_results = (
                *task.pending_stale_tool_results,
                PendingStaleToolResult(
                    source_tool_result_event_id=str(event["event_id"]),
                    tool_call_id=str(event["tool_call_id"]),
                    result_plan_version=event_plan_version,
                    task_event_seq=_int_field(event, "task_event_seq"),
                ),
            )
        self._advance_task_event_seq(task, event)

    def _handle_tool_result_marked_stale(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        current_plan_version = _int_field(event, "current_plan_version")
        result_plan_version = _int_field(event, "result_plan_version")
        if current_plan_version != task.current_plan_version:
            raise SlowTaskStateError("TOOL_RESULT_MARKED_STALE current_plan_version must match SlowTask state")
        if result_plan_version >= current_plan_version:
            raise SlowTaskStateError("TOOL_RESULT_MARKED_STALE must refer to an older result_plan_version")
        source_tool_result_event_id = _pending_stale_source_for_mark(
            task,
            tool_call_id=str(event["tool_call_id"]),
            result_plan_version=result_plan_version,
        )
        task.pending_stale_tool_results = _ensure_pending_stale_tool_result(
            task.pending_stale_tool_results,
            task.tool_results,
            source_tool_result_event_id=source_tool_result_event_id,
        )
        task.stale_marks = (
            *task.stale_marks,
            StaleMark(
                event_id=str(event["event_id"]),
                tool_call_id=str(event["tool_call_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                result_plan_version=result_plan_version,
                current_plan_version=current_plan_version,
                stale_reason=str(event["stale_reason"]),
            ),
        )
        task.pending_stale_tool_results = _mark_pending_stale_tool_result(
            task.pending_stale_tool_results,
            source_tool_result_event_id=source_tool_result_event_id,
            marked_stale_event_id=str(event["event_id"]),
        )
        self._advance_task_event_seq(task, event)

    def _handle_stale_evidence_recorded(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        source_tool_result_event_id = str(event["source_tool_result_event_id"])
        if not _pending_stale_tool_result_is_marked(
            task.pending_stale_tool_results,
            source_tool_result_event_id=source_tool_result_event_id,
        ):
            raise SlowTaskStateError(
                "STALE_EVIDENCE_RECORDED requires a prior TOOL_RESULT_MARKED_STALE for "
                f"{source_tool_result_event_id}"
            )
        stale_evidence_ref = str(event["stale_evidence_ref"])
        task.stale_evidence_refs = _append_unique(task.stale_evidence_refs, stale_evidence_ref)
        task.pending_stale_tool_results = _record_pending_stale_evidence(
            task.pending_stale_tool_results,
            source_tool_result_event_id=source_tool_result_event_id,
            stale_evidence_ref=stale_evidence_ref,
            stale_evidence_recorded_event_id=str(event["event_id"]),
        )
        self._advance_task_event_seq(task, event)

    def _handle_stale_evidence_adopted(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        stale_evidence_ref = str(event["stale_evidence_ref"])
        source_tool_result_event_id = str(event["source_tool_result_event_id"])
        adopted_from_plan_version = _int_field(event, "adopted_from_plan_version")
        if not _has_recorded_stale_evidence(
            task,
            source_tool_result_event_id=source_tool_result_event_id,
            stale_evidence_ref=stale_evidence_ref,
            adopted_from_plan_version=adopted_from_plan_version,
        ):
            raise SlowTaskStateError(
                "STALE_EVIDENCE_ADOPTED requires recorded stale evidence from the source tool result"
            )
        task.adopted_evidence = (
            *task.adopted_evidence,
            AdoptedEvidence(
                event_id=str(event["event_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                stale_evidence_ref=stale_evidence_ref,
                source_tool_result_event_id=source_tool_result_event_id,
                adopted_from_plan_version=adopted_from_plan_version,
                adoption_mode=str(event["adoption_mode"]),
                adoption_reason=str(event["adoption_reason"]),
                adopted_scope=_string_tuple(event.get("adopted_scope", ())),
                adopted_by_event_id=str(event["adopted_by_event_id"]),
            ),
        )
        self._advance_task_event_seq(task, event)

    def _handle_semantic_commitment(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        self._require_current_plan(event, task)
        source_events = _string_tuple(event.get("source_events", ()))
        _require_stale_evidence_adopted_for_advancement(
            task,
            event,
            source_event_ids=source_events,
        )
        task.semantic_commitments = (
            *task.semantic_commitments,
            SemanticCommitment(
                event_id=str(event["event_id"]),
                commitment_id=str(event["commitment_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                source_events=source_events,
                commitment_ref=_optional_str(event.get("commitment_ref")),
            ),
        )
        self._advance_task_event_seq(task, event)

    def _record_late_event(self, task: SlowTaskRecord, event: Mapping[str, Any]) -> None:
        self._advance_task_event_seq(task, event)
        task.late_events = (
            *task.late_events,
            LateEvent(
                event_id=str(event["event_id"]),
                event_name=str(event["event_name"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                reason=f"terminal_{task.lifecycle_state.lower()}",
            ),
        )

    def _task_for_event(self, event: Mapping[str, Any]) -> SlowTaskRecord:
        task_id = str(event["task_id"])
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise SlowTaskStateError(f"{event['event_name']} references unknown task_id={task_id}") from exc

    def _require_current_plan(self, event: Mapping[str, Any], task: SlowTaskRecord) -> None:
        plan_version = _int_field(event, "plan_version")
        if plan_version != task.current_plan_version:
            raise SlowTaskStateError(
                f"{event['event_name']} plan_version={plan_version} does not match current "
                f"plan_version={task.current_plan_version}"
            )

    def _advance_task_event_seq(self, task: SlowTaskRecord, event: Mapping[str, Any]) -> None:
        task_event_seq = _int_field(event, "task_event_seq")
        if task_event_seq <= task.current_task_event_seq:
            raise SlowTaskStateError(
                f"{event['event_name']} task_event_seq={task_event_seq} must be greater than current "
                f"task_event_seq={task.current_task_event_seq}"
            )
        task.current_task_event_seq = task_event_seq

    def _require_next_task_event_seq(self, task: SlowTaskRecord, event: Mapping[str, Any]) -> None:
        task_event_seq = _int_field(event, "task_event_seq")
        if task_event_seq <= task.current_task_event_seq:
            raise SlowTaskStateError(
                f"{event['event_name']} task_event_seq={task_event_seq} must be greater than current "
                f"task_event_seq={task.current_task_event_seq}"
            )


def _ref_event(
    event: Mapping[str, Any],
    *,
    refs: tuple[str, ...] = (),
    reason: str | None = None,
) -> RefEvent:
    return RefEvent(
        event_id=str(event["event_id"]),
        event_name=str(event["event_name"]),
        plan_version=_int_field(event, "plan_version"),
        task_event_seq=_int_field(event, "task_event_seq"),
        refs=refs,
        reason=reason,
    )


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    if value in values:
        return values
    return (*values, value)


def _append_many_unique(values: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    updated = values
    for addition in additions:
        updated = _append_unique(updated, addition)
    return updated


def _has_interpreted_user_patch(task: SlowTaskRecord, *, patch_id: str) -> bool:
    return any(
        interpretation.patch_id == patch_id
        and interpretation.plan_version == task.current_plan_version
        for interpretation in task.user_patch_interpretations
    )


def _has_received_user_patch_evidence(task: SlowTaskRecord, *, patch_id: str) -> bool:
    return any(
        evidence.patch_id == patch_id
        and evidence.plan_version == task.current_plan_version
        for evidence in task.user_patch_evidence
    )


def _has_material_user_patch_interpretation_for_event(
    task: SlowTaskRecord,
    *,
    user_patch_event_id: str,
    plan_version: int,
) -> bool:
    patch_ids = {
        evidence.patch_id
        for evidence in task.user_patch_evidence
        if evidence.event_id == user_patch_event_id and evidence.plan_version == plan_version
    }
    if not patch_ids:
        return False
    return any(
        interpretation.patch_id in patch_ids
        and interpretation.caused_by_event_id == user_patch_event_id
        and interpretation.plan_version == plan_version
        and interpretation.materially_changes_task
        for interpretation in task.user_patch_interpretations
    )


def _is_material_user_patch_planning_reason(planning_reason: str) -> bool:
    return planning_reason == "material_user_patch" or planning_reason.startswith("material_user_patch:")


def _has_matching_tool_call(task: SlowTaskRecord, *, tool_call_id: str, plan_version: int) -> bool:
    return any(
        tool_call.tool_call_id == tool_call_id
        and tool_call.plan_version == plan_version
        for tool_call in task.tool_calls
    )


def _has_matching_tool_execution_start(
    task: SlowTaskRecord,
    *,
    tool_call_id: str,
    plan_version: int,
) -> bool:
    return any(
        tool_call.source_event_name == "TOOL_EXECUTION_STARTED"
        and tool_call.tool_call_id == tool_call_id
        and tool_call.plan_version == plan_version
        for tool_call in task.tool_calls
    )


def _has_recorded_stale_evidence(
    task: SlowTaskRecord,
    *,
    source_tool_result_event_id: str,
    stale_evidence_ref: str,
    adopted_from_plan_version: int,
) -> bool:
    return any(
        pending.source_tool_result_event_id == source_tool_result_event_id
        and pending.result_plan_version == adopted_from_plan_version
        and pending.stale_evidence_ref == stale_evidence_ref
        for pending in task.pending_stale_tool_results
    )


def _require_stale_evidence_adopted_for_advancement(
    task: SlowTaskRecord,
    event: Mapping[str, Any],
    *,
    refs: tuple[str, ...] = (),
    source_event_ids: tuple[str, ...] = (),
) -> None:
    unadopted_stale_refs = _unadopted_stale_evidence_refs(task, refs)
    unadopted_old_result_refs = _unadopted_old_tool_result_refs(task, refs)
    unadopted_event_ids = _unadopted_stale_dependency_event_ids(
        task,
        (*refs, *source_event_ids, *_optional_ref_tuple(event.get("caused_by_event_id"))),
    )
    blocked_refs = sorted(unadopted_stale_refs | unadopted_old_result_refs)
    blocked_event_ids = sorted(unadopted_event_ids)
    if blocked_refs or blocked_event_ids:
        raise SlowTaskStateError(
            f"{event['event_name']} requires STALE_EVIDENCE_ADOPTED before stale evidence "
            "can advance current-plan reasoning: "
            f"refs={blocked_refs}, source_events={blocked_event_ids}"
        )


def _unadopted_stale_evidence_refs(task: SlowTaskRecord, refs: tuple[str, ...]) -> set[str]:
    stale_refs = set(task.stale_evidence_refs).intersection(refs)
    if not stale_refs:
        return set()
    adopted_refs = {
        adopted.stale_evidence_ref
        for adopted in task.adopted_evidence
        if adopted.plan_version == task.current_plan_version
    }
    return stale_refs - adopted_refs


def _unadopted_old_tool_result_refs(task: SlowTaskRecord, refs: tuple[str, ...]) -> set[str]:
    ref_set = set(refs)
    if not ref_set:
        return set()
    adopted_source_event_ids = {
        adopted.source_tool_result_event_id
        for adopted in task.adopted_evidence
        if adopted.plan_version == task.current_plan_version
    }
    return {
        result.result_ref
        for result in task.tool_results
        if result.plan_version < task.current_plan_version
        and result.result_ref in ref_set
        and result.event_id not in adopted_source_event_ids
    }


def _unadopted_stale_dependency_event_ids(
    task: SlowTaskRecord,
    event_ids: tuple[str, ...],
) -> set[str]:
    event_id_set = set(event_ids)
    if not event_id_set:
        return set()
    adopted_source_event_ids = {
        adopted.source_tool_result_event_id
        for adopted in task.adopted_evidence
        if adopted.plan_version == task.current_plan_version
    }
    adoption_event_ids = {
        adopted.event_id
        for adopted in task.adopted_evidence
        if adopted.plan_version == task.current_plan_version
    }
    blocked_ids = {
        result.event_id
        for result in task.tool_results
        if result.plan_version < task.current_plan_version
        and result.event_id in event_id_set
        and result.event_id not in adopted_source_event_ids
    }
    for pending in task.pending_stale_tool_results:
        if pending.source_tool_result_event_id in adopted_source_event_ids:
            continue
        stale_dependency_ids = {
            pending.source_tool_result_event_id,
            *_optional_ref_tuple(pending.marked_stale_event_id),
            *_optional_ref_tuple(pending.stale_evidence_recorded_event_id),
        }
        blocked_ids.update(event_id_set.intersection(stale_dependency_ids))
    return blocked_ids - adoption_event_ids


def _pending_stale_source_for_mark(
    task: SlowTaskRecord,
    *,
    tool_call_id: str,
    result_plan_version: int,
) -> str:
    matches = [
        pending.source_tool_result_event_id
        for pending in task.pending_stale_tool_results
        if pending.tool_call_id == tool_call_id
        and pending.result_plan_version == result_plan_version
        and pending.stale_evidence_ref is None
    ]
    if matches:
        return matches[0]
    matches = [
        result.event_id
        for result in task.tool_results
        if result.tool_call_id == tool_call_id
        and result.plan_version == result_plan_version
        and result.plan_version < task.current_plan_version
        and not _has_pending_stale_tool_result(
            task.pending_stale_tool_results,
            source_tool_result_event_id=result.event_id,
        )
    ]
    if not matches:
        raise SlowTaskStateError(
            "TOOL_RESULT_MARKED_STALE requires a prior old-plan TOOL_RESULT_RECEIVED"
        )
    return matches[0]


def _has_pending_stale_tool_result(
    pending_results: tuple[PendingStaleToolResult, ...],
    *,
    source_tool_result_event_id: str,
) -> bool:
    return any(
        pending.source_tool_result_event_id == source_tool_result_event_id
        for pending in pending_results
    )


def _ensure_pending_stale_tool_result(
    pending_results: tuple[PendingStaleToolResult, ...],
    tool_results: tuple[ToolResultMetadata, ...],
    *,
    source_tool_result_event_id: str,
) -> tuple[PendingStaleToolResult, ...]:
    if _has_pending_stale_tool_result(
        pending_results,
        source_tool_result_event_id=source_tool_result_event_id,
    ):
        return pending_results
    matches = [
        result
        for result in tool_results
        if result.event_id == source_tool_result_event_id
    ]
    if not matches:
        raise SlowTaskStateError(
            "TOOL_RESULT_MARKED_STALE requires a prior TOOL_RESULT_RECEIVED source"
        )
    result = matches[0]
    return (
        *pending_results,
        PendingStaleToolResult(
            source_tool_result_event_id=result.event_id,
            tool_call_id=result.tool_call_id,
            result_plan_version=result.plan_version,
            task_event_seq=result.task_event_seq,
        ),
    )


def _mark_pending_stale_tool_result(
    pending_results: tuple[PendingStaleToolResult, ...],
    *,
    source_tool_result_event_id: str,
    marked_stale_event_id: str,
) -> tuple[PendingStaleToolResult, ...]:
    return tuple(
        PendingStaleToolResult(
            source_tool_result_event_id=pending.source_tool_result_event_id,
            tool_call_id=pending.tool_call_id,
            result_plan_version=pending.result_plan_version,
            task_event_seq=pending.task_event_seq,
            marked_stale_event_id=marked_stale_event_id
            if pending.source_tool_result_event_id == source_tool_result_event_id
            else pending.marked_stale_event_id,
            stale_evidence_ref=pending.stale_evidence_ref,
            stale_evidence_recorded_event_id=pending.stale_evidence_recorded_event_id,
        )
        for pending in pending_results
    )


def _pending_stale_tool_result_is_marked(
    pending_results: tuple[PendingStaleToolResult, ...],
    *,
    source_tool_result_event_id: str,
) -> bool:
    return any(
        pending.source_tool_result_event_id == source_tool_result_event_id
        and pending.marked_stale_event_id is not None
        and pending.stale_evidence_ref is None
        for pending in pending_results
    )


def _record_pending_stale_evidence(
    pending_results: tuple[PendingStaleToolResult, ...],
    *,
    source_tool_result_event_id: str,
    stale_evidence_ref: str,
    stale_evidence_recorded_event_id: str,
) -> tuple[PendingStaleToolResult, ...]:
    return tuple(
        PendingStaleToolResult(
            source_tool_result_event_id=pending.source_tool_result_event_id,
            tool_call_id=pending.tool_call_id,
            result_plan_version=pending.result_plan_version,
            task_event_seq=pending.task_event_seq,
            marked_stale_event_id=pending.marked_stale_event_id,
            stale_evidence_ref=stale_evidence_ref
            if pending.source_tool_result_event_id == source_tool_result_event_id
            else pending.stale_evidence_ref,
            stale_evidence_recorded_event_id=stale_evidence_recorded_event_id
            if pending.source_tool_result_event_id == source_tool_result_event_id
            else pending.stale_evidence_recorded_event_id,
        )
        for pending in pending_results
    )


def _int_field(event: Mapping[str, Any], field: str) -> int:
    value = event[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise SlowTaskStateError(f"{field} must be an integer")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (str(value),)
    if not isinstance(value, (list, tuple)):
        raise SlowTaskStateError("expected a list of string refs")
    return tuple(str(item) for item in value)


def _optional_ref_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    return (str(value),)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
