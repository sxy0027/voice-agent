from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Protocol

from voice_agent.adapters.capabilities import CREDENTIAL_LIKE_REF_PATTERN
from voice_agent.privacy.redaction import LOCAL_ONLY_PATH_PATTERN, SECRET_VALUE_PATTERN


class WorkbenchCodexProposalError(ValueError):
    """Raised when a Workbench Codex proposal is malformed or unsafe."""


ALLOWED_PROPOSAL_TYPES = frozenset(
    {
        "plan_update",
        "evidence_review",
        "tool_preview",
        "clarification",
        "commitment_draft",
    }
)
ALLOWED_PROVIDER_MODES = frozenset({"fake", "codex_unavailable"})
PROVIDER_DRAFT_STATUSES = frozenset({"draft", "validated"})
FINAL_PROPOSAL_STATUSES = frozenset({"draft", "validated", "accepted", "rejected"})
REQUIRED_PROPOSAL_FIELDS = (
    "proposal_id",
    "proposal_type",
    "status",
    "summary",
    "suggested_next_steps",
    "missing_fields",
    "requires_confirmation",
    "risk_notes",
    "source_evidence_refs",
    "safety",
)
REQUIRED_SAFETY_FLAGS = (
    "codex_is_fact_owner",
    "advances_plan_version",
    "authorizes_tool",
    "contains_secret",
)
NORMALIZED_SAFETY_FLAGS = (
    *REQUIRED_SAFETY_FLAGS,
    "mutates_task_snapshot",
    "emits_canonical_event",
    "executes_external_tool",
    "contains_raw_provider_body",
)
FORBIDDEN_PROPOSAL_KEYS = frozenset(
    {
        "event_name",
        "canonical_event",
        "canonical_events",
        "task_snapshot_patch",
        "task_mutation",
        "plan_version_advanced",
        "plan_version_advance",
        "semantic_commitment_event",
        "tool_authorization",
        "tool_execution",
        "ui_patch",
        "external_tool_call",
        "tool_execution_authorized",
        "tool_ui_state_patched",
        "raw_provider_body",
        "raw_provider_request",
        "raw_provider_response",
        "raw_request_body",
        "raw_response_body",
        "provider_body",
        "provider_payload",
        "provider_request",
        "provider_response",
        "headers",
        "authorization",
        "cookies",
        "cookie",
        "prompt_dump",
        "local_path",
        "file_path",
    }
)
FORBIDDEN_EVENT_NAMES = frozenset(
    {
        "PLAN_VERSION_ADVANCED",
        "SEMANTIC_COMMITMENT_EMITTED",
        "TOOL_EXECUTION_AUTHORIZED",
        "TOOL_UI_STATE_PATCHED",
    }
)
UNSAFE_STRING_MARKERS = tuple(
    marker.lower()
    for marker in (
        "file://",
        "/Users/",
        "\\Users\\",
        "/private/",
        ".env",
        "audio/raw/",
        "diagnostics/",
        "traces/",
        "replays/local/",
        "raw_provider_body",
        "raw_provider_request",
        "raw_provider_response",
        "raw audio",
        "prompt dump",
        "provider body",
        "provider payload",
        "authorization:",
        "cookie:",
    )
)
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]+$")


@dataclass(frozen=True)
class WorkbenchCodexProposalRequest:
    snapshot: Mapping[str, Any]
    intent: str
    proposal_type: str
    source_evidence_refs: tuple[str, ...]


class WorkbenchCodexProposalProvider(Protocol):
    def propose(self, request: WorkbenchCodexProposalRequest) -> Mapping[str, Any]:
        ...


