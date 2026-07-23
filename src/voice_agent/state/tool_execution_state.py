from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


class ToolExecutionStateError(ValueError):
    pass


TOOL_EXECUTION_EVENT_NAMES = frozenset(
    {
        "TOOL_CALL_STARTED",
        "TOOL_MANIFEST_LOADED",
        "TOOL_ARGUMENTS_PARTIAL",
        "TOOL_ARGUMENTS_READY",
        "TOOL_PREVIEW_AVAILABLE",
        "TOOL_EXECUTION_AUTHORIZED",
        "TOOL_EXECUTION_STARTED",
        "TOOL_PROGRESS_UPDATED",
        "TOOL_UI_STATE_PATCHED",
        "TOOL_RESULT_RECEIVED",
        "TOOL_EXECUTION_FAILED",
        "TOOL_CALL_RETRYING",
        "TOOL_EXECUTION_CANCEL_REQUESTED",
        "TOOL_EXECUTION_CANCELLED",
        "TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS",
    }
)
CALL_EVENT_STATUS = {
    "TOOL_CALL_STARTED": "CALL_STARTED",
    "TOOL_ARGUMENTS_PARTIAL": "ARGUMENTS_PARTIAL",
    "TOOL_ARGUMENTS_READY": "ARGUMENTS_READY",
    "TOOL_PREVIEW_AVAILABLE": "PREVIEW_AVAILABLE",
    "TOOL_EXECUTION_AUTHORIZED": "AUTHORIZED",
    "TOOL_EXECUTION_STARTED": "EXECUTING",
    "TOOL_PROGRESS_UPDATED": "EXECUTING",
    "TOOL_UI_STATE_PATCHED": "UI_STATE_PATCHED",
    "TOOL_RESULT_RECEIVED": "RESULT_RECEIVED",
    "TOOL_EXECUTION_FAILED": "FAILED",
    "TOOL_CALL_RETRYING": "RETRYING",
    "TOOL_EXECUTION_CANCEL_REQUESTED": "CANCEL_REQUESTED",
    "TOOL_EXECUTION_CANCELLED": "CANCELLED",
    "TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS": "BLOCKED_INSUFFICIENT_ARGUMENTS",
}


@dataclass(frozen=True)
class ToolManifestRecord:
    event_id: str
    tool_name: str
    tool_adapter_id: str
    tool_manifest_version: str
    side_effect_class: str
    risk_class: str | None = None


@dataclass(frozen=True)
class ToolEventRecord:
    event_id: str
    event_name: str
    task_id: str
    plan_version: int
    task_event_seq: int


@dataclass(frozen=True)
class PartialArgumentsRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    partial_arguments_ref: str
    missing_fields: tuple[str, ...]


@dataclass(frozen=True)
class ReadyArgumentsRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    resolved_arguments_ref: str
    provenance_ref: str


@dataclass(frozen=True)
class PreviewRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    preview_ref: str
    requires_confirmation: bool


@dataclass(frozen=True)
class AuthorizationRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    authorization_basis: str
    confirmation_id: str | None = None


@dataclass(frozen=True)
class ExecutionStartedRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    idempotency_key: str
    authorization_event_id: str | None = None


@dataclass(frozen=True)
class ProgressRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    progress_type: str
    progress_ref: str


@dataclass(frozen=True)
class UIPatchRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    ui_patch_id: str
    idempotency_key: str
    patch_ref: str


@dataclass(frozen=True)
class ResultRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    result_status: str
    result_ref: str
    trust_level: str | None = None
    source_type: str | None = None


@dataclass(frozen=True)
class FailureRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    failure_reason: str
    retryable: bool


@dataclass(frozen=True)
class RetryRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    retry_count: int
    retry_reason: str


@dataclass(frozen=True)
class CancelRequestRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    cancel_reason: str


@dataclass(frozen=True)
class CancellationRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    cancel_request_event_id: str
    cancel_status: str


@dataclass(frozen=True)
class BlockedInsufficientArgumentsRecord:
    event_id: str
    task_id: str
    plan_version: int
    task_event_seq: int
    blocking_fields: tuple[str, ...]
    source_event_id: str


@dataclass
class ToolCallRecord:
    tool_call_id: str
    task_id: str | None = None
    plan_version: int | None = None
    current_task_event_seq: int | None = None
    lifecycle_status: str = "RECORDED"
    tool_name: str | None = None
    tool_adapter_id: str | None = None
    idempotency_key: str | None = None
    events: tuple[ToolEventRecord, ...] = ()
    partial_arguments: tuple[PartialArgumentsRecord, ...] = ()
    ready_arguments: tuple[ReadyArgumentsRecord, ...] = ()
    preview_events: tuple[PreviewRecord, ...] = ()
    authorizations: tuple[AuthorizationRecord, ...] = ()
    execution_started: tuple[ExecutionStartedRecord, ...] = ()
    progress_updates: tuple[ProgressRecord, ...] = ()
    ui_patches: tuple[UIPatchRecord, ...] = ()
    results: tuple[ResultRecord, ...] = ()
    failures: tuple[FailureRecord, ...] = ()
    retries: tuple[RetryRecord, ...] = ()
    cancel_requests: tuple[CancelRequestRecord, ...] = ()
    cancellations: tuple[CancellationRecord, ...] = ()
    blocked_events: tuple[BlockedInsufficientArgumentsRecord, ...] = ()
    last_tool_event_id: str | None = None


