from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from voice_agent.demo_backend.in_memory import DemoBackendExecutionError, InMemoryDemoBackend
from voice_agent.events.journal import InMemoryEventJournal
from voice_agent.tools.manifest import ToolExecutionPolicyError, ToolManifest, require_mvp_side_effect_class
from voice_agent.tools.registry import ToolRegistry


TOOL_EXECUTOR_SOURCE_MODULE = "tool_executor"
TERMINAL_SLOWTASK_STATES = frozenset({"COMPLETED", "CANCELLED", "FAILED"})
DEFAULT_CONFIRMATION_SCOPE = "FINAL_ARGUMENT_CONFIRMATION"


@dataclass(frozen=True)
class ToolExecutionRequest:
    tool_call_id: str
    tool_name: str
    task_id: str
    plan_version: int
    current_plan_version: int
    start_task_event_seq: int
    caused_by_event_id: str
    event_id_prefix: str
    created_monotonic_ms: int
    created_wall_clock_ms: int
    idempotency_key: str
    arguments: Mapping[str, Any]
    argument_provenance: Mapping[str, str]
    resolved_arguments_ref: str
    provenance_ref: str
    partial_arguments_ref: str | None = None
    preview_ref: str | None = None
    accepted_confirmation_event_id: str | None = None
    accepted_confirmation_id: str | None = None
    accepted_confirmation_scope: str | None = None
    accepted_confirmation_plan_version: int | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    produced_events: tuple[dict[str, Any], ...]
    result_ref: str | None = None
    result_status: str | None = None
    blocking_fields: tuple[str, ...] = ()
    payload: Mapping[str, Any] | None = None


@dataclass
class _EmissionContext:
    request: ToolExecutionRequest
    produced_events: list[dict[str, Any]] = field(default_factory=list)
    next_task_event_seq: int = 0
    next_time_offset: int = 0
    caused_by_event_id: str = ""
    finished: bool = False
    created_monotonic_ms: int | None = None
    created_wall_clock_ms: int | None = None


@dataclass(frozen=True)
class ToolExecutionHandle:
    """A started sandbox call whose result can be completed later.

    The handle is intentionally control-plane local.  It carries the same
    task/plan binding as the start events and lets a session demonstrate a
    late result without inventing a second tool execution path.
    """

    request: ToolExecutionRequest
    manifest: ToolManifest
    context: _EmissionContext

    @property
    def next_task_event_seq(self) -> int:
        return self.context.next_task_event_seq

    @property
    def caused_by_event_id(self) -> str:
        return self.context.caused_by_event_id

    @property
    def task_id(self) -> str:
        return self.request.task_id

    @property
    def plan_version(self) -> int:
        return self.request.plan_version

    def record_external_event(self, event: Mapping[str, Any]) -> None:
        """Advance the handle cursor after a SlowTask event between phases."""

        if str(event.get("task_id")) != self.request.task_id:
            raise ToolExecutionPolicyError("external tool phase event task_id does not match handle")
        if int(event.get("plan_version", -1)) != self.request.plan_version:
            raise ToolExecutionPolicyError("external tool phase event plan_version does not match handle")
        task_event_seq = event.get("task_event_seq")
        if not isinstance(task_event_seq, int) or isinstance(task_event_seq, bool):
            raise ToolExecutionPolicyError("external tool phase event requires task_event_seq")
        if task_event_seq != self.context.next_task_event_seq:
            raise ToolExecutionPolicyError("external tool phase event must consume the next task_event_seq")
        self.context.next_task_event_seq = task_event_seq + 1
        self.context.caused_by_event_id = str(event["event_id"])

    def record_task_event(self, event: Mapping[str, Any]) -> None:
        """Advance the cursor across a task event, including a plan advance.

        A late tool result keeps the original ``plan_version`` while its
        ``task_event_seq`` must still follow every intervening UserPatch and
        replan event.  ``record_external_event`` remains strict for callers
        that need an unchanged plan binding; this method is the explicit
        cursor operation for the old-plan late-result path.
        """

        if str(event.get("task_id")) != self.request.task_id:
            raise ToolExecutionPolicyError("task event task_id does not match handle")
        task_event_seq = event.get("task_event_seq")
        if not isinstance(task_event_seq, int) or isinstance(task_event_seq, bool):
            raise ToolExecutionPolicyError("task event requires task_event_seq")
        if task_event_seq != self.context.next_task_event_seq:
            raise ToolExecutionPolicyError("task event must consume the next task_event_seq")
        self.context.next_task_event_seq = task_event_seq + 1
        self.context.caused_by_event_id = str(event["event_id"])


@dataclass(frozen=True)
class ToolExecutionStartResult:
    produced_events: tuple[dict[str, Any], ...]
    handle: ToolExecutionHandle | None = None
    blocking_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class _JournalTaskCursor:
    current_plan_version: int | None = None
    latest_task_event_seq: int | None = None
    lifecycle_state: str | None = None


