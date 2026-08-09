from __future__ import annotations

from dataclasses import dataclass, field


class EventRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class EventDefinition:
    event_name: str
    required_fields: tuple[str, ...]
    one_of_fields: tuple[tuple[str, ...], ...] = ()
    literal_fields: dict[str, object] = field(default_factory=dict)
    is_root: bool = False
    caused_by_event_required: bool = True


def _definition(
    event_name: str,
    *,
    required_fields: tuple[str, ...] = (),
    one_of_fields: tuple[tuple[str, ...], ...] = (),
    literal_fields: dict[str, object] | None = None,
    is_root: bool = False,
    caused_by_event_required: bool = True,
) -> EventDefinition:
    return EventDefinition(
        event_name=event_name,
        required_fields=required_fields,
        one_of_fields=one_of_fields,
        literal_fields=literal_fields or {},
        is_root=is_root,
        caused_by_event_required=caused_by_event_required,
    )


MVP0_EVENT_DEFINITIONS: dict[str, EventDefinition] = {
    "SESSION_STARTED": _definition(
        "SESSION_STARTED",
        required_fields=("session_id", "conversation_id", "runtime_config_ref", "capability_snapshot_ref"),
        is_root=True,
    ),
    "ADAPTER_CAPABILITY_SNAPSHOT_RECORDED": _definition(
        "ADAPTER_CAPABILITY_SNAPSHOT_RECORDED",
        required_fields=(
            "capability_snapshot_ref",
            "adapter_ids",
            "adapter_types",
            "deployment_modes",
            "output_modes",
        ),
    ),
    "ADAPTER_HEALTHCHECK_FAILED": _definition(
        "ADAPTER_HEALTHCHECK_FAILED",
        required_fields=("adapter_id", "adapter_type", "health_status", "failure_reason", "output_mode"),
    ),
    "ADAPTER_REQUEST_RETRYING": _definition(
        "ADAPTER_REQUEST_RETRYING",
        required_fields=("adapter_id", "adapter_type", "adapter_request_id", "retry_count", "retry_reason"),
    ),
    "ADAPTER_REQUEST_FAILED": _definition(
        "ADAPTER_REQUEST_FAILED",
        required_fields=(
            "adapter_id",
            "adapter_type",
            "adapter_request_id",
            "failure_reason",
            "retryable",
            "output_mode",
        ),
    ),
    "ADAPTER_OUTPUT_VALIDATION_FAILED": _definition(
        "ADAPTER_OUTPUT_VALIDATION_FAILED",
        required_fields=(
            "adapter_id",
            "adapter_type",
            "adapter_request_id",
            "schema_name",
            "failure_reasons",
            "output_mode",
        ),
    ),
    "ADAPTER_OUTPUT_DEGRADED": _definition(
        "ADAPTER_OUTPUT_DEGRADED",
        required_fields=("adapter_id", "adapter_type", "degraded_reason", "output_mode"),
    ),
    "ASR_TRANSCRIPT_OUTPUT_EMITTED": _definition(
        "ASR_TRANSCRIPT_OUTPUT_EMITTED",
        required_fields=(
            "adapter_id",
            "adapter_type",
            "adapter_request_id",
            "turn_id",
            "utterance_id",
            "input_modality",
            "audio_span_id",
            "asr_frame_ref",
            "text_ref",
            "transcript_finality",
            "timestamp_status",
            "streaming_status",
            "output_mode",
        ),
        literal_fields={
            "adapter_type": "asr",
            "input_modality": "audio",
            "transcript_finality": "final",
        },
    ),
    "THINKER_SEMANTIC_FRAME_OUTPUT_EMITTED": _definition(
        "THINKER_SEMANTIC_FRAME_OUTPUT_EMITTED",
        required_fields=(
            "adapter_id",
            "adapter_type",
            "adapter_request_id",
            "turn_id",
            "utterance_id",
            "input_modality",
            "semantic_frame_schema",
            "normalization_status",
            "semantic_frame_ref",
            "semantic_summary_ref",
            "semantic_close_status",
            "assistant_directedness_status",
            "emotion_status",
            "audio_caption_status",
            "output_mode",
        ),
        literal_fields={
            "adapter_type": "thinker",
            "semantic_frame_schema": "voice_agent.semantic_frame.v1",
            "normalization_status": "normalized",
        },
    ),
    "SLOW_LLM_STRUCTURED_OUTPUT_EMITTED": _definition(
        "SLOW_LLM_STRUCTURED_OUTPUT_EMITTED",
        required_fields=(
            "adapter_id",
            "adapter_type",
            "adapter_request_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "schema_name",
            "normalization_status",
            "slow_llm_output_ref",
            "structured_output_ref",
            "validation_result_ref",
            "output_mode",
        ),
        literal_fields={
            "adapter_type": "slow_llm",
            "normalization_status": "normalized",
        },
    ),
    "TTS_SYNTHESIS_OUTPUT_EMITTED": _definition(
        "TTS_SYNTHESIS_OUTPUT_EMITTED",
        required_fields=(
            "adapter_id",
            "adapter_type",
            "adapter_request_id",
            "spoken_plan_id",
            "approved_check_event_id",
            "normalization_status",
            "audio_format_ref",
            "synthesis_result_ref",
            "truncate_status",
            "output_mode",
        ),
        one_of_fields=(("audio_ref", "tts_stream_ref"),),
        literal_fields={
            "adapter_type": "tts",
            "normalization_status": "normalized",
        },
    ),
    "TEXT_INPUT_RECEIVED": _definition(
        "TEXT_INPUT_RECEIVED",
        required_fields=(
            "input_span_id",
            "text_span_id",
            "directedness",
            "semantic_close",
        ),
        one_of_fields=(("redacted_text", "text_ref"),),
        literal_fields={
            "input_modality": "text",
            "directedness": "ASSUMED_DIRECTED",
            "semantic_close": "ASSUMED_CLOSED",
        },
    ),
    "LOW_CONFIDENCE_INGRESS": _definition(
        "LOW_CONFIDENCE_INGRESS",
        required_fields=("confidence_fields", "ingress_reason"),
        one_of_fields=(("input_span_id", "audio_span_id"),),
    ),
    "AUDIO_SPAN_STARTED": _definition(
        "AUDIO_SPAN_STARTED",
        required_fields=("audio_span_id", "audio_sample_offset", "audio_format_ref"),
        literal_fields={"input_modality": "audio"},
    ),
    "AUDIO_SPAN_ENDED": _definition(
        "AUDIO_SPAN_ENDED",
        required_fields=("audio_span_id", "audio_sample_offset", "duration_ms", "end_reason"),
    ),
    "SPEECH_START_DETECTED": _definition(
        "SPEECH_START_DETECTED",
        required_fields=("audio_span_id", "audio_sample_offset", "vad_confidence"),
    ),
    "SPEECH_END_DETECTED": _definition(
        "SPEECH_END_DETECTED",
        required_fields=("audio_span_id", "audio_sample_offset", "vad_confidence", "silence_duration_ms"),
    ),
    "BARGE_IN_CANDIDATE": _definition(
        "BARGE_IN_CANDIDATE",
        required_fields=(
            "audio_span_id",
            "playback_span_id",
            "playback_offset_ms",
            "echo_likelihood",
            "vad_confidence",
            "barge_in_confidence",
        ),
    ),
    "DIRECTEDNESS_CANDIDATE": _definition(
        "DIRECTEDNESS_CANDIDATE",
        required_fields=("audio_span_id", "directedness", "directedness_confidence"),
    ),
    "SEMANTIC_CLOSE_CANDIDATE": _definition(
        "SEMANTIC_CLOSE_CANDIDATE",
        required_fields=("audio_span_id", "semantic_close", "semantic_close_confidence"),
    ),
    "NON_ASSISTANT_CANDIDATE": _definition(
        "NON_ASSISTANT_CANDIDATE",
        required_fields=("audio_span_id", "directedness_confidence"),
        literal_fields={"directedness": "NOT_DIRECTED"},
    ),
    "TURN_OPENED": _definition(
        "TURN_OPENED",
        required_fields=("turn_id", "turn_phase", "input_modality"),
        one_of_fields=(("input_span_id", "audio_span_id"),),
        literal_fields={"turn_phase": "COLLECTING_INPUT"},
    ),
    "TURN_HELD": _definition(
        "TURN_HELD",
        required_fields=("turn_id", "semantic_close", "directedness"),
        literal_fields={"ingress_outcome": "HELD"},
    ),
    "TURN_INGRESS_ACCEPTED": _definition(
        "TURN_INGRESS_ACCEPTED",
        required_fields=("turn_id", "ingress_outcome"),
        one_of_fields=(("input_span_id", "audio_span_id"),),
        literal_fields={"ingress_outcome": "ACCEPTED"},
    ),
    "TURN_INGRESS_REJECTED": _definition(
        "TURN_INGRESS_REJECTED",
        required_fields=("turn_id", "reject_reason"),
        one_of_fields=(("input_span_id", "audio_span_id"),),
        literal_fields={"ingress_outcome": "REJECTED"},
    ),
    "TURN_INGRESS_COMMITTED": _definition(
        "TURN_INGRESS_COMMITTED",
        required_fields=(
            "turn_id",
            "utterance_id",
            "input_modality",
            "directedness",
            "semantic_close",
            "ingress_outcome",
        ),
        one_of_fields=(("input_span_id", "text_span_id", "audio_span_id"),),
        literal_fields={"ingress_outcome": "COMMITTED"},
    ),
    "INTERRUPT_CANDIDATE": _definition(
        "INTERRUPT_CANDIDATE",
        required_fields=("playback_span_id", "playback_offset_ms", "policy_reason", "confidence_summary"),
    ),
    "ROUTER_DECISION_EMITTED": _definition(
        "ROUTER_DECISION_EMITTED",
        required_fields=("turn_id", "utterance_id", "router_decision"),
    ),
    "PLAYBACK_SPAN_STARTED": _definition(
        "PLAYBACK_SPAN_STARTED",
        required_fields=("playback_span_id",),
        one_of_fields=(("audio_ref", "tts_stream_ref"),),
    ),
    "PLAYBACK_PROGRESS": _definition(
        "PLAYBACK_PROGRESS",
        required_fields=("playback_span_id", "playback_offset_ms"),
    ),
    "PLAYBACK_COMMITTED": _definition(
        "PLAYBACK_COMMITTED",
        required_fields=("playback_span_id", "playback_offset_ms", "commit_basis"),
    ),
    "PLAYBACK_FINISHED": _definition(
        "PLAYBACK_FINISHED",
        required_fields=("playback_span_id", "final_playback_offset_ms"),
    ),
    "TTS_TRUNCATE_REQUESTED": _definition(
        "TTS_TRUNCATE_REQUESTED",
        required_fields=("playback_span_id", "cutoff_playback_offset_ms", "interrupt_candidate_event_id"),
    ),
    "TTS_TRUNCATED": _definition(
        "TTS_TRUNCATED",
        required_fields=("playback_span_id", "actual_stop_offset_ms", "truncate_request_event_id"),
    ),
    "MOCK_ASR_FRAME_EMITTED": _definition(
        "MOCK_ASR_FRAME_EMITTED",
        required_fields=("turn_id", "utterance_id", "input_modality", "asr_frame_ref"),
        literal_fields={"output_mode": "mock"},
    ),
    "MOCK_THINKER_FRAME_EMITTED": _definition(
        "MOCK_THINKER_FRAME_EMITTED",
        required_fields=("turn_id", "utterance_id", "semantic_frame_ref"),
        literal_fields={"output_mode": "mock"},
    ),
    "REPLAY_STARTED": _definition(
        "REPLAY_STARTED",
        required_fields=("replay_id", "source_trace_ref", "replay_mode"),
        caused_by_event_required=False,
    ),
    "REPLAY_COMPLETED": _definition(
        "REPLAY_COMPLETED",
        required_fields=("replay_id", "result_status", "state_digest"),
    ),
    "TRACE_WRITE_DEGRADED": _definition(
        "TRACE_WRITE_DEGRADED",
        required_fields=("storage_target", "degraded_reason"),
    ),
    "TRACE_SECRET_REDACTION_APPLIED": _definition(
        "TRACE_SECRET_REDACTION_APPLIED",
        required_fields=("redaction_reason", "redacted_fields"),
        one_of_fields=(("caused_by_event_id", "payload_ref"),),
        caused_by_event_required=False,
    ),
    "TRACE_WRITE_BLOCKED_SECRET_DETECTED": _definition(
        "TRACE_WRITE_BLOCKED_SECRET_DETECTED",
        required_fields=("source_module", "blocked_payload_ref", "secret_kind", "blocking_reason"),
        caused_by_event_required=False,
    ),
}

