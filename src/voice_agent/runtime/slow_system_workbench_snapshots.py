from __future__ import annotations

"""Public Workbench snapshot and timing projections.

Only these projections cross the Python/React boundary.  Internal reducer
dataclasses and raw event payloads remain inside the control plane.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from voice_agent.runtime.slow_system_workbench_context import TaskContextPack
from voice_agent.state.slowtask_state import SlowTaskRecord, SlowTaskState
from voice_agent.state.task_focus_state import TaskFocusState
from voice_agent.state.tool_execution_state import ToolExecutionState


@dataclass(frozen=True)
class ProviderTraceItem:
    trace_id: str
    sequence: int
    kind: str
    status: str
    provider_mode: str
    output_mode: str
    task_id: str | None
    plan_version: int | None
    task_event_seq: int | None = None
    canonical: bool = False
    created_monotonic_ms: int | None = None
    tool_name: str | None = None
    proposal_only: bool | None = None
    result_present: bool | None = None
    usage: Mapping[str, int] | None = None
    latency_ms: int | None = None
    detail: str | None = None
    phase: str | None = None
    label: str | None = None
    orchestration_role: str | None = None
    subtask_id: str | None = None
    subtask_goal: str | None = None
    public_thought: str | None = None
    tool_input_summary: str | None = None
    tool_output_summary: str | None = None
    next_step: str | None = None
    blocked_on_user: bool | None = None
    role: str | None = None
    proposal_id: str | None = None
    context_hash: str | None = None
    public_summary: str | None = None
    validation_status: str | None = None
    degraded_reason: str | None = None
    next_role: str | None = None
    accepted: bool | None = None
    rejected: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "trace_id": self.trace_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "status": self.status,
            "provider_mode": self.provider_mode,
            "output_mode": self.output_mode,
            "canonical": False,
        }
        for field_name, value in (
            ("task_id", self.task_id),
            ("plan_version", self.plan_version),
            ("task_event_seq", self.task_event_seq),
            ("created_monotonic_ms", self.created_monotonic_ms),
            ("tool_name", self.tool_name),
            ("proposal_only", self.proposal_only),
            ("result_present", self.result_present),
            ("usage", dict(self.usage) if self.usage is not None else None),
            ("latency_ms", self.latency_ms),
            ("detail", self.detail),
            ("phase", self.phase),
            ("label", self.label),
            ("orchestration_role", self.orchestration_role),
            ("subtask_id", self.subtask_id),
            ("subtask_goal", self.subtask_goal),
            ("public_thought", self.public_thought),
            ("tool_input_summary", self.tool_input_summary),
            ("tool_output_summary", self.tool_output_summary),
            ("next_step", self.next_step),
            ("blocked_on_user", self.blocked_on_user),
            ("role", self.role),
            ("proposal_id", self.proposal_id),
            ("context_hash", self.context_hash),
            ("public_summary", self.public_summary),
            ("validation_status", self.validation_status),
            ("degraded_reason", self.degraded_reason),
            ("next_role", self.next_role),
            ("accepted", self.accepted),
            ("rejected", self.rejected),
        ):
            if value is not None:
                result[field_name] = value
        return result


@dataclass(frozen=True)
class TimingSummary:
    ingress_to_router_ms: int | None = None
    router_to_slowtask_ms: int | None = None
    context_build_ms: int | None = None
    provider_first_event_ms: int | None = None
    provider_total_ms: int | None = None
    structured_validation_ms: int | None = None
    tool_authorization_wait_ms: int | None = None
    tool_execution_ms: int | None = None
    confirmation_wait_ms: int | None = None
    end_to_end_ms: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "ingress_to_router_ms": self.ingress_to_router_ms,
            "router_to_slowtask_ms": self.router_to_slowtask_ms,
            "context_build_ms": self.context_build_ms,
            "provider_first_event_ms": self.provider_first_event_ms,
            "provider_total_ms": self.provider_total_ms,
            "structured_validation_ms": self.structured_validation_ms,
            "tool_authorization_wait_ms": self.tool_authorization_wait_ms,
            "tool_execution_ms": self.tool_execution_ms,
            "confirmation_wait_ms": self.confirmation_wait_ms,
            "end_to_end_ms": self.end_to_end_ms,
        }


@dataclass(frozen=True)
class WorkbenchSnapshot:
    snapshot_id: str
    session_id: str
    mode: str
    router: Mapping[str, Any] | None
    task: Mapping[str, Any] | None
    conversation: tuple[Mapping[str, Any], ...]
    timeline: tuple[Mapping[str, Any], ...]
    provider_trace: tuple[ProviderTraceItem, ...]
    codex_proposals: tuple[Mapping[str, Any], ...]
    context_pack: TaskContextPack
    timing_summary: TimingSummary
    capability_snapshot: Mapping[str, Any]
    capability_matrices: tuple[Mapping[str, Any], ...]
    replay: Mapping[str, Any]
    safety: Mapping[str, Any]
    live_progress: tuple[ProviderTraceItem, ...] = ()
    streaming: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "router": deepcopy(dict(self.router)) if self.router is not None else None,
            "task": deepcopy(dict(self.task)) if self.task is not None else None,
            "conversation": deepcopy(list(self.conversation)),
            "timeline": deepcopy(list(self.timeline)),
            "provider_trace": [item.to_dict() for item in self.provider_trace],
            "live_progress": [item.to_dict() for item in self.live_progress],
            "streaming": deepcopy(dict(self.streaming or {
                "active": False,
                "phase": "idle",
                "label": "等待新的 Workbench turn",
                "detail": "",
                "sequence": 0,
            })),
            "codex_proposals": deepcopy(list(self.codex_proposals)),
            "context_pack": self.context_pack.to_dict(),
            "context_hash": self.context_pack.context_hash,
            "prompt_preview": self.context_pack.prompt_preview,
            "timing_summary": self.timing_summary.to_dict(),
            "capability_snapshot": deepcopy(dict(self.capability_snapshot)),
            "capability_matrices": deepcopy(list(self.capability_matrices)),
            "replay": deepcopy(dict(self.replay)),
            "safety": deepcopy(dict(self.safety)),
        }


def project_workbench_snapshot(
    *,
    session_id: str,
    mode: str,
    events: Sequence[Mapping[str, Any]],
    slowtask_state: SlowTaskState,
    task_focus_state: TaskFocusState,
    tool_execution_state: ToolExecutionState,
    context_pack: TaskContextPack,
    conversation: Sequence[Mapping[str, Any]],
    evidence_catalog: Mapping[str, Mapping[str, Any]],
    provider_trace: Sequence[ProviderTraceItem],
    codex_proposals: Sequence[Mapping[str, Any]],
    capability_snapshot: Mapping[str, Any] | None = None,
    capability_matrices: Sequence[Mapping[str, Any]] = (),
    live_progress: Sequence[ProviderTraceItem] = (),
    streaming: Mapping[str, Any] | None = None,
) -> WorkbenchSnapshot:
    task_created_event_id = next(
        (
            str(event["event_id"])
            for event in events
            if event.get("event_name") == "SLOWTASK_CREATED"
        ),
        None,
    )
    task = _public_task(
        slowtask_state,
        tool_execution_state=tool_execution_state,
        evidence_catalog=evidence_catalog,
        context_pack=context_pack,
        task_created_event_id=task_created_event_id,
    )
    router = _public_router(events, task_focus_state=task_focus_state)
    timeline = tuple(_public_timeline_event(event) for event in events if _is_displayable_event(event))
    timing = _timing_summary(events, provider_trace=provider_trace)
    digest_payload = {
        "events": [
            {
                "event_id": event.get("event_id"),
                "event_name": event.get("event_name"),
                "event_seq": event.get("event_seq"),
            }
            for event in events
        ],
        "task": task,
        "router": router,
    }
    state_digest = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    last_seq = int(events[-1]["event_seq"]) if events else 0
    return WorkbenchSnapshot(
        snapshot_id=f"snapshot_{session_id}_{last_seq:08d}",
        session_id=session_id,
        mode=mode,
        router=router,
        task=task,
        conversation=tuple(deepcopy(dict(item)) for item in conversation[-24:]),
        timeline=timeline[-120:],
        provider_trace=tuple(provider_trace[-80:]),
        live_progress=tuple(live_progress[-80:]),
        streaming=deepcopy(dict(streaming or {
            "active": False,
            "phase": "idle",
            "label": "等待新的 Workbench turn",
            "detail": "",
            "sequence": 0,
        })),
        codex_proposals=tuple(deepcopy(dict(item)) for item in codex_proposals[-12:]),
        context_pack=context_pack,
        timing_summary=timing,
        capability_snapshot=deepcopy(dict(capability_snapshot or {})),
        capability_matrices=tuple(deepcopy(dict(item)) for item in capability_matrices),
        replay={
            "status": "deterministic_projection_available",
            "replay_mode": "deterministic",
            "replay_reruns_provider": False,
            "replay_reruns_tool": False,
            "state_digest": f"sha256:{state_digest}",
            "event_count": len(events),
        },
        safety={
            "raw_audio_included": False,
            "raw_provider_body_included": False,
            "chain_of_thought_included": False,
            "prompt_dump_included": False,
            "secret_included": False,
            "local_path_included": False,
            "frontend_fact_owner": False,
            "codex_fact_owner": False,
        },
    )


def _public_task(
    state: SlowTaskState,
    *,
    tool_execution_state: ToolExecutionState,
    evidence_catalog: Mapping[str, Mapping[str, Any]],
    context_pack: TaskContextPack,
    task_created_event_id: str | None = None,
) -> dict[str, Any] | None:
    task = _active_or_last_task(state)
    if task is None:
        return None
    current_plan_version = task.current_plan_version
    current_evidence, stale_evidence = _public_evidence(
        task,
        evidence_catalog=evidence_catalog,
        current_plan_version=current_plan_version,
    )
    plan_versions = _public_plan_versions(
        task,
        task_created_event_id=task_created_event_id,
        evidence_catalog=evidence_catalog,
    )
    pending = _public_pending_confirmation(task)
    tool_calls = _public_tool_calls(tool_execution_state, task_id=task.task_id)
    current_missing_fields = (
        _task_fields(task, {"WAITING_FOR_SLOT", "INSUFFICIENT_EVIDENCE_FOR_ACTION", "CLARIFICATION_REQUESTED"})
        if task.lifecycle_state == "WAITING_FOR_SLOT"
        else []
    )
    return {
        "task_id": task.task_id,
        "lifecycle": task.lifecycle_state,
        "current_plan_version": current_plan_version,
        "latest_task_event_seq": task.current_task_event_seq,
        "goal_summary": _safe_summary(
            str(
                evidence_catalog.get(
                    task.source_evidence_refs[0] if task.source_evidence_refs else "",
                    {},
                ).get("summary", task.initial_goal_ref)
            )
        ),
        "resolved_arguments": {
            "refs": list(task.resolved_arguments_refs[-8:]),
            "provenance_refs": list(task.argument_provenance_refs[-12:]),
        },
        "missing_fields": current_missing_fields,
        "conflicting_fields": _task_fields(task, {"AMBIGUITY_DETECTED"}),
        "slot_summary": deepcopy(list(context_pack.slot_summary)),
        "readiness": deepcopy(dict(context_pack.readiness)),
        "clarification": deepcopy(dict(context_pack.clarification)) if context_pack.clarification is not None else None,
        "task_requirement_model_ref": context_pack.task_requirement_model_ref,
        "task_requirement_model_version": context_pack.task_requirement_model_version,
        "task_kind": context_pack.task_kind,
        "task_model_status": task.task_model_status,
        "task_model_confidence": task.task_model_confidence,
        "task_model_bootstrap_reason": task.task_model_bootstrap_reason,
        "task_model_needs_remodeling": task.task_model_needs_remodeling,
        "task_components": list(context_pack.task_components),
        "requirement_summary": deepcopy(list(context_pack.requirement_summary)),
        "current_role": context_pack.current_role,
        "prior_role_proposal_refs": list(context_pack.prior_role_proposal_refs),
        "available_tool_manifest_summaries": deepcopy(list(context_pack.available_tool_manifest_summaries)),
        "selected_clarification_requirement_ids": list(context_pack.selected_clarification_requirement_ids),
        "planner_mode": context_pack.planner_mode,
        "current_plan_proposal_ref": context_pack.current_plan_proposal_ref,
        "reviewer_status": context_pack.reviewer_status,
        "plan_versions": plan_versions,
        "evidence": current_evidence,
        "stale_evidence": stale_evidence,
        "adopted_evidence": [
            {
                "event_id": item.event_id,
                "plan_version": item.plan_version,
                "stale_evidence_ref": item.stale_evidence_ref,
                "adopted_from_plan_version": item.adopted_from_plan_version,
                "adoption_mode": item.adoption_mode,
                "adoption_reason": _safe_summary(item.adoption_reason),
                "adopted_scope": list(item.adopted_scope),
            }
            for item in task.adopted_evidence[-12:]
        ],
        "pending_confirmation": pending,
        "in_flight_tool_calls": [call for call in tool_calls if call["status"] not in {"RESULT_RECEIVED", "FAILED", "CANCELLED"}],
        "tool_calls": tool_calls,
        "semantic_commitment": {
            "status": "emitted" if task.semantic_commitments else "not_emitted",
            "commitment_id": task.semantic_commitments[-1].commitment_id if task.semantic_commitments else None,
            "plan_version": task.semantic_commitments[-1].plan_version if task.semantic_commitments else None,
        },
        "terminal_outcome": task.terminal_outcome,
        "late_evidence_count": len(task.late_events),
        "stale_evidence_policy": "old plan ToolResult is stale until explicit SlowTask adoption/rebase",
    }


def _public_router(
    events: Sequence[Mapping[str, Any]],
    *,
    task_focus_state: TaskFocusState,
) -> dict[str, Any] | None:
    router_events = [event for event in events if event.get("event_name") == "ROUTER_DECISION_EMITTED"]
    latest = router_events[-1] if router_events else None
    if latest is None and task_focus_state.last_focus_decision is None:
        return None
    if latest is None:
        return {
            "router_decision": None,
            "task_focus": task_focus_state.last_focus_decision,
            "confidence": task_focus_state.last_focus_confidence,
            "foreground_mode": task_focus_state.foreground_mode,
            "active_task_id": task_focus_state.active_task_id,
        }
    return {
        "router_decision": latest.get("router_decision"),
        "task_focus": latest.get("task_focus"),
        "confidence": latest.get("confidence"),
        "evidence_uncertainty": latest.get("evidence_uncertainty", "low"),
        "turn_id": latest.get("turn_id"),
        "utterance_id": latest.get("utterance_id"),
        "event_id": latest.get("event_id"),
        "event_seq": latest.get("event_seq"),
        "active_task_id": task_focus_state.active_task_id,
        "foreground_mode": task_focus_state.foreground_mode,
        "task_focus_event_id": task_focus_state.last_focus_event_id,
    }


def _public_plan_versions(
    task: SlowTaskRecord,
    *,
    task_created_event_id: str | None = None,
    evidence_catalog: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    evidence_catalog = evidence_catalog or {}
    initial_summary = str(
        evidence_catalog.get(
            task.source_evidence_refs[0] if task.source_evidence_refs else "",
            {},
        ).get("summary", task.initial_goal_ref)
    )
    result: list[dict[str, Any]] = [
        {
            "plan_version": 1,
            "status": "current" if task.current_plan_version == 1 else "superseded",
            "summary": _safe_summary(initial_summary),
            "reason": "initial_plan",
            "created_by_event_id": task_created_event_id or task.last_slowtask_event_id,
        }
    ]
    for advance in task.plan_advances:
        summary = "用户 material patch 导致当前计划重新规划。"
        interpretation_reason = ""
        if advance.caused_by_user_patch_event_id:
            interpretation_reason = next(
                (
                    item.interpretation_reason or ""
                    for item in reversed(task.user_patch_interpretations)
                    if item.caused_by_event_id == advance.caused_by_user_patch_event_id
                    and item.materially_changes_task
                ),
                "",
            )
            patch_evidence_ref = next(
                (
                    evidence.evidence_ref
                    for evidence in task.user_patch_evidence
                    if evidence.event_id == advance.caused_by_user_patch_event_id
                ),
                None,
            )
            summary = str(
                evidence_catalog.get(patch_evidence_ref or advance.caused_by_user_patch_event_id, {}).get(
                    "summary",
                    summary,
                )
            )
        result[-1]["status"] = "superseded"
        result.append(
            {
                "plan_version": advance.to_plan_version,
                "status": "current" if advance.to_plan_version == task.current_plan_version else "superseded",
                "summary": _safe_summary(summary),
                "reason": (
                    "user_patch:" + _safe_summary(interpretation_reason)
                    if interpretation_reason
                    else "user_patch" if advance.caused_by_user_patch_event_id else "manual_demo"
                ),
                "created_by_event_id": advance.event_id,
            }
        )
    if result:
        if task.terminal_outcome == "COMPLETED":
            result[-1]["status"] = "completed"
        elif task.terminal_outcome == "FAILED":
            result[-1]["status"] = "failed"
    return result


def _public_pending_confirmation(task: SlowTaskRecord) -> dict[str, Any] | None:
    state = task.confirmation_state
    if state.pending_confirmation_id is None:
        return None
    return {
        "confirmation_id": state.pending_confirmation_id,
        "scope": state.confirmation_scope,
        "plan_version": task.current_plan_version,
        "prompt": "请确认是否取消当前 synthetic Workbench SlowTask。",
        "prompt_ref": state.prompt_ref,
        "risk_summary": "确认后任务进入 CANCELLED；MVP 不实现 pause/resume。",
        "status": state.status,
    }


def _public_evidence(
    task: SlowTaskRecord,
    *,
    evidence_catalog: Mapping[str, Mapping[str, Any]],
    current_plan_version: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for ref, item in evidence_catalog.items():
        if item.get("task_id", task.task_id) != task.task_id:
            continue
        record = {
            "evidence_id": str(item.get("evidence_id", ref)),
            "evidence_ref": str(ref),
            "source": str(item.get("source", "slowtask")),
            "trust_level": str(item.get("trust_level", "hypothesis")),
            "plan_version": int(item.get("plan_version", current_plan_version)),
            "label": _safe_summary(str(item.get("label", "evidence"))),
            "summary": _safe_summary(str(item.get("summary", ""))),
            "stale": bool(item.get("stale", False)),
            "provenance": str(item.get("provenance", "event_journal")),
        }
        if record["stale"] or ref in task.stale_evidence_refs:
            stale.append(record)
        elif record["plan_version"] <= current_plan_version:
            current.append(record)
    for ref in task.stale_evidence_refs:
        if not any(item["evidence_ref"] == ref for item in stale):
            stale.append(
                {
                    "evidence_id": ref,
                    "evidence_ref": ref,
                    "source": "tool",
                    "trust_level": "stale_evidence",
                    "plan_version": max(1, current_plan_version - 1),
                    "label": "Old tool result",
                    "summary": "旧 plan 工具结果已返回，但不能推进当前计划。",
                    "stale": True,
                    "provenance": "event_journal",
                }
            )
    return current[-32:], stale[-32:]


def _public_tool_calls(state: ToolExecutionState, *, task_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for call in state.tool_calls.values():
        if call.task_id != task_id:
            continue
        result.append(
            {
                "tool_call_id": call.tool_call_id,
                "tool_name": call.tool_name,
                "tool_adapter_id": call.tool_adapter_id,
                "plan_version": call.plan_version,
                "status": call.lifecycle_status,
                "last_event_id": call.last_tool_event_id,
                "progress_count": len(call.progress_updates),
                "ui_patch_count": len(call.ui_patches),
                "result_count": len(call.results),
                "preview_available": bool(call.preview_events),
                "authorization_count": len(call.authorizations),
            }
        )
    return result[-24:]


def _public_timeline_event(event: Mapping[str, Any]) -> dict[str, Any]:
    event_name = str(event.get("event_name", "UNKNOWN"))
    details: list[str] = []
    for field in ("router_decision", "task_focus", "tool_name", "tool_call_id", "confirmation_scope", "stale_reason", "progress_type", "result_status", "cancel_reason"):
        value = event.get(field)
        if value not in (None, ""):
            details.append(f"{field}={_safe_summary(str(value))}")
    if event.get("plan_version") is not None:
        details.append(f"plan_version={event['plan_version']}")
    if event.get("task_event_seq") is not None:
        details.append(f"task_event_seq={event['task_event_seq']}")
    return {
        "event_id": str(event.get("event_id", "")),
        "event_seq": event.get("event_seq"),
        "event_name": event_name,
        "canonical": True,
        "source_module": str(event.get("source_module", "")),
        "task_id": event.get("task_id"),
        "plan_version": event.get("plan_version"),
        "task_event_seq": event.get("task_event_seq"),
        "created_monotonic_ms": event.get("created_monotonic_ms"),
        "details": details,
    }


def _is_displayable_event(event: Mapping[str, Any]) -> bool:
    return event.get("event_name") not in {
        "SESSION_STARTED",
        "ADAPTER_CAPABILITY_SNAPSHOT_RECORDED",
        "TRACE_SECRET_REDACTION_APPLIED",
    }


def _timing_summary(
    events: Sequence[Mapping[str, Any]],
    *,
    provider_trace: Sequence[ProviderTraceItem],
) -> TimingSummary:
    def first(name: str) -> Mapping[str, Any] | None:
        return next((event for event in events if event.get("event_name") == name), None)

    def last(name: str) -> Mapping[str, Any] | None:
        return next((event for event in reversed(events) if event.get("event_name") == name), None)

    def ms(event: Mapping[str, Any] | ProviderTraceItem | None) -> int | None:
        if isinstance(event, ProviderTraceItem):
            value = event.created_monotonic_ms
        else:
            value = event.get("created_monotonic_ms") if event is not None else None
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    def delta(
        start: Mapping[str, Any] | ProviderTraceItem | None,
        end: Mapping[str, Any] | ProviderTraceItem | None,
    ) -> int | None:
        start_ms, end_ms = ms(start), ms(end)
        if start_ms is None or end_ms is None:
            return None
        return max(0, end_ms - start_ms)

    ingress = first("TEXT_INPUT_RECEIVED") or first("TURN_INGRESS_COMMITTED")
    router = first("ROUTER_DECISION_EMITTED")
    slowtask = first("SLOWTASK_CREATED")
    provider_first = provider_trace[0] if provider_trace else None
    provider_last = provider_trace[-1] if provider_trace else None
    structured = last("SLOW_LLM_STRUCTURED_OUTPUT_EMITTED")
    ready = last("TOOL_ARGUMENTS_READY") or last("TOOL_PREVIEW_AVAILABLE")
    authorized = last("TOOL_EXECUTION_AUTHORIZED")
    tool_started = last("TOOL_EXECUTION_STARTED")
    tool_result = last("TOOL_RESULT_RECEIVED")
    confirmation_required = last("CONFIRMATION_REQUIRED")
    confirmation_accepted = last("CONFIRMATION_ACCEPTED")
    provider_first_ms = provider_first.created_monotonic_ms if provider_first else None
    provider_last_ms = provider_last.created_monotonic_ms if provider_last else None
    provider_total = (
        max(0, provider_last_ms - provider_first_ms)
        if isinstance(provider_first_ms, int) and isinstance(provider_last_ms, int)
        else None
    )
    structured_ms = (
        max(0, int(structured["created_monotonic_ms"]) - provider_first_ms)
        if structured is not None and isinstance(provider_first_ms, int)
        else None
    )
    return TimingSummary(
        ingress_to_router_ms=delta(ingress, router),
        router_to_slowtask_ms=delta(router, slowtask),
        context_build_ms=delta(slowtask, provider_first),
        provider_first_event_ms=delta(slowtask, provider_first),
        provider_total_ms=provider_total,
        structured_validation_ms=structured_ms,
        tool_authorization_wait_ms=delta(ready, authorized),
        tool_execution_ms=delta(tool_started, tool_result),
        confirmation_wait_ms=delta(confirmation_required, confirmation_accepted),
        end_to_end_ms=delta(ingress, events[-1] if events else None),
    )


def _task_fields(task: SlowTaskRecord, names: set[str]) -> list[str]:
    for event in reversed((*task.evidence_events, *task.progress_events)):
        if event.event_name not in names:
            continue
        values: list[str] = []
        for ref in event.refs:
            if _field_like(ref) and ref not in values:
                values.append(ref)
        if values:
            return values[:24]
    return []


def _field_like(value: str) -> bool:
    if "://" in value:
        return False
    return all(char.isalnum() or char == "_" for char in value)


def _active_or_last_task(state: SlowTaskState) -> SlowTaskRecord | None:
    active = [task for task in state.tasks.values() if not task.is_terminal]
    if active:
        return sorted(active, key=lambda item: item.task_id)[0]
    if state.last_task_id is not None:
        return state.tasks.get(state.last_task_id)
    return None


def _safe_summary(value: str) -> str:
    normalized = " ".join(value.split())
    lowered = normalized.lower()
    if any(marker in lowered for marker in ("bearer ", "api_key=", "authorization=", "token=", "password=", "/users/", "file://")):
        return "[redacted unsafe metadata]"
    return normalized[:280]


__all__ = [
    "ProviderTraceItem",
    "TimingSummary",
    "WorkbenchSnapshot",
    "project_workbench_snapshot",
]