class FakeWorkbenchCodexProposalProvider:
    """Deterministic proposal provider for demo and tests.

    This provider intentionally does not call Codex or any external model. It
    makes the Workbench proposal path runnable without credentials while keeping
    the same validation contract a future provider must satisfy.
    """

    def propose(self, request: WorkbenchCodexProposalRequest) -> Mapping[str, Any]:
        proposal_type = _proposal_type(request.proposal_type)
        source_refs = _non_empty_safe_string_tuple(
            request.source_evidence_refs,
            "source_evidence_refs",
        )
        summary_by_type = {
            "plan_update": "Codex 建议更新计划，因为用户补充了新的任务约束。",
            "evidence_review": "Codex 建议把当前证据分为用户事实和非权威假设来审阅。",
            "tool_preview": "Codex 建议先展示工具预览，等待后端授权后再执行。",
            "clarification": "Codex 建议向用户追问缺失字段，避免猜测关键参数。",
            "commitment_draft": "Codex 建议起草最终表述，但该草案不是 SemanticCommitment。",
        }
        missing_fields = ["budget_or_time_preference"] if proposal_type == "clarification" else []
        return {
            "proposal_id": f"proposal_demo_{proposal_type}",
            "proposal_type": proposal_type,
            "status": "draft",
            "summary": summary_by_type[proposal_type],
            "suggested_next_steps": [
                "保留已有用户约束和来源引用。",
                "只把该内容作为 proposal 展示给用户或研发。",
                "如果需要改变任务，必须交给后端 SlowTask 规则处理。",
            ],
            "missing_fields": missing_fields,
            "requires_confirmation": proposal_type in {"tool_preview", "commitment_draft"},
            "risk_notes": [
                "该 proposal 在被 SlowTask 接受前不是 SemanticCommitment。",
                "该 proposal 不推进 plan_version，也不授权工具执行。",
            ],
            "source_evidence_refs": list(source_refs),
            "safety": {
                "codex_is_fact_owner": False,
                "advances_plan_version": False,
                "authorizes_tool": False,
                "contains_secret": False,
                "mutates_task_snapshot": False,
                "emits_canonical_event": False,
                "executes_external_tool": False,
                "contains_raw_provider_body": False,
            },
        }


class UnavailableWorkbenchCodexProposalProvider:
    """Explicit placeholder for a future approved Codex-backed provider."""

    def propose(self, request: WorkbenchCodexProposalRequest) -> Mapping[str, Any]:
        raise WorkbenchCodexProposalError(
            "Codex-backed proposal provider is not configured; use provider_mode=fake"
        )


class WorkbenchCodexProposalBridge:
    def __init__(self, provider: WorkbenchCodexProposalProvider) -> None:
        self._provider = provider

    def request_proposal(self, request: WorkbenchCodexProposalRequest) -> dict[str, Any]:
        try:
            _validate_request(request)
            draft = self._provider.propose(request)
            return validate_workbench_codex_proposal(
                draft,
                snapshot=request.snapshot,
                requested_source_evidence_refs=request.source_evidence_refs,
            )
        except WorkbenchCodexProposalError as exc:
            return build_rejected_workbench_codex_proposal(
                reason=str(exc),
                proposal_type=request.proposal_type,
                source_evidence_refs=request.source_evidence_refs,
            )


def request_workbench_codex_proposal(
    *,
    snapshot: Mapping[str, Any],
    intent: str,
    proposal_type: str,
    source_evidence_refs: Sequence[str],
    provider_mode: str = "fake",
) -> dict[str, Any]:
    bridge = WorkbenchCodexProposalBridge(_provider_for_mode(provider_mode))
    return bridge.request_proposal(
        WorkbenchCodexProposalRequest(
            snapshot=deepcopy(dict(snapshot)),
            intent=intent,
            proposal_type=proposal_type,
            source_evidence_refs=_non_empty_safe_string_tuple(
                source_evidence_refs,
                "source_evidence_refs",
            ),
        )
    )