MVP0_EVENT_NAMES = frozenset(MVP0_EVENT_DEFINITIONS)

MVP1_EVENT_DEFINITIONS: dict[str, EventDefinition] = {
    "TASK_FOCUS_STATE_UPDATED": _definition(
        "TASK_FOCUS_STATE_UPDATED",
        required_fields=(
            "foreground_mode",
            "side_conversation_allowed",
            "default_patch_policy",
            "ambiguous_input_policy",
            "last_focus_decision",
            "last_focus_confidence",
            "router_decision_event_id",
        ),
    ),
    "SLOWTASK_CREATED": _definition(
        "SLOWTASK_CREATED",
        required_fields=("task_id", "plan_version", "task_event_seq", "initial_goal_ref"),
    ),
    "SLOWTASK_STATE_CHANGED": _definition(
        "SLOWTASK_STATE_CHANGED",
        required_fields=("task_id", "plan_version", "task_event_seq", "from_state", "to_state", "reason"),
    ),
    "USER_PATCH_RECEIVED": _definition(
        "USER_PATCH_RECEIVED",
        required_fields=(
            "patch_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "observed_plan_version",
            "evidence_ref",
        ),
    ),
    "USER_PATCH_INTERPRETED": _definition(
        "USER_PATCH_INTERPRETED",
        required_fields=(
            "patch_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "observed_plan_version",
            "interpreted_against_plan_version",
            "interpretation_type",
            "materially_changes_task",
        ),
    ),
    "PLAN_VERSION_ADVANCED": _definition(
        "PLAN_VERSION_ADVANCED",
        required_fields=(
            "task_id",
            "plan_version",
            "task_event_seq",
            "from_plan_version",
            "to_plan_version",
            "planning_reason",
        ),
    ),
    "TASK_REPLANNED": _definition(
        "TASK_REPLANNED",
        required_fields=("task_id", "plan_version", "task_event_seq", "planning_reason"),
    ),
    "TASK_REQUIREMENT_MODEL_ACCEPTED": _definition(
        "TASK_REQUIREMENT_MODEL_ACCEPTED",
        required_fields=(
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
    ),
    "TASK_REQUIREMENT_MODEL_INVALIDATED": _definition(
        "TASK_REQUIREMENT_MODEL_INVALIDATED",
        required_fields=(
            "task_id",
            "plan_version",
            "task_event_seq",
            "model_ref",
            "invalidation_reason",
        ),
    ),
    "EVIDENCE_REVIEWED": _definition(
        "EVIDENCE_REVIEWED",
        required_fields=("task_id", "plan_version", "task_event_seq", "evidence_refs", "review_result"),
    ),
    "AMBIGUITY_DETECTED": _definition(
        "AMBIGUITY_DETECTED",
        required_fields=(
            "task_id",
            "plan_version",
            "task_event_seq",
            "ambiguous_fields",
            "source_evidence_refs",
        ),
    ),
    "AMBIGUITY_RESOLVED": _definition(
        "AMBIGUITY_RESOLVED",
        required_fields=(
            "task_id",
            "plan_version",
            "task_event_seq",
            "resolved_fields",
            "resolution_reason",
            "source_evidence_refs",
        ),
    ),
    "CLARIFICATION_REQUESTED": _definition(
        "CLARIFICATION_REQUESTED",
        required_fields=(
            "task_id",
            "plan_version",
            "task_event_seq",
            "missing_or_ambiguous_fields",
            "clarification_prompt_ref",
        ),
    ),
    "ARGUMENTS_RESOLVED": _definition(
        "ARGUMENTS_RESOLVED",
        required_fields=(
            "task_id",
            "plan_version",
            "task_event_seq",
            "resolved_arguments_ref",
            "provenance_ref",
        ),
    ),
    "ARGUMENT_RESOLUTION_PROVENANCE": _definition(
        "ARGUMENT_RESOLUTION_PROVENANCE",
        required_fields=("task_id", "plan_version", "task_event_seq", "field_provenance_refs"),
    ),
    "INSUFFICIENT_EVIDENCE_FOR_ACTION": _definition(
        "INSUFFICIENT_EVIDENCE_FOR_ACTION",
        required_fields=(
            "task_id",
            "plan_version",
            "task_event_seq",
            "blocking_fields",
            "source_evidence_refs",
        ),
    ),
    "PLANNING_STARTED": _definition(
        "PLANNING_STARTED",
        required_fields=("task_id", "plan_version", "task_event_seq", "planning_reason"),
    ),
    "PLANNING_RESTARTED": _definition(
        "PLANNING_RESTARTED",
        required_fields=("task_id", "plan_version", "task_event_seq", "restart_reason"),
    ),
    "WAITING_FOR_SLOT": _definition(
        "WAITING_FOR_SLOT",
        required_fields=("task_id", "plan_version", "task_event_seq", "missing_fields"),
    ),
    "WAITING_FOR_USER_CONFIRMATION": _definition(
        "WAITING_FOR_USER_CONFIRMATION",
        required_fields=("task_id", "plan_version", "task_event_seq", "confirmation_id"),
    ),
    "FINALIZING": _definition(
        "FINALIZING",
        required_fields=("task_id", "plan_version", "task_event_seq", "source_events"),
    ),
    "SLOWTASK_DEGRADED": _definition(
        "SLOWTASK_DEGRADED",
        required_fields=("task_id", "plan_version", "task_event_seq", "degraded_reason"),
    ),
    "SLOWTASK_FAILED": _definition(
        "SLOWTASK_FAILED",
        required_fields=("task_id", "plan_version", "task_event_seq", "failure_reason"),
    ),
    "CONFIRMATION_REQUIRED": _definition(
        "CONFIRMATION_REQUIRED",
        required_fields=(
            "confirmation_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "confirmation_scope",
            "required_for_event_id",
            "prompt_ref",
        ),
    ),
    "USER_CONFIRMATION_RECEIVED": _definition(
        "USER_CONFIRMATION_RECEIVED",
        required_fields=(
            "confirmation_id",
            "patch_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "confirmation_signal",
        ),
    ),
    "CONFIRMATION_ACCEPTED": _definition(
        "CONFIRMATION_ACCEPTED",
        required_fields=(
            "confirmation_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "accepted_scope",
            "authorization_ref",
        ),
    ),
    "CONFIRMATION_REJECTED": _definition(
        "CONFIRMATION_REJECTED",
        required_fields=("confirmation_id", "task_id", "plan_version", "task_event_seq", "rejection_reason"),
    ),
    "SLOWTASK_CANCEL_REQUESTED": _definition(
        "SLOWTASK_CANCEL_REQUESTED",
        required_fields=("task_id", "plan_version", "task_event_seq", "cancel_reason"),
    ),
    "SLOWTASK_CANCELLED": _definition(
        "SLOWTASK_CANCELLED",
        required_fields=("task_id", "plan_version", "task_event_seq", "cancel_reason", "inflight_tool_policy"),
    ),
    "TOOL_CALL_STARTED": _definition(
        "TOOL_CALL_STARTED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "tool_name",
            "idempotency_key",
        ),
    ),
    "TOOL_RESULT_RECEIVED": _definition(
        "TOOL_RESULT_RECEIVED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "result_status",
            "result_ref",
        ),
    ),
    "TOOL_RESULT_MARKED_STALE": _definition(
        "TOOL_RESULT_MARKED_STALE",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "result_plan_version",
            "current_plan_version",
            "stale_reason",
        ),
    ),
    "STALE_EVIDENCE_RECORDED": _definition(
        "STALE_EVIDENCE_RECORDED",
        required_fields=(
            "task_id",
            "plan_version",
            "task_event_seq",
            "stale_evidence_ref",
            "source_tool_result_event_id",
        ),
    ),
    "STALE_EVIDENCE_ADOPTED": _definition(
        "STALE_EVIDENCE_ADOPTED",
        required_fields=(
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
        literal_fields={"adoption_mode": "adopt_or_rebase"},
    ),
    "SEMANTIC_COMMITMENT_EMITTED": _definition(
        "SEMANTIC_COMMITMENT_EMITTED",
        required_fields=("commitment_id", "task_id", "plan_version", "task_event_seq", "source_events"),
    ),
}

