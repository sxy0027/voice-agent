from __future__ import annotations

"""Bounded TaskContextPack projection for the Slow-system Workbench."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from voice_agent.state.slowtask_state import SlowTaskRecord, SlowTaskState
from voice_agent.state.task_focus_state import TaskFocusState
from voice_agent.state.tool_execution_state import ToolExecutionState


TASK_CONTEXT_PACK_SCHEMA_VERSION = "workbench.task_context_pack.v1"
MAX_EVIDENCE_ITEMS = 24
MAX_CONVERSATION_ITEMS = 8
MAX_SUMMARY_LENGTH = 280
_UNSAFE_SUMMARY_MARKERS = (
    "bearer ",
    "api_key=",
    "authorization=",
    "token=",
    "password=",
    "file://",
    "/users/",
    "audio/raw/",
    "traces/",
)


@dataclass(frozen=True)
class TaskContextPack:
    schema_version: str
    context_hash: str
    task_binding: Mapping[str, Any]
    lifecycle: str
    current_goal: str
    current_constraints: tuple[Mapping[str, Any], ...]
    resolved_arguments: Mapping[str, Any]
    missing_fields: tuple[str, ...]
    conflicting_fields: tuple[str, ...]
    plan_history: tuple[Mapping[str, Any], ...]
    accepted_evidence_refs: tuple[str, ...]
    accepted_evidence_summaries: tuple[Mapping[str, Any], ...]
    stale_evidence_refs: tuple[str, ...]
    stale_evidence_summaries: tuple[Mapping[str, Any], ...]
    adopted_evidence: tuple[Mapping[str, Any], ...]
    pending_confirmation: Mapping[str, Any] | None
    in_flight_tool_calls: tuple[Mapping[str, Any], ...]
    recent_interaction_summary: tuple[Mapping[str, Any], ...]
    latest_user_input: str | None
    trust_labels: Mapping[str, str]
    provenance: Mapping[str, Any]
    prompt_preview: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "context_hash": self.context_hash,
            "task_binding": deepcopy(dict(self.task_binding)),
            "lifecycle": self.lifecycle,
            "current_goal": self.current_goal,
            "current_constraints": deepcopy(list(self.current_constraints)),
            "resolved_arguments": deepcopy(dict(self.resolved_arguments)),
            "missing_fields": list(self.missing_fields),
            "conflicting_fields": list(self.conflicting_fields),
            "plan_history": deepcopy(list(self.plan_history)),
            "accepted_evidence_refs": list(self.accepted_evidence_refs),
            "accepted_evidence_summaries": deepcopy(list(self.accepted_evidence_summaries)),
            "stale_evidence_refs": list(self.stale_evidence_refs),
            "stale_evidence_summaries": deepcopy(list(self.stale_evidence_summaries)),
            "adopted_evidence": deepcopy(list(self.adopted_evidence)),
            "pending_confirmation": deepcopy(dict(self.pending_confirmation))
            if self.pending_confirmation is not None
            else None,
            "in_flight_tool_calls": deepcopy(list(self.in_flight_tool_calls)),
            "recent_interaction_summary": deepcopy(list(self.recent_interaction_summary)),
            "latest_user_input": self.latest_user_input,
            "trust_labels": deepcopy(dict(self.trust_labels)),
            "provenance": deepcopy(dict(self.provenance)),
            "prompt_preview": self.prompt_preview,
        }


class TaskContextPackBuilder:
    """Build context from reducer projections, never from provider payloads."""

    def build(
        self,
        *,
        slowtask_state: SlowTaskState,
        task_focus_state: TaskFocusState,
        tool_execution_state: ToolExecutionState,
        evidence_catalog: Mapping[str, Mapping[str, Any]],
        conversation: Sequence[Mapping[str, Any]],
        latest_user_input: str | None,
        resolved_argument_values: Mapping[str, Any] | None = None,
        task_created_event_id: str | None = None,
    ) -> TaskContextPack:
        task = _active_or_last_task(slowtask_state)
        if task is None:
            payload = {
                "schema_version": TASK_CONTEXT_PACK_SCHEMA_VERSION,
                "task_binding": {
                    "task_id": None,
                    "plan_version": None,
                    "task_event_seq": None,
                },
                "lifecycle": "NONE",
                "current_goal": "none",
                "current_constraints": [],
                "resolved_arguments": {},
                "missing_fields": [],
                "conflicting_fields": [],
                "plan_history": [],
                "accepted_evidence_refs": [],
                "accepted_evidence_summaries": [],
                "stale_evidence_refs": [],
                "stale_evidence_summaries": [],
                "adopted_evidence": [],
                "pending_confirmation": None,
                "in_flight_tool_calls": [],
                "recent_interaction_summary": _recent_interactions(conversation),
                "latest_user_input": _bounded_optional_text(latest_user_input),
                "trust_labels": {},
                "provenance": {
                    "source": "event_journal_reducer",
                    "task_focus": task_focus_state.to_digest_dict(),
                },
            }
            return _pack_from_payload(payload)

        current_plan_version = task.current_plan_version
        accepted, stale = _catalog_evidence(
            task,
            evidence_catalog=evidence_catalog,
            current_plan_version=current_plan_version,
        )
        missing_fields, conflicting_fields = (
            _missing_and_conflicting_fields(task)
            if task.lifecycle_state == "WAITING_FOR_SLOT"
            else ((), ())
        )
        plan_history = _plan_history(
            task,
            evidence_catalog=evidence_catalog,
            task_created_event_id=task_created_event_id,
        )
        constraints = _current_constraints(task, accepted)
        in_flight = _in_flight_tools(tool_execution_state, task_id=task.task_id)
        pending_confirmation = _pending_confirmation(task)
        resolved_arguments = {
            "refs": list(task.resolved_arguments_refs[-4:]),
            "provenance_refs": list(task.argument_provenance_refs[-8:]),
        }
        if resolved_argument_values:
            resolved_arguments["synthetic_values"] = _bounded_mapping(resolved_argument_values)

        payload = {
            "schema_version": TASK_CONTEXT_PACK_SCHEMA_VERSION,
            "task_binding": {
                "task_id": task.task_id,
                "plan_version": current_plan_version,
                "task_event_seq": task.current_task_event_seq,
            },
            "lifecycle": task.lifecycle_state,
            "current_goal": _bounded_text(
                str(
                    evidence_catalog.get(
                        task.source_evidence_refs[0] if task.source_evidence_refs else "",
                        {},
                    ).get("summary", task.initial_goal_ref)
                )
            ),
            "current_constraints": constraints,
            "resolved_arguments": resolved_arguments,
            "missing_fields": list(missing_fields),
            "conflicting_fields": list(conflicting_fields),
            "plan_history": plan_history,
            "accepted_evidence_refs": [item["evidence_ref"] for item in accepted],
            "accepted_evidence_summaries": accepted,
            "stale_evidence_refs": [item["evidence_ref"] for item in stale],
            "stale_evidence_summaries": stale,
            "adopted_evidence": [
                {
                    "event_id": item.event_id,
                    "plan_version": item.plan_version,
                    "stale_evidence_ref": item.stale_evidence_ref,
                    "source_tool_result_event_id": item.source_tool_result_event_id,
                    "adopted_from_plan_version": item.adopted_from_plan_version,
                    "adoption_mode": item.adoption_mode,
                    "adoption_reason": _bounded_text(item.adoption_reason),
                    "adopted_scope": list(item.adopted_scope),
                }
                for item in task.adopted_evidence[-8:]
            ],
            "pending_confirmation": pending_confirmation,
            "in_flight_tool_calls": in_flight,
            "recent_interaction_summary": _recent_interactions(conversation),
            "latest_user_input": _bounded_optional_text(latest_user_input),
            "trust_labels": {
                "user_patch": "authoritative_evidence_plus_non_authoritative_hypothesis",
                "demo_tool_result": "TRUSTED_DEMO_TOOL_RESULT",
                "web_search": "UNTRUSTED_WEB_EVIDENCE",
                "stale_result": "stale_evidence_until_explicit_adoption",
                "codex": "evidence_candidate_only",
            },
            "provenance": {
                "source": "event_journal_reducer",
                "task_id": task.task_id,
                "task_focus_event_id": task_focus_state.last_focus_event_id,
                "last_slowtask_event_id": task.last_slowtask_event_id,
                "tool_state_last_event_id": tool_execution_state.last_tool_event_id,
            },
        }
        return _pack_from_payload(payload)


def _pack_from_payload(payload: Mapping[str, Any]) -> TaskContextPack:
    normalized = deepcopy(dict(payload))
    normalized.pop("context_hash", None)
    normalized["prompt_preview"] = _prompt_preview(normalized)
    canonical = json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    context_hash = f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    normalized["context_hash"] = context_hash
    return TaskContextPack(
        schema_version=str(normalized["schema_version"]),
        context_hash=context_hash,
        task_binding=normalized["task_binding"],
        lifecycle=str(normalized["lifecycle"]),
        current_goal=str(normalized["current_goal"]),
        current_constraints=tuple(normalized["current_constraints"]),
        resolved_arguments=normalized["resolved_arguments"],
        missing_fields=tuple(str(field) for field in normalized["missing_fields"]),
        conflicting_fields=tuple(str(field) for field in normalized["conflicting_fields"]),
        plan_history=tuple(normalized["plan_history"]),
        accepted_evidence_refs=tuple(str(ref) for ref in normalized["accepted_evidence_refs"]),
        accepted_evidence_summaries=tuple(normalized["accepted_evidence_summaries"]),
        stale_evidence_refs=tuple(str(ref) for ref in normalized["stale_evidence_refs"]),
        stale_evidence_summaries=tuple(normalized["stale_evidence_summaries"]),
        adopted_evidence=tuple(normalized["adopted_evidence"]),
        pending_confirmation=normalized["pending_confirmation"],
        in_flight_tool_calls=tuple(normalized["in_flight_tool_calls"]),
        recent_interaction_summary=tuple(normalized["recent_interaction_summary"]),
        latest_user_input=normalized["latest_user_input"],
        trust_labels=normalized["trust_labels"],
        provenance=normalized["provenance"],
        prompt_preview=str(normalized["prompt_preview"]),
    )


def _active_or_last_task(state: SlowTaskState) -> SlowTaskRecord | None:
    active = [task for task in state.tasks.values() if not task.is_terminal]
    if active:
        return sorted(active, key=lambda item: item.task_id)[0]
    if state.last_task_id is not None:
        return state.tasks.get(state.last_task_id)
    return None


def _catalog_evidence(
    task: SlowTaskRecord,
    *,
    evidence_catalog: Mapping[str, Mapping[str, Any]],
    current_plan_version: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for ref, item in evidence_catalog.items():
        if str(item.get("task_id", task.task_id)) != task.task_id:
            continue
        normalized = {
            "evidence_ref": str(ref),
            "label": _bounded_text(str(item.get("label", "evidence"))),
            "summary": _bounded_text(str(item.get("summary", ""))),
            "plan_version": int(item.get("plan_version", current_plan_version)),
            "trust_level": str(item.get("trust_level", "hypothesis")),
            "source": str(item.get("source", "slowtask")),
            "provenance": str(item.get("provenance", "event_journal")),
        }
        if bool(item.get("stale")) or ref in task.stale_evidence_refs:
            stale.append(normalized)
        elif normalized["plan_version"] <= current_plan_version:
            accepted.append(normalized)
    for ref in task.stale_evidence_refs:
        if not any(item["evidence_ref"] == ref for item in stale):
            stale.append(
                {
                    "evidence_ref": ref,
                    "label": "stale evidence",
                    "summary": "旧 plan 结果已记录但尚未 adopt/rebase。",
                    "plan_version": max(1, current_plan_version - 1),
                    "trust_level": "stale_evidence",
                    "source": "tool_result",
                    "provenance": "event_journal",
                }
            )
    return accepted[-MAX_EVIDENCE_ITEMS:], stale[-MAX_EVIDENCE_ITEMS:]


def _missing_and_conflicting_fields(task: SlowTaskRecord) -> tuple[tuple[str, ...], tuple[str, ...]]:
    missing: list[str] = []
    conflicting: list[str] = []
    for event in task.evidence_events:
        if event.event_name in {"WAITING_FOR_SLOT", "INSUFFICIENT_EVIDENCE_FOR_ACTION", "CLARIFICATION_REQUESTED"}:
            missing.extend(ref for ref in event.refs if _field_like(ref))
        if event.event_name == "AMBIGUITY_DETECTED":
            conflicting.extend(ref for ref in event.refs if _field_like(ref))
    return _unique_strings(missing), _unique_strings(conflicting)


def _field_like(value: str) -> bool:
    if "://" in value:
        return False
    return all(char.isalnum() or char == "_" for char in value)


def _current_constraints(task: SlowTaskRecord, accepted: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    for item in accepted:
        label = str(item.get("label", ""))
        if "patch" in label.lower() or "constraint" in label.lower() or "user" in label.lower():
            constraints.append(
                {
                    "summary": _bounded_text(str(item.get("summary", ""))),
                    "plan_version": int(item.get("plan_version", task.current_plan_version)),
                    "evidence_ref": str(item.get("evidence_ref", "")),
                }
            )
    return constraints[-12:]


def _plan_history(
    task: SlowTaskRecord,
    *,
    evidence_catalog: Mapping[str, Mapping[str, Any]],
    task_created_event_id: str | None = None,
) -> list[dict[str, Any]]:
    initial_summary = str(
        evidence_catalog.get(
            task.source_evidence_refs[0] if task.source_evidence_refs else "",
            {},
        ).get("summary", task.initial_goal_ref)
    )
    history: list[dict[str, Any]] = [
        {
            "plan_version": 1,
            "status": "current" if task.current_plan_version == 1 else "superseded",
            "reason": "initial_plan",
            "summary": _bounded_text(initial_summary),
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
            summary = _bounded_text(
                str(
                    evidence_catalog.get(patch_evidence_ref or advance.caused_by_user_patch_event_id, {}).get(
                        "summary",
                        summary,
                    )
                )
            )
        history[-1]["status"] = "superseded"
        history.append(
            {
                "plan_version": advance.to_plan_version,
                "status": "current" if advance.to_plan_version == task.current_plan_version else "superseded",
                "reason": (
                    "user_patch:" + _bounded_text(interpretation_reason)
                    if interpretation_reason
                    else "user_patch" if advance.caused_by_user_patch_event_id else "manual_demo"
                ),
                "summary": summary,
                "created_by_event_id": advance.event_id,
            }
        )
    if history:
        history[-1]["status"] = "current" if not task.is_terminal or task.terminal_outcome == "COMPLETED" else task.terminal_outcome.lower()
    return history


def _pending_confirmation(task: SlowTaskRecord) -> dict[str, Any] | None:
    state = task.confirmation_state
    if state.pending_confirmation_id is None:
        return None
    return {
        "confirmation_id": state.pending_confirmation_id,
        "scope": state.confirmation_scope,
        "plan_version": task.current_plan_version,
        "prompt_ref": state.prompt_ref,
        "required_for_event_id": state.required_for_event_id,
        "status": state.status,
    }


def _in_flight_tools(state: ToolExecutionState, *, task_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for call in state.tool_calls.values():
        if call.task_id != task_id:
            continue
        if not call.execution_started or call.results or call.failures or call.cancellations:
            continue
        result.append(
            {
                "tool_call_id": call.tool_call_id,
                "tool_name": call.tool_name,
                "plan_version": call.plan_version,
                "status": call.lifecycle_status,
                "last_event_id": call.last_tool_event_id,
            }
        )
    return result[-12:]


def _recent_interactions(conversation: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in conversation[-MAX_CONVERSATION_ITEMS:]:
        result.append(
            {
                "speaker": str(item.get("speaker", "system")),
                "summary": _bounded_text(str(item.get("summary", item.get("text", "")))),
                "source": str(item.get("source", "workbench")),
            }
        )
    return result


def _prompt_preview(payload: Mapping[str, Any]) -> str:
    task_binding = payload.get("task_binding", {})
    return (
        "synthetic/redacted context preview: "
        f"task_id={task_binding.get('task_id')}; "
        f"plan_version={task_binding.get('plan_version')}; "
        f"task_event_seq={task_binding.get('task_event_seq')}; "
        f"accepted_evidence={len(payload.get('accepted_evidence_refs', ())) }; "
        f"stale_evidence={len(payload.get('stale_evidence_refs', ())) }; "
        f"in_flight_tools={len(payload.get('in_flight_tool_calls', ())) }"
    )


def _bounded_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted(value)[:24]:
        item = value[key]
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)] = _bounded_text(str(item)) if isinstance(item, str) else item
    return result


def _bounded_optional_text(value: str | None) -> str | None:
    return None if value is None else _bounded_text(value)


def _bounded_text(value: str) -> str:
    normalized = " ".join(str(value).split())
    lowered = normalized.lower()
    if any(marker in lowered for marker in _UNSAFE_SUMMARY_MARKERS):
        return "[redacted unsafe metadata]"
    return normalized[:MAX_SUMMARY_LENGTH]


def _unique_strings(values: Sequence[object]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = str(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return tuple(result[:24])


__all__ = [
    "TASK_CONTEXT_PACK_SCHEMA_VERSION",
    "TaskContextPack",
    "TaskContextPackBuilder",
]