def validate_workbench_codex_proposal(
    proposal: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    requested_source_evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise WorkbenchCodexProposalError("proposal must be a JSON object")
    normalized = deepcopy(dict(proposal))
    _validate_json_safe_value(normalized, path=())
    _reject_forbidden_keys(normalized, path=())
    _require_proposal_fields(normalized)
    _validate_proposal_identity(normalized)
    _validate_proposal_lists(normalized)
    _validate_source_evidence_refs(
        normalized,
        snapshot=snapshot,
        requested_source_evidence_refs=requested_source_evidence_refs,
    )
    normalized["safety"] = _validate_safety(normalized["safety"])
    normalized["status"] = "validated"
    return normalized


def mark_workbench_codex_proposal_status(
    proposal: Mapping[str, Any],
    *,
    status: str,
) -> dict[str, Any]:
    if status not in FINAL_PROPOSAL_STATUSES or status in PROVIDER_DRAFT_STATUSES:
        raise WorkbenchCodexProposalError("proposal status transition must be accepted or rejected")
    validated = validate_workbench_codex_proposal(proposal, snapshot={})
    validated["status"] = status
    validated["fact_owner"] = "slowtask_event_journal"
    validated["mutates_task_snapshot"] = False
    return validated


def build_rejected_workbench_codex_proposal(
    *,
    reason: str,
    proposal_type: str,
    source_evidence_refs: Sequence[str],
) -> dict[str, Any]:
    safe_type = proposal_type if proposal_type in ALLOWED_PROPOSAL_TYPES else "evidence_review"
    safe_refs = _safe_refs_or_empty(source_evidence_refs)
    safe_reason = _safe_rejection_reason(reason)
    return {
        "proposal_id": "proposal_rejected",
        "proposal_type": safe_type,
        "status": "rejected",
        "summary": "Proposal 被后端校验拒绝。",
        "suggested_next_steps": [],
        "missing_fields": [],
        "requires_confirmation": False,
        "risk_notes": [safe_reason],
        "source_evidence_refs": list(safe_refs),
        "safety": {
            "codex_is_fact_owner": False,
            "advances_plan_version": False,
            "authorizes_tool": False,
            "contains_secret": False,
            "mutates_task_snapshot": False,
            "emits_canonical_event": False,
            "executes_external_tool": False,
            "contains_raw_provider_body": False,
        },
    }


def _provider_for_mode(provider_mode: str) -> WorkbenchCodexProposalProvider:
    if provider_mode not in ALLOWED_PROVIDER_MODES:
        raise WorkbenchCodexProposalError("provider_mode must be fake or codex_unavailable")
    if provider_mode == "fake":
        return FakeWorkbenchCodexProposalProvider()
    return UnavailableWorkbenchCodexProposalProvider()


def _validate_request(request: WorkbenchCodexProposalRequest) -> None:
    if not isinstance(request.snapshot, Mapping):
        raise WorkbenchCodexProposalError("snapshot must be a JSON object")
    _validate_json_safe_value(request.snapshot, path=("snapshot",))
    if not isinstance(request.intent, str) or not request.intent:
        raise WorkbenchCodexProposalError("intent must be a non-empty string")
    _validate_safe_string(request.intent, path=("intent",))
    _proposal_type(request.proposal_type)
    _non_empty_safe_string_tuple(request.source_evidence_refs, "source_evidence_refs")


def _require_proposal_fields(proposal: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_PROPOSAL_FIELDS if field not in proposal]
    if missing:
        raise WorkbenchCodexProposalError(f"proposal missing required fields: {missing}")


def _validate_proposal_identity(proposal: Mapping[str, Any]) -> None:
    proposal_id = proposal.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id or not SAFE_ID_PATTERN.match(proposal_id):
        raise WorkbenchCodexProposalError("proposal_id must be a safe non-empty identifier")
    _proposal_type(proposal.get("proposal_type"))
    status = proposal.get("status")
    if status not in PROVIDER_DRAFT_STATUSES:
        raise WorkbenchCodexProposalError("provider proposal status must be draft or validated")
    summary = proposal.get("summary")
    if not isinstance(summary, str) or not summary:
        raise WorkbenchCodexProposalError("summary must be a non-empty string")
    if not isinstance(proposal.get("requires_confirmation"), bool):
        raise WorkbenchCodexProposalError("requires_confirmation must be a boolean")


def _validate_proposal_lists(proposal: Mapping[str, Any]) -> None:
    for field in ("suggested_next_steps", "missing_fields", "risk_notes", "source_evidence_refs"):
        _safe_string_list(proposal.get(field), field)


def _validate_source_evidence_refs(
    proposal: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    requested_source_evidence_refs: Sequence[str],
) -> None:
    source_refs = _non_empty_safe_string_tuple(proposal.get("source_evidence_refs", ()), "source_evidence_refs")
    requested_refs = set(_safe_refs_or_empty(requested_source_evidence_refs))
    if requested_refs and not set(source_refs).issubset(requested_refs):
        raise WorkbenchCodexProposalError("proposal source_evidence_refs must come from the request")
    snapshot_refs = _collect_snapshot_evidence_refs(snapshot)
    if snapshot_refs and not set(source_refs).issubset(snapshot_refs):
        raise WorkbenchCodexProposalError("proposal source_evidence_refs must exist in snapshot evidence")


def _validate_safety(safety: Any) -> dict[str, bool]:
    if not isinstance(safety, Mapping):
        raise WorkbenchCodexProposalError("safety must be a JSON object")
    normalized: dict[str, bool] = {}
    for field in NORMALIZED_SAFETY_FLAGS:
        value = safety.get(field, False)
        if not isinstance(value, bool):
            raise WorkbenchCodexProposalError(f"safety.{field} must be a boolean")
        normalized[field] = value
    unsafe_flags = [field for field, value in normalized.items() if value]
    if unsafe_flags:
        raise WorkbenchCodexProposalError(f"proposal unsafe safety flags set: {unsafe_flags}")
    for field in REQUIRED_SAFETY_FLAGS:
        if field not in safety:
            raise WorkbenchCodexProposalError(f"safety missing required field: {field}")
    return normalized


def _reject_forbidden_keys(value: Any, *, path: tuple[str, ...]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise WorkbenchCodexProposalError("proposal keys must be strings")
            normalized_key = key.lower()
            if normalized_key in FORBIDDEN_PROPOSAL_KEYS:
                label = ".".join((*path, key))
                raise WorkbenchCodexProposalError(f"forbidden proposal field: {label}")
            if key == "event_name" and child in FORBIDDEN_EVENT_NAMES:
                raise WorkbenchCodexProposalError("proposal must not emit canonical events")
            _reject_forbidden_keys(child, path=(*path, key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, path=(*path, str(index)))


def _validate_json_safe_value(value: Any, *, path: tuple[str, ...]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise WorkbenchCodexProposalError("proposal keys must be strings")
            _validate_safe_key(key, path=(*path, key))
            _validate_json_safe_value(child, path=(*path, key))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _validate_json_safe_value(child, path=(*path, str(index)))
        return
    if isinstance(value, str):
        _validate_safe_string(value, path=path)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise WorkbenchCodexProposalError("proposal contains unsupported non-JSON value")


def _validate_safe_string(value: str, *, path: tuple[str, ...]) -> None:
    lowered = value.lower()
    label = ".".join(path) or "value"
    if CREDENTIAL_LIKE_REF_PATTERN.search(value) or SECRET_VALUE_PATTERN.search(value):
        raise WorkbenchCodexProposalError(f"unsafe credential-like string at {label}")
    if LOCAL_ONLY_PATH_PATTERN.search(value) or any(marker in lowered for marker in UNSAFE_STRING_MARKERS):
        raise WorkbenchCodexProposalError(f"unsafe local/raw string at {label}")


def _validate_safe_key(value: str, *, path: tuple[str, ...]) -> None:
    lowered = value.lower()
    label = ".".join(path) or "key"
    local_key_markers = ("file://", "/users/", "\\users\\", "/private/", ".env")
    if CREDENTIAL_LIKE_REF_PATTERN.search(value) or SECRET_VALUE_PATTERN.search(value):
        raise WorkbenchCodexProposalError(f"unsafe credential-like key at {label}")
    if LOCAL_ONLY_PATH_PATTERN.search(value) or any(marker in lowered for marker in local_key_markers):
        raise WorkbenchCodexProposalError(f"unsafe local/raw key at {label}")


def _proposal_type(value: Any) -> str:
    if not isinstance(value, str) or value not in ALLOWED_PROPOSAL_TYPES:
        raise WorkbenchCodexProposalError(
            f"proposal_type must be one of {sorted(ALLOWED_PROPOSAL_TYPES)}"
        )
    return value


def _safe_string_list(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise WorkbenchCodexProposalError(f"{field} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise WorkbenchCodexProposalError(f"{field}[{index}] must be a string")
        _validate_safe_string(item, path=(field, str(index)))
        result.append(item)
    return result


def _non_empty_safe_string_tuple(value: Any, field: str) -> tuple[str, ...]:
    result = tuple(_safe_string_list(value, field))
    if not result:
        raise WorkbenchCodexProposalError(f"{field} must not be empty")
    return result


def _safe_refs_or_empty(value: Sequence[str]) -> tuple[str, ...]:
    try:
        return tuple(_safe_string_list(value, "source_evidence_refs"))
    except WorkbenchCodexProposalError:
        return ()


def _safe_rejection_reason(reason: str) -> str:
    if not reason:
        return "Proposal 被后端校验拒绝。"
    lowered = reason.lower()
    unsafe_reason_markers = (
        "file://",
        "/users/",
        "\\users\\",
        "/private/",
        ".env",
        "audio/raw/",
        "diagnostics/",
        "traces/",
        "replays/local/",
        "authorization:",
        "cookie:",
        "bearer ",
    )
    if CREDENTIAL_LIKE_REF_PATTERN.search(reason) or SECRET_VALUE_PATTERN.search(reason):
        return "Proposal 被拒绝，原因包含不安全内容，已省略。"
    if LOCAL_ONLY_PATH_PATTERN.search(reason) or any(marker in lowered for marker in unsafe_reason_markers):
        return "Proposal 被拒绝，原因包含不安全内容，已省略。"
    return reason


def _collect_snapshot_evidence_refs(snapshot: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in {"evidence_id", "event_id"} and isinstance(child, str):
                    refs.add(child)
                elif key in {
                    "source_evidence_refs",
                    "authoritative_evidence_refs",
                    "non_authoritative_hypothesis_refs",
                }:
                    for item in _iter_strings(child):
                        refs.add(item)
                visit(child)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value:
                visit(child)

    visit(snapshot)
    return refs


def _iter_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


CodexProposalError = WorkbenchCodexProposalError
CodexProposalRequest = WorkbenchCodexProposalRequest
CodexProposalProvider = WorkbenchCodexProposalProvider
FakeCodexProposalProvider = FakeWorkbenchCodexProposalProvider
UnavailableCodexProposalProvider = UnavailableWorkbenchCodexProposalProvider
CodexProposalBridge = WorkbenchCodexProposalBridge
request_codex_proposal = request_workbench_codex_proposal
validate_codex_proposal = validate_workbench_codex_proposal
mark_codex_proposal_status = mark_workbench_codex_proposal_status

__all__ = [
    "CodexProposalBridge",
    "CodexProposalError",
    "CodexProposalProvider",
    "CodexProposalRequest",
    "FakeCodexProposalProvider",
    "UnavailableCodexProposalProvider",
    "WorkbenchCodexProposalBridge",
    "WorkbenchCodexProposalError",
    "WorkbenchCodexProposalProvider",
    "WorkbenchCodexProposalRequest",
    "FakeWorkbenchCodexProposalProvider",
    "UnavailableWorkbenchCodexProposalProvider",
    "build_rejected_workbench_codex_proposal",
    "mark_codex_proposal_status",
    "mark_workbench_codex_proposal_status",
    "request_codex_proposal",
    "request_workbench_codex_proposal",
    "validate_codex_proposal",
    "validate_workbench_codex_proposal",
]