MVP1_EVENT_NAMES = frozenset(MVP1_EVENT_DEFINITIONS)
MVP2_EVENT_DEFINITIONS: dict[str, EventDefinition] = {
    "TOOL_MANIFEST_LOADED": _definition(
        "TOOL_MANIFEST_LOADED",
        required_fields=("tool_name", "tool_adapter_id", "tool_manifest_version", "side_effect_class"),
    ),
    "TOOL_ARGUMENTS_PARTIAL": _definition(
        "TOOL_ARGUMENTS_PARTIAL",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "partial_arguments_ref",
            "missing_fields",
        ),
    ),
    "TOOL_ARGUMENTS_READY": _definition(
        "TOOL_ARGUMENTS_READY",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "resolved_arguments_ref",
            "provenance_ref",
        ),
    ),
    "TOOL_PREVIEW_AVAILABLE": _definition(
        "TOOL_PREVIEW_AVAILABLE",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "preview_ref",
            "requires_confirmation",
        ),
    ),
    "TOOL_EXECUTION_AUTHORIZED": _definition(
        "TOOL_EXECUTION_AUTHORIZED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "authorization_basis",
        ),
    ),
    "TOOL_EXECUTION_STARTED": _definition(
        "TOOL_EXECUTION_STARTED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "idempotency_key",
        ),
    ),
    "WAITING_FOR_TOOL": _definition(
        "WAITING_FOR_TOOL",
        required_fields=("task_id", "plan_version", "task_event_seq", "tool_call_id"),
    ),
    "TOOL_PROGRESS_UPDATED": _definition(
        "TOOL_PROGRESS_UPDATED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "progress_type",
            "progress_ref",
        ),
    ),
    "TOOL_UI_STATE_PATCHED": _definition(
        "TOOL_UI_STATE_PATCHED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "ui_patch_id",
            "idempotency_key",
            "patch_ref",
        ),
    ),
    "TOOL_EXECUTION_FAILED": _definition(
        "TOOL_EXECUTION_FAILED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "failure_reason",
            "retryable",
        ),
    ),
    "TOOL_CALL_RETRYING": _definition(
        "TOOL_CALL_RETRYING",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "retry_count",
            "retry_reason",
        ),
    ),
    "TOOL_EXECUTION_CANCEL_REQUESTED": _definition(
        "TOOL_EXECUTION_CANCEL_REQUESTED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "cancel_reason",
        ),
    ),
    "TOOL_EXECUTION_CANCELLED": _definition(
        "TOOL_EXECUTION_CANCELLED",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "cancel_request_event_id",
            "cancel_status",
        ),
    ),
    "TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS": _definition(
        "TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS",
        required_fields=(
            "tool_call_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "blocking_fields",
            "source_event_id",
        ),
    ),
    "SPOKEN_PLAN_EMITTED": _definition(
        "SPOKEN_PLAN_EMITTED",
        required_fields=(
            "spoken_plan_id",
            "task_id",
            "plan_version",
            "task_event_seq",
            "source_events",
            "source_progress_event_ids",
            "coverage_check_required",
            "truthfulness_check_required",
            "text_ref",
            "emotion",
            "speaking_style",
            "interruptible",
            "priority",
            "source",
            "output_mode",
        ),
    ),
    "COMMITMENT_COVERAGE_CHECK_PASSED": _definition(
        "COMMITMENT_COVERAGE_CHECK_PASSED",
        required_fields=(
            "spoken_plan_id",
            "source_commitment_id",
            "checked_fields",
            "check_result_ref",
            "output_mode",
        ),
    ),
    "COMMITMENT_COVERAGE_CHECK_FAILED": _definition(
        "COMMITMENT_COVERAGE_CHECK_FAILED",
        required_fields=(
            "spoken_plan_id",
            "source_commitment_id",
            "failure_reasons",
            "check_result_ref",
            "output_mode",
        ),
    ),
    "PROGRESS_TRUTHFULNESS_CHECK_PASSED": _definition(
        "PROGRESS_TRUTHFULNESS_CHECK_PASSED",
        required_fields=(
            "spoken_plan_id",
            "source_progress_event_ids",
            "truthfulness_level",
            "check_result_ref",
            "output_mode",
        ),
    ),
    "PROGRESS_TRUTHFULNESS_CHECK_FAILED": _definition(
        "PROGRESS_TRUTHFULNESS_CHECK_FAILED",
        required_fields=(
            "spoken_plan_id",
            "source_progress_event_ids",
            "failure_reasons",
            "check_result_ref",
            "output_mode",
        ),
    ),
}
MVP2_EVENT_NAMES = frozenset(MVP2_EVENT_DEFINITIONS) | {
    "TOOL_CALL_STARTED",
    "TOOL_RESULT_RECEIVED",
}
EVENT_DEFINITIONS: dict[str, EventDefinition] = {
    **MVP0_EVENT_DEFINITIONS,
    **MVP1_EVENT_DEFINITIONS,
    **MVP2_EVENT_DEFINITIONS,
}


def get_event_definition(event_name: str) -> EventDefinition:
    try:
        return EVENT_DEFINITIONS[event_name]
    except KeyError as exc:
        raise EventRegistryError(f"Unknown event_name: {event_name}") from exc