@dataclass
class ToolExecutionState:
    tool_manifests: dict[str, ToolManifestRecord] = field(default_factory=dict)
    tool_calls: dict[str, ToolCallRecord] = field(default_factory=dict)
    latest_task_event_seq_by_task_id: dict[str, int] = field(default_factory=dict)
    last_tool_event_id: str | None = None

    def reduce_event(self, event: Mapping[str, Any]) -> bool:
        event_name = str(event["event_name"])
        if event_name not in TOOL_EXECUTION_EVENT_NAMES:
            return False

        if event_name == "TOOL_MANIFEST_LOADED":
            self._handle_manifest_loaded(event)
            self.last_tool_event_id = str(event["event_id"])
            return True

        call = self._ensure_call(event)
        self._record_call_event(call, event)
        if event_name == "TOOL_CALL_STARTED":
            self._handle_call_started(call, event)
        elif event_name == "TOOL_ARGUMENTS_PARTIAL":
            self._handle_arguments_partial(call, event)
        elif event_name == "TOOL_ARGUMENTS_READY":
            self._handle_arguments_ready(call, event)
        elif event_name == "TOOL_PREVIEW_AVAILABLE":
            self._handle_preview_available(call, event)
        elif event_name == "TOOL_EXECUTION_AUTHORIZED":
            self._handle_execution_authorized(call, event)
        elif event_name == "TOOL_EXECUTION_STARTED":
            self._handle_execution_started(call, event)
        elif event_name == "TOOL_PROGRESS_UPDATED":
            self._handle_progress_updated(call, event)
        elif event_name == "TOOL_UI_STATE_PATCHED":
            self._handle_ui_state_patched(call, event)
        elif event_name == "TOOL_RESULT_RECEIVED":
            self._handle_result_received(call, event)
        elif event_name == "TOOL_EXECUTION_FAILED":
            self._handle_execution_failed(call, event)
        elif event_name == "TOOL_CALL_RETRYING":
            self._handle_call_retrying(call, event)
        elif event_name == "TOOL_EXECUTION_CANCEL_REQUESTED":
            self._handle_cancel_requested(call, event)
        elif event_name == "TOOL_EXECUTION_CANCELLED":
            self._handle_execution_cancelled(call, event)
        elif event_name == "TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS":
            self._handle_blocked_insufficient_arguments(call, event)

        call.lifecycle_status = CALL_EVENT_STATUS[event_name]
        call.last_tool_event_id = str(event["event_id"])
        self.last_tool_event_id = str(event["event_id"])
        return True

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "tool_manifests": {
                tool_name: asdict(self.tool_manifests[tool_name])
                for tool_name in sorted(self.tool_manifests)
            },
            "tool_calls": {
                tool_call_id: asdict(self.tool_calls[tool_call_id])
                for tool_call_id in sorted(self.tool_calls)
            },
            "latest_task_event_seq_by_task_id": dict(sorted(self.latest_task_event_seq_by_task_id.items())),
            "last_tool_event_id": self.last_tool_event_id,
        }

    def _handle_manifest_loaded(self, event: Mapping[str, Any]) -> None:
        tool_name = str(event["tool_name"])
        self.tool_manifests[tool_name] = ToolManifestRecord(
            event_id=str(event["event_id"]),
            tool_name=tool_name,
            tool_adapter_id=str(event["tool_adapter_id"]),
            tool_manifest_version=str(event["tool_manifest_version"]),
            side_effect_class=str(event["side_effect_class"]),
            risk_class=_optional_str(event.get("risk_class")),
        )

    def _ensure_call(self, event: Mapping[str, Any]) -> ToolCallRecord:
        tool_call_id = str(event["tool_call_id"])
        call = self.tool_calls.get(tool_call_id)
        if call is None:
            call = ToolCallRecord(tool_call_id=tool_call_id)
            self.tool_calls[tool_call_id] = call
        self._preserve_task_binding(call, event)
        return call

    def _preserve_task_binding(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        task_id = str(event["task_id"])
        task_event_seq = _int_field(event, "task_event_seq")
        if call.task_id is not None and call.task_id != task_id:
            raise ToolExecutionStateError("tool_call_id cannot move between task_id values")
        latest_task_event_seq = self.latest_task_event_seq_by_task_id.get(task_id)
        if latest_task_event_seq is not None and task_event_seq <= latest_task_event_seq:
            raise ToolExecutionStateError("task_event_seq must increase monotonically per task_id")
        if call.current_task_event_seq is not None and task_event_seq <= call.current_task_event_seq:
            raise ToolExecutionStateError("tool_call_id task_event_seq must increase monotonically")
        call.task_id = task_id
        if call.plan_version is None:
            call.plan_version = _int_field(event, "plan_version")
        call.current_task_event_seq = task_event_seq
        self.latest_task_event_seq_by_task_id[task_id] = task_event_seq

    def _record_call_event(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.events = (
            *call.events,
            ToolEventRecord(
                event_id=str(event["event_id"]),
                event_name=str(event["event_name"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
            ),
        )

    def _handle_call_started(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        if call.tool_name is not None:
            raise ToolExecutionStateError("Duplicate TOOL_CALL_STARTED for tool_call_id")
        call.tool_name = str(event["tool_name"])
        call.tool_adapter_id = _optional_str(event.get("tool_adapter_id"))
        call.idempotency_key = str(event["idempotency_key"])

    def _handle_arguments_partial(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.partial_arguments = (
            *call.partial_arguments,
            PartialArgumentsRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                partial_arguments_ref=str(event["partial_arguments_ref"]),
                missing_fields=_string_tuple(event.get("missing_fields", ())),
            ),
        )

    def _handle_arguments_ready(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.ready_arguments = (
            *call.ready_arguments,
            ReadyArgumentsRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                resolved_arguments_ref=str(event["resolved_arguments_ref"]),
                provenance_ref=str(event["provenance_ref"]),
            ),
        )

    def _handle_preview_available(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.preview_events = (
            *call.preview_events,
            PreviewRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                preview_ref=str(event["preview_ref"]),
                requires_confirmation=bool(event["requires_confirmation"]),
            ),
        )

    def _handle_execution_authorized(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.authorizations = (
            *call.authorizations,
            AuthorizationRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                authorization_basis=str(event["authorization_basis"]),
                confirmation_id=_optional_str(event.get("confirmation_id")),
            ),
        )

    def _handle_execution_started(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        authorization_event_id = event.get("authorization_event_id")
        if authorization_event_id in (None, ""):
            authorization_event_id = event.get("caused_by_event_id")
        if call.tool_name is None and event.get("tool_name") not in (None, ""):
            call.tool_name = str(event["tool_name"])
        call.execution_started = (
            *call.execution_started,
            ExecutionStartedRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                idempotency_key=str(event["idempotency_key"]),
                authorization_event_id=_optional_str(authorization_event_id),
            ),
        )

    def _handle_progress_updated(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.progress_updates = (
            *call.progress_updates,
            ProgressRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                progress_type=str(event["progress_type"]),
                progress_ref=str(event["progress_ref"]),
            ),
        )

    def _handle_ui_state_patched(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.ui_patches = (
            *call.ui_patches,
            UIPatchRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                ui_patch_id=str(event["ui_patch_id"]),
                idempotency_key=str(event["idempotency_key"]),
                patch_ref=str(event["patch_ref"]),
            ),
        )

    def _handle_result_received(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.results = (
            *call.results,
            ResultRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                result_status=str(event["result_status"]),
                result_ref=str(event["result_ref"]),
                trust_level=_optional_str(event.get("trust_level")),
                source_type=_optional_str(event.get("source_type")),
            ),
        )

    def _handle_execution_failed(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.failures = (
            *call.failures,
            FailureRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                failure_reason=str(event["failure_reason"]),
                retryable=bool(event["retryable"]),
            ),
        )

    def _handle_call_retrying(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.retries = (
            *call.retries,
            RetryRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                retry_count=_int_field(event, "retry_count"),
                retry_reason=str(event["retry_reason"]),
            ),
        )

    def _handle_cancel_requested(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.cancel_requests = (
            *call.cancel_requests,
            CancelRequestRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                cancel_reason=str(event["cancel_reason"]),
            ),
        )

    def _handle_execution_cancelled(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.cancellations = (
            *call.cancellations,
            CancellationRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                cancel_request_event_id=str(event["cancel_request_event_id"]),
                cancel_status=str(event["cancel_status"]),
            ),
        )

    def _handle_blocked_insufficient_arguments(self, call: ToolCallRecord, event: Mapping[str, Any]) -> None:
        call.blocked_events = (
            *call.blocked_events,
            BlockedInsufficientArgumentsRecord(
                event_id=str(event["event_id"]),
                task_id=str(event["task_id"]),
                plan_version=_int_field(event, "plan_version"),
                task_event_seq=_int_field(event, "task_event_seq"),
                blocking_fields=_string_tuple(event.get("blocking_fields", ())),
                source_event_id=str(event["source_event_id"]),
            ),
        )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_field(event: Mapping[str, Any], field: str) -> int:
    value = event[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolExecutionStateError(f"{field} must be an integer")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (str(value),)
    if not isinstance(value, (list, tuple)):
        raise ToolExecutionStateError("expected a list of string refs")
    return tuple(str(item) for item in value)