class DemoToolExecutor:
    """Sandbox-only MVP-2 Tool Executor skeleton.

    It emits recorded tool events into the journal and delegates only to the
    deterministic in-memory demo backend after all policy gates pass.
    """

    def __init__(
        self,
        *,
        journal: InMemoryEventJournal,
        registry: ToolRegistry,
        backend: InMemoryDemoBackend,
    ) -> None:
        self._journal = journal
        self._registry = registry
        self._backend = backend

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        started = self.begin(request)
        if started.handle is None:
            return ToolExecutionResult(
                produced_events=started.produced_events,
                blocking_fields=started.blocking_fields,
            )
        return self.complete(started.handle)

    def begin(self, request: ToolExecutionRequest) -> ToolExecutionStartResult:
        """Emit authorization/start events without completing the backend call."""

        _validate_request_shape(request)
        manifest = self._registry.get(request.tool_name)
        _require_current_plan(request, self._journal.events())
        require_mvp_side_effect_class(manifest.side_effect_class)
        _require_unused_idempotency_key(request, manifest, self._journal.events())

        context = _EmissionContext(
            request=request,
            next_task_event_seq=request.start_task_event_seq,
            caused_by_event_id=request.caused_by_event_id,
        )
        self._append_manifest_loaded(context, manifest)

        missing_fields = _missing_argument_or_provenance_fields(
            request,
            manifest,
            self._journal.events(),
        )
        if missing_fields:
            self._append_arguments_partial(context, missing_fields)
            self._append_blocked_insufficient_arguments(context, missing_fields)
            return ToolExecutionStartResult(
                produced_events=tuple(context.produced_events),
                blocking_fields=missing_fields,
            )

        self._append_arguments_ready(context)
        if manifest.preview_required:
            self._append_preview_available(context, requires_confirmation=manifest.confirmation_required)
        authorization = self._append_execution_authorized_after_policy_gate(context, manifest)
        self._append_execution_started(context, authorization_event_id=str(authorization["event_id"]))

        return ToolExecutionStartResult(
            produced_events=tuple(context.produced_events),
            handle=ToolExecutionHandle(request=request, manifest=manifest, context=context),
        )

    def complete(
        self,
        handle: ToolExecutionHandle,
        *,
        created_monotonic_ms: int | None = None,
        created_wall_clock_ms: int | None = None,
    ) -> ToolExecutionResult:
        """Complete a previously authorized sandbox call.

        Completion deliberately does not re-check the current plan.  The
        original plan binding remains on the result; SlowTask decides whether
        it is current or stale after the result is journaled.
        """

        if handle.context.finished:
            raise ToolExecutionPolicyError("tool execution handle has already completed")
        context = handle.context
        manifest = handle.manifest
        if created_monotonic_ms is not None:
            context.created_monotonic_ms = created_monotonic_ms
        if created_wall_clock_ms is not None:
            context.created_wall_clock_ms = created_wall_clock_ms

        try:
            backend_result = self._backend.execute(
                tool_name=manifest.tool_name,
                tool_adapter_id=manifest.tool_adapter_id,
                arguments=context.request.arguments,
                idempotency_key=context.request.idempotency_key,
                expected_state_namespace=manifest.sandbox_state_namespace,
            )
        except DemoBackendExecutionError as exc:
            self._append_execution_failed(context, failure_reason=exc.reason, retryable=False)
            context.finished = True
            return ToolExecutionResult(
                produced_events=tuple(context.produced_events),
                result_status="FAILED",
            )

        return self.complete_with_backend_result(handle, backend_result)

    def complete_with_backend_result(
        self,
        handle: ToolExecutionHandle,
        backend_result: Any,
        *,
        created_monotonic_ms: int | None = None,
        created_wall_clock_ms: int | None = None,
    ) -> ToolExecutionResult:
        """Journal a normalized adapter result on the control-plane thread.

        A read-only adapter may perform its blocking I/O outside this executor.
        The caller returns to the async control plane before invoking this
        method, so critical journal writes remain serialized here.
        """

        context = handle.context
        manifest = handle.manifest
        if context.finished:
            raise ToolExecutionPolicyError("tool execution handle has already completed")
        if created_monotonic_ms is not None:
            context.created_monotonic_ms = created_monotonic_ms
        if created_wall_clock_ms is not None:
            context.created_wall_clock_ms = created_wall_clock_ms

        if manifest.ui_patch_capable and backend_result.ui_patch is not None:
            if backend_result.ui_patch.state_namespace != manifest.sandbox_state_namespace:
                self._append_execution_failed(
                    context,
                    failure_reason="demo_backend_ui_patch_namespace_mismatch",
                    retryable=False,
                )
                context.finished = True
                return ToolExecutionResult(
                    produced_events=tuple(context.produced_events),
                    result_status="FAILED",
                )
        self._append_progress_updated(
            context,
            progress_type=backend_result.progress_type,
            progress_ref=backend_result.progress_ref,
        )
        if manifest.ui_patch_capable and backend_result.ui_patch is not None:
            self._append_ui_state_patched(
                context,
                ui_patch_id=backend_result.ui_patch.ui_patch_id,
                patch_ref=backend_result.ui_patch.patch_ref,
            )
        self._append_result_received(
            context,
            result_status=backend_result.result_status,
            result_ref=backend_result.result_ref,
            trust_level=manifest.trust_level,
            source_type=manifest.source_type,
        )
        context.finished = True
        return ToolExecutionResult(
            produced_events=tuple(context.produced_events),
            result_ref=backend_result.result_ref,
            result_status=backend_result.result_status,
            payload=backend_result.payload,
        )

    def cancel(
        self,
        handle: ToolExecutionHandle,
        *,
        cancel_reason: str,
        created_monotonic_ms: int | None = None,
        created_wall_clock_ms: int | None = None,
    ) -> ToolExecutionResult:
        """Close an in-flight demo call through the canonical tool events."""

        if handle.context.finished:
            raise ToolExecutionPolicyError("tool execution handle has already completed")
        context = handle.context
        if not cancel_reason:
            raise ToolExecutionPolicyError("cancel_reason is required")
        if created_monotonic_ms is not None:
            context.created_monotonic_ms = created_monotonic_ms
        if created_wall_clock_ms is not None:
            context.created_wall_clock_ms = created_wall_clock_ms
        requested = self._append_event(
            context,
            event_name="TOOL_EXECUTION_CANCEL_REQUESTED",
            event_id=f"{context.request.event_id_prefix}_execution_cancel_requested",
            cancel_reason=cancel_reason,
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(requested["event_id"])
        cancelled = self._append_event(
            context,
            event_name="TOOL_EXECUTION_CANCELLED",
            event_id=f"{context.request.event_id_prefix}_execution_cancelled",
            cancel_request_event_id=str(requested["event_id"]),
            cancel_status="CANCELLED_BY_SLOWTASK",
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(cancelled["event_id"])
        context.finished = True
        return ToolExecutionResult(
            produced_events=tuple(context.produced_events),
            result_status="CANCELLED",
        )

    def _append_manifest_loaded(self, context: _EmissionContext, manifest: ToolManifest) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_MANIFEST_LOADED",
            event_id=f"{context.request.event_id_prefix}_manifest_loaded",
            include_task_binding=False,
            **manifest.manifest_event_fields(),
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_arguments_partial(self, context: _EmissionContext, missing_fields: tuple[str, ...]) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_ARGUMENTS_PARTIAL",
            event_id=f"{context.request.event_id_prefix}_arguments_partial",
            partial_arguments_ref=(
                context.request.partial_arguments_ref
                or f"args://synthetic/{context.request.event_id_prefix}/partial"
            ),
            missing_fields=list(missing_fields),
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_blocked_insufficient_arguments(
        self,
        context: _EmissionContext,
        blocking_fields: tuple[str, ...],
    ) -> None:
        partial_event_id = context.caused_by_event_id
        event = self._append_event(
            context,
            event_name="TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS",
            event_id=f"{context.request.event_id_prefix}_blocked_insufficient_arguments",
            blocking_fields=list(blocking_fields),
            source_event_id=partial_event_id,
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_arguments_ready(self, context: _EmissionContext) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_ARGUMENTS_READY",
            event_id=f"{context.request.event_id_prefix}_arguments_ready",
            resolved_arguments_ref=context.request.resolved_arguments_ref,
            provenance_ref=context.request.provenance_ref,
            argument_fingerprint=_argument_fingerprint(context.request.arguments),
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_preview_available(self, context: _EmissionContext, *, requires_confirmation: bool) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_PREVIEW_AVAILABLE",
            event_id=f"{context.request.event_id_prefix}_preview_available",
            preview_ref=context.request.preview_ref or f"preview://synthetic/{context.request.event_id_prefix}",
            requires_confirmation=requires_confirmation,
            argument_fingerprint=_argument_fingerprint(context.request.arguments),
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_execution_authorized_after_policy_gate(
        self,
        context: _EmissionContext,
        manifest: ToolManifest,
    ) -> dict[str, Any]:
        if _requires_current_plan_confirmation(manifest):
            _require_current_plan_confirmation(context.request, manifest)
            self._require_recorded_confirmation(context.request, manifest)
            return self._append_execution_authorized(
                context,
                authorization_basis="current_plan_confirmation_acceptance",
                confirmation_id=context.request.accepted_confirmation_id,
                caused_by_event_id=context.request.accepted_confirmation_event_id,
            )
        return self._append_execution_authorized(
            context,
            authorization_basis="current_plan_policy_allow",
            confirmation_id=None,
            caused_by_event_id=None,
        )

    def _require_recorded_confirmation(
        self,
        request: ToolExecutionRequest,
        manifest: ToolManifest,
    ) -> None:
        journal_events = self._journal.events()
        events_by_id = {str(event["event_id"]): event for event in journal_events}
        expected_scope = _expected_confirmation_scope(manifest)
        accepted = events_by_id.get(str(request.accepted_confirmation_event_id))
        required = _matching_confirmation_required(
            journal_events,
            request=request,
            confirmation_scope=expected_scope,
            before_event=accepted,
        )
        if (
            not _matches_confirmation_accepted(
                accepted,
                request=request,
                confirmation_scope=expected_scope,
            )
            or required is None
        ):
            raise ToolExecutionPolicyError(
                f"{manifest.tool_name} requires current-plan CONFIRMATION_ACCEPTED before tool authorization"
            )

        received = events_by_id.get(str(accepted.get("caused_by_event_id")))
        interpreted = events_by_id.get(str(received.get("caused_by_event_id"))) if received else None
        patch_received = events_by_id.get(str(interpreted.get("caused_by_event_id"))) if interpreted else None
        requires_destructive_chain = manifest.side_effect_class == "DEMO_DESTRUCTIVE_ACTION"
        waiting_for_confirmation = _matching_waiting_for_user_confirmation(
            journal_events,
            request=request,
            required=required,
            before_event=patch_received,
        ) if requires_destructive_chain else None
        if not requires_destructive_chain and patch_received is not None:
            waiting_for_confirmation = events_by_id.get(str(patch_received.get("caused_by_event_id")))
            if (
                waiting_for_confirmation is not None
                and waiting_for_confirmation.get("event_name") != "WAITING_FOR_USER_CONFIRMATION"
            ):
                waiting_for_confirmation = None
        if not _matches_confirmation_chain(
            required=required,
            waiting_for_confirmation=waiting_for_confirmation,
            patch_received=patch_received,
            interpreted=interpreted,
            received=received,
            accepted=accepted,
            request=request,
            confirmation_scope=expected_scope,
            require_waiting_for_confirmation=requires_destructive_chain,
            events_by_id=events_by_id,
        ):
            raise ToolExecutionPolicyError(
                f"{manifest.tool_name} requires current-plan CONFIRMATION_ACCEPTED before tool authorization"
            )
        if requires_destructive_chain:
            _require_confirmation_binds_pending_tool_request(
                required,
                request=request,
                manifest=manifest,
                events_by_id=events_by_id,
            )

    def _append_execution_authorized(
        self,
        context: _EmissionContext,
        *,
        authorization_basis: str,
        confirmation_id: str | None,
        caused_by_event_id: str | None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "authorization_basis": authorization_basis,
            "tool_name": context.request.tool_name,
        }
        if confirmation_id is not None:
            fields["confirmation_id"] = confirmation_id
        event = self._append_event(
            context,
            event_name="TOOL_EXECUTION_AUTHORIZED",
            event_id=f"{context.request.event_id_prefix}_execution_authorized",
            caused_by_event_id=caused_by_event_id,
            **fields,
        )
        context.caused_by_event_id = str(event["event_id"])
        return event

    def _append_execution_started(self, context: _EmissionContext, *, authorization_event_id: str) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_EXECUTION_STARTED",
            event_id=f"{context.request.event_id_prefix}_execution_started",
            idempotency_key=context.request.idempotency_key,
            authorization_event_id=authorization_event_id,
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_progress_updated(
        self,
        context: _EmissionContext,
        *,
        progress_type: str,
        progress_ref: str,
    ) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_PROGRESS_UPDATED",
            event_id=f"{context.request.event_id_prefix}_progress_updated",
            progress_type=progress_type,
            progress_ref=progress_ref,
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_ui_state_patched(
        self,
        context: _EmissionContext,
        *,
        ui_patch_id: str,
        patch_ref: str,
    ) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_UI_STATE_PATCHED",
            event_id=f"{context.request.event_id_prefix}_ui_state_patched",
            ui_patch_id=ui_patch_id,
            idempotency_key=context.request.idempotency_key,
            patch_ref=patch_ref,
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_result_received(
        self,
        context: _EmissionContext,
        *,
        result_status: str,
        result_ref: str,
        trust_level: str,
        source_type: str,
    ) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_RESULT_RECEIVED",
            event_id=f"{context.request.event_id_prefix}_result_received",
            result_status=result_status,
            result_ref=result_ref,
            trust_level=trust_level,
            source_type=source_type,
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_execution_failed(
        self,
        context: _EmissionContext,
        *,
        failure_reason: str,
        retryable: bool,
    ) -> None:
        event = self._append_event(
            context,
            event_name="TOOL_EXECUTION_FAILED",
            event_id=f"{context.request.event_id_prefix}_execution_failed",
            failure_reason=failure_reason,
            retryable=retryable,
            tool_name=context.request.tool_name,
        )
        context.caused_by_event_id = str(event["event_id"])

    def _append_event(
        self,
        context: _EmissionContext,
        *,
        event_name: str,
        event_id: str,
        include_task_binding: bool = True,
        caused_by_event_id: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        event_fields = dict(fields)
        if include_task_binding:
            event_fields.update(
                {
                    "tool_call_id": context.request.tool_call_id,
                    "task_id": context.request.task_id,
                    "plan_version": context.request.plan_version,
                    "task_event_seq": context.next_task_event_seq,
                }
            )
            context.next_task_event_seq += 1

        event = self._journal.append(
            event_name=event_name,
            event_id=event_id,
            source_module=TOOL_EXECUTOR_SOURCE_MODULE,
            caused_by_event_id=caused_by_event_id or context.caused_by_event_id,
            created_monotonic_ms=(
                context.created_monotonic_ms
                if context.created_monotonic_ms is not None
                else context.request.created_monotonic_ms
            ) + context.next_time_offset,
            created_wall_clock_ms=(
                context.created_wall_clock_ms
                if context.created_wall_clock_ms is not None
                else context.request.created_wall_clock_ms
            ) + context.next_time_offset,
            trace_redaction_level="metadata_only",
            **event_fields,
        )
        context.next_time_offset += 1
        context.produced_events.append(event)
        return event


def _validate_request_shape(request: ToolExecutionRequest) -> None:
    for field_name in (
        "tool_call_id",
        "tool_name",
        "task_id",
        "caused_by_event_id",
        "event_id_prefix",
        "idempotency_key",
        "resolved_arguments_ref",
        "provenance_ref",
    ):
        if not str(getattr(request, field_name)):
            raise ToolExecutionPolicyError(f"{field_name} is required")
    if request.plan_version < 1:
        raise ToolExecutionPolicyError("plan_version must be positive")
    if request.current_plan_version < 1:
        raise ToolExecutionPolicyError("current_plan_version must be positive")
    if request.start_task_event_seq < 1:
        raise ToolExecutionPolicyError("start_task_event_seq must be positive")


def _require_current_plan(
    request: ToolExecutionRequest,
    journal_events: Sequence[Mapping[str, Any]],
) -> None:
    if request.plan_version != request.current_plan_version:
        raise ToolExecutionPolicyError(
            "tool request plan_version must match current plan_version before execution"
        )
    cursor = _journal_task_cursor(
        journal_events,
        task_id=request.task_id,
    )
    if cursor.current_plan_version is None:
        raise ToolExecutionPolicyError(
            "tool request requires journal current plan_version before execution"
        )
    if request.plan_version != cursor.current_plan_version:
        raise ToolExecutionPolicyError(
            "tool request plan_version must match journal current plan_version before execution"
        )
    if cursor.lifecycle_state in TERMINAL_SLOWTASK_STATES:
        raise ToolExecutionPolicyError("terminal SlowTask cannot execute tools")
    if (
        cursor.latest_task_event_seq is not None
        and request.start_task_event_seq <= cursor.latest_task_event_seq
    ):
        raise ToolExecutionPolicyError(
            "tool request start_task_event_seq must follow journal task_event_seq cursor"
        )


def _require_unused_idempotency_key(
    request: ToolExecutionRequest,
    manifest: ToolManifest,
    journal_events: Sequence[Mapping[str, Any]],
) -> None:
    if not manifest.idempotency_required:
        return
    for event in journal_events:
        if (
            event.get("event_name") == "TOOL_EXECUTION_STARTED"
            and event.get("idempotency_key") == request.idempotency_key
        ):
            raise ToolExecutionPolicyError(
                "idempotency_key already has a recorded TOOL_EXECUTION_STARTED"
            )


def _requires_current_plan_confirmation(manifest: ToolManifest) -> bool:
    return manifest.confirmation_required or manifest.side_effect_class == "DEMO_DESTRUCTIVE_ACTION"


def _require_current_plan_confirmation(
    request: ToolExecutionRequest,
    manifest: ToolManifest,
) -> None:
    if (
        request.accepted_confirmation_event_id in (None, "")
        or request.accepted_confirmation_id in (None, "")
        or request.accepted_confirmation_scope in (None, "")
        or request.accepted_confirmation_plan_version != request.plan_version
        or request.accepted_confirmation_scope != _expected_confirmation_scope(manifest)
    ):
        raise ToolExecutionPolicyError(
            f"{manifest.tool_name} requires current-plan CONFIRMATION_ACCEPTED before tool authorization"
        )


def _expected_confirmation_scope(manifest: ToolManifest) -> str:
    if manifest.side_effect_class == "DEMO_DESTRUCTIVE_ACTION":
        return "DEMO_DESTRUCTIVE_ACTION"
    return DEFAULT_CONFIRMATION_SCOPE


def _matches_confirmation_accepted(
    event: Mapping[str, Any] | None,
    *,
    request: ToolExecutionRequest,
    confirmation_scope: str,
) -> bool:
    return bool(
        event is not None
        and event.get("event_name") == "CONFIRMATION_ACCEPTED"
        and event.get("task_id") == request.task_id
        and event.get("plan_version") == request.plan_version
        and event.get("confirmation_id") == request.accepted_confirmation_id
        and event.get("accepted_scope") == confirmation_scope
    )


def _matching_confirmation_required(
    journal_events: Sequence[Mapping[str, Any]],
    *,
    request: ToolExecutionRequest,
    confirmation_scope: str,
    before_event: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if before_event is None:
        return None
    accepted_event_seq = _int_value(before_event.get("event_seq"))
    matching_required: Mapping[str, Any] | None = None
    for event in journal_events:
        if (
            event.get("event_name") == "CONFIRMATION_REQUIRED"
            and event.get("task_id") == request.task_id
            and event.get("plan_version") == request.plan_version
            and event.get("confirmation_id") == request.accepted_confirmation_id
            and event.get("confirmation_scope") == confirmation_scope
            and event.get("required_for_event_id") == request.caused_by_event_id
            and event.get("caused_by_event_id") == request.caused_by_event_id
            and _event_seq_before(event, accepted_event_seq)
        ):
            matching_required = event
    return matching_required


def _matching_waiting_for_user_confirmation(
    journal_events: Sequence[Mapping[str, Any]],
    *,
    request: ToolExecutionRequest,
    required: Mapping[str, Any],
    before_event: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if before_event is None:
        return None
    before_event_seq = _int_value(before_event.get("event_seq"))
    matching_waiting: Mapping[str, Any] | None = None
    for event in journal_events:
        if (
            _matches_waiting_for_user_confirmation(
                event,
                request=request,
                confirmation_id=required.get("confirmation_id"),
            )
            and event.get("caused_by_event_id") == required.get("event_id")
            and _strict_event_seq_order(required, event)
            and _event_seq_before(event, before_event_seq)
        ):
            matching_waiting = event
    return matching_waiting


def _require_confirmation_binds_pending_tool_request(
    required: Mapping[str, Any],
    *,
    request: ToolExecutionRequest,
    manifest: ToolManifest,
    events_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    required_for_event_id = required.get("required_for_event_id")
    required_for_event = (
        events_by_id.get(str(required_for_event_id))
        if required_for_event_id not in (None, "")
        else None
    )
    if (
        required_for_event is None
        or required_for_event.get("event_name") != "TOOL_PREVIEW_AVAILABLE"
        or required_for_event.get("task_id") != request.task_id
        or required_for_event.get("plan_version") != request.plan_version
        or required_for_event.get("tool_call_id") != request.tool_call_id
        or required_for_event.get("tool_name") != manifest.tool_name
    ):
        raise ToolExecutionPolicyError(
            f"{manifest.tool_name} confirmation must bind the pending tool request"
        )
    preview_arguments = events_by_id.get(str(required_for_event.get("caused_by_event_id")))
    if (
        preview_arguments is None
        or preview_arguments.get("event_name") != "TOOL_ARGUMENTS_READY"
        or preview_arguments.get("task_id") != request.task_id
        or preview_arguments.get("plan_version") != request.plan_version
        or preview_arguments.get("tool_call_id") != request.tool_call_id
        or preview_arguments.get("tool_name") != manifest.tool_name
        or preview_arguments.get("resolved_arguments_ref") != request.resolved_arguments_ref
        or preview_arguments.get("provenance_ref") != request.provenance_ref
        or preview_arguments.get("argument_fingerprint") != _argument_fingerprint(request.arguments)
    ):
        raise ToolExecutionPolicyError(
            f"{manifest.tool_name} confirmation must bind the previewed arguments"
        )


def _matches_confirmation_chain(
    *,
    required: Mapping[str, Any],
    waiting_for_confirmation: Mapping[str, Any] | None,
    patch_received: Mapping[str, Any] | None,
    interpreted: Mapping[str, Any] | None,
    received: Mapping[str, Any] | None,
    accepted: Mapping[str, Any],
    request: ToolExecutionRequest,
    confirmation_scope: str,
    require_waiting_for_confirmation: bool,
    events_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    patch_id = received.get("patch_id") if received is not None else None
    confirmation_events = (
        (required, waiting_for_confirmation, patch_received, interpreted, received, accepted)
        if require_waiting_for_confirmation
        else (required, patch_received, interpreted, received, accepted)
    )
    causal_chain_matches = (
        (
            _caused_by_chain_matches(required, waiting_for_confirmation)
            and _patch_received_is_caused_by_confirmation_path(
                patch_received,
                waiting_for_confirmation=waiting_for_confirmation,
                request=request,
                events_by_id=events_by_id,
            )
            and _caused_by_chain_matches(patch_received, interpreted, received, accepted)
        )
        if require_waiting_for_confirmation
        else _caused_by_chain_matches(required, patch_received, interpreted, received, accepted)
    )
    return bool(
        (
            not require_waiting_for_confirmation
            or _matches_waiting_for_user_confirmation(
                waiting_for_confirmation,
                request=request,
                confirmation_id=required.get("confirmation_id"),
            )
        )
        and _matches_user_patch_received(patch_received, request=request, patch_id=patch_id)
        and _matches_user_patch_interpreted(interpreted, request=request, patch_id=patch_id)
        and _matches_user_confirmation_received(
            received,
            request=request,
            patch_id=patch_id,
        )
        and _matches_confirmation_accepted(
            accepted,
            request=request,
            confirmation_scope=confirmation_scope,
        )
        and causal_chain_matches
        and _strict_event_seq_order(*confirmation_events)
    )


def _patch_received_is_caused_by_confirmation_path(
    patch_received: Mapping[str, Any] | None,
    *,
    waiting_for_confirmation: Mapping[str, Any] | None,
    request: ToolExecutionRequest,
    events_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    if patch_received is None or waiting_for_confirmation is None:
        return False
    caused_by_event_id = patch_received.get("caused_by_event_id")
    router_event = events_by_id.get(str(caused_by_event_id))
    return bool(
        _matches_confirmation_router_event(router_event, request=request)
        and _confirmation_router_has_turn_evidence(
            router_event,
            waiting_for_confirmation=waiting_for_confirmation,
            events_by_id=events_by_id,
        )
        and _strict_event_seq_order(waiting_for_confirmation, router_event, patch_received)
    )


def _matches_confirmation_router_event(
    event: Mapping[str, Any] | None,
    *,
    request: ToolExecutionRequest,
) -> bool:
    return bool(
        event is not None
        and event.get("event_name") == "ROUTER_DECISION_EMITTED"
        and event.get("router_decision") == "PATCH_ACTIVE_SLOW_TASK"
        and event.get("task_focus") == "ACTIVE_TASK_PATCH"
        and event.get("active_task_id") == request.task_id
    )


def _confirmation_router_has_turn_evidence(
    router_event: Mapping[str, Any] | None,
    *,
    waiting_for_confirmation: Mapping[str, Any],
    events_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    if router_event is None:
        return False
    turn_event = events_by_id.get(str(router_event.get("turn_committed_event_id")))
    thinker_event = events_by_id.get(str(router_event.get("thinker_frame_event_id")))
    return bool(
        _matches_router_turn_event(turn_event, router_event=router_event)
        and _matches_router_thinker_event(thinker_event, router_event=router_event)
        and turn_event.get("caused_by_event_id") == waiting_for_confirmation.get("event_id")
        and thinker_event.get("caused_by_event_id") == turn_event.get("event_id")
        and router_event.get("caused_by_event_id") == thinker_event.get("event_id")
        and _strict_event_seq_order(waiting_for_confirmation, turn_event, thinker_event, router_event)
    )


def _matches_router_turn_event(
    event: Mapping[str, Any] | None,
    *,
    router_event: Mapping[str, Any],
) -> bool:
    return bool(
        event is not None
        and event.get("event_name") == "TURN_INGRESS_COMMITTED"
        and event.get("turn_id") == router_event.get("turn_id")
        and event.get("utterance_id") == router_event.get("utterance_id")
    )


def _matches_router_thinker_event(
    event: Mapping[str, Any] | None,
    *,
    router_event: Mapping[str, Any],
) -> bool:
    return bool(
        event is not None
        and event.get("event_name") == "MOCK_THINKER_FRAME_EMITTED"
        and event.get("turn_id") == router_event.get("turn_id")
        and event.get("utterance_id") == router_event.get("utterance_id")
    )


def _matches_waiting_for_user_confirmation(
    event: Mapping[str, Any] | None,
    *,
    request: ToolExecutionRequest,
    confirmation_id: object,
) -> bool:
    return bool(
        event is not None
        and confirmation_id not in (None, "")
        and event.get("event_name") == "WAITING_FOR_USER_CONFIRMATION"
        and event.get("task_id") == request.task_id
        and event.get("plan_version") == request.plan_version
        and event.get("confirmation_id") == confirmation_id
    )


def _matches_user_patch_received(
    event: Mapping[str, Any] | None,
    *,
    request: ToolExecutionRequest,
    patch_id: object,
) -> bool:
    return bool(
        event is not None
        and patch_id not in (None, "")
        and event.get("event_name") == "USER_PATCH_RECEIVED"
        and event.get("task_id") == request.task_id
        and event.get("plan_version") == request.plan_version
        and event.get("patch_id") == patch_id
        and event.get("observed_plan_version") == request.plan_version
    )


def _matches_user_patch_interpreted(
    event: Mapping[str, Any] | None,
    *,
    request: ToolExecutionRequest,
    patch_id: object,
) -> bool:
    return bool(
        event is not None
        and patch_id not in (None, "")
        and event.get("event_name") == "USER_PATCH_INTERPRETED"
        and event.get("task_id") == request.task_id
        and event.get("plan_version") == request.plan_version
        and event.get("patch_id") == patch_id
        and event.get("observed_plan_version") == request.plan_version
        and event.get("interpreted_against_plan_version") == request.plan_version
        and event.get("interpretation_type") == "confirmation"
    )


def _matches_user_confirmation_received(
    event: Mapping[str, Any] | None,
    *,
    request: ToolExecutionRequest,
    patch_id: object,
) -> bool:
    return bool(
        event is not None
        and patch_id not in (None, "")
        and event.get("event_name") == "USER_CONFIRMATION_RECEIVED"
        and event.get("task_id") == request.task_id
        and event.get("plan_version") == request.plan_version
        and event.get("confirmation_id") == request.accepted_confirmation_id
        and event.get("patch_id") == patch_id
        and event.get("confirmation_signal") == "accepted"
    )


def _strict_event_seq_order(*events: Mapping[str, Any] | None) -> bool:
    previous_event_seq: int | None = None
    for event in events:
        if event is None:
            return False
        event_seq = _int_value(event.get("event_seq"))
        if event_seq is None:
            return False
        if previous_event_seq is not None and event_seq <= previous_event_seq:
            return False
        previous_event_seq = event_seq
    return True


def _caused_by_chain_matches(*events: Mapping[str, Any] | None) -> bool:
    previous_event_id: object | None = None
    for event in events:
        if event is None:
            return False
        if previous_event_id is not None and event.get("caused_by_event_id") != previous_event_id:
            return False
        previous_event_id = event.get("event_id")
        if previous_event_id in (None, ""):
            return False
    return True


def _event_seq_before(event: Mapping[str, Any], before_event_seq: int | None) -> bool:
    event_seq = _int_value(event.get("event_seq"))
    return event_seq is not None and before_event_seq is not None and event_seq < before_event_seq


def _missing_argument_or_provenance_fields(
    request: ToolExecutionRequest,
    manifest: ToolManifest,
    journal_events: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    events_by_id = {str(event["event_id"]): event for event in journal_events}
    required_arguments = tuple(str(field) for field in manifest.required_arguments)
    required_provenance = tuple(
        str(field) for field in manifest.argument_provenance_requirements
    )
    missing_arguments = tuple(
        field
        for field in required_arguments
        if not _has_value(request.arguments.get(field))
    )
    missing_argument_set = set(missing_arguments)
    missing_provenance = tuple(
        f"provenance.{field}"
        for field in required_provenance
        if field not in missing_argument_set
        and not _has_current_plan_argument_provenance(
            request.argument_provenance.get(field),
            events_by_id=events_by_id,
            task_id=request.task_id,
            plan_version=request.plan_version,
        )
    )
    return (*missing_arguments, *missing_provenance)


def _journal_task_cursor(
    journal_events: Sequence[Mapping[str, Any]],
    *,
    task_id: str,
) -> _JournalTaskCursor:
    current_plan_version: int | None = None
    latest_task_event_seq: int | None = None
    lifecycle_state: str | None = None
    for event in journal_events:
        if event.get("task_id") != task_id:
            continue
        event_name = event.get("event_name")
        if event_name == "SLOWTASK_CREATED":
            current_plan_version = _int_value(event.get("plan_version"))
            lifecycle_state = lifecycle_state or "CREATED"
        elif event_name == "PLAN_VERSION_ADVANCED":
            current_plan_version = _int_value(event.get("to_plan_version"))
        elif event_name == "SLOWTASK_STATE_CHANGED":
            to_state = event.get("to_state")
            if isinstance(to_state, str):
                lifecycle_state = _next_lifecycle_state(lifecycle_state, to_state)
        elif event_name == "SLOWTASK_FAILED":
            lifecycle_state = "FAILED"
        elif event_name == "SLOWTASK_CANCELLED":
            lifecycle_state = "CANCELLED"

        task_event_seq = _int_value(event.get("task_event_seq"))
        if task_event_seq is not None and (
            latest_task_event_seq is None or task_event_seq > latest_task_event_seq
        ):
            latest_task_event_seq = task_event_seq
    return _JournalTaskCursor(
        current_plan_version=current_plan_version,
        latest_task_event_seq=latest_task_event_seq,
        lifecycle_state=lifecycle_state,
    )


def _next_lifecycle_state(current_state: str | None, to_state: str) -> str:
    if current_state in TERMINAL_SLOWTASK_STATES and to_state not in TERMINAL_SLOWTASK_STATES:
        return current_state
    return to_state


def _has_current_plan_argument_provenance(
    event_id: object,
    *,
    events_by_id: Mapping[str, Mapping[str, Any]],
    task_id: str,
    plan_version: int,
) -> bool:
    if not _has_value(event_id):
        return False
    event = events_by_id.get(str(event_id))
    if event is None:
        return False
    if event.get("task_id") != task_id:
        return False
    if event.get("event_name") not in {
        "ARGUMENTS_RESOLVED",
        "ARGUMENT_RESOLUTION_PROVENANCE",
    }:
        return False
    return _int_value(event.get("plan_version")) == plan_version


def _int_value(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _has_value(value: object) -> bool:
    return value is not None and value != ""


def _argument_fingerprint(arguments: Mapping[str, Any]) -> str:
    canonical_arguments = json.dumps(
        arguments,
        default=str,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical_arguments.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
