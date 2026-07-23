from __future__ import annotations

"""Python-owned, text-first Slow-system Workbench sessions.

The browser talks to a small public snapshot projection.  Every meaningful
operation in this module goes through the existing text ingress, Interaction
Controller, Router, SlowTask runtime, UserPatch evidence pack, or Demo Tool
Executor.  The session object is only an orchestration boundary; reducers and
the event journal remain the source of truth.
"""

import asyncio
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import os
import re
from typing import Any

from voice_agent.access.text_ingress import receive_text_input
from voice_agent.adapters.capabilities import AdapterCapability
from voice_agent.adapters.codex_slow_llm import (
    CODEX_PROVIDER_MODES,
    CodexSlowLLMAdapter,
    CodexSlowLLMAdapterConfig,
    build_codex_slow_llm_capability,
)
from voice_agent.adapters.mock_adapters import mvp0_mock_adapter_capabilities
from voice_agent.demo_backend.in_memory import InMemoryDemoBackend
from voice_agent.events.journal import InMemoryEventJournal
from voice_agent.interaction.controller import InteractionController
from voice_agent.router.router import (
    MVP1Router,
    MVP1TaskFocusUpdateEmitter,
    RouterContext,
    TaskFocusSnapshot,
)
from voice_agent.runtime.adapter_callback_boundary import AdapterCallbackAppendBoundary
from voice_agent.runtime.assembly import RuntimeAdapterAssemblyConfig
from voice_agent.runtime.session import start_configured_session
from voice_agent.runtime.slow_system_workbench_context import TaskContextPackBuilder
from voice_agent.runtime.slow_system_workbench_snapshots import (
    ProviderTraceItem,
    WorkbenchSnapshot,
    project_workbench_snapshot,
)
from voice_agent.slowtask.mock_runtime import MockSlowTaskRuntime
from voice_agent.state.slowtask_state import SlowTaskRecord, SlowTaskState
from voice_agent.state.task_focus_state import TaskFocusState
from voice_agent.state.tool_execution_state import ToolExecutionState
from voice_agent.tools.demo_manifests import mvp2_demo_tool_manifests
from voice_agent.tools.executor import (
    DemoToolExecutor,
    ToolExecutionHandle,
    ToolExecutionRequest,
)
from voice_agent.tools.registry import ToolRegistry
from voice_agent.user_patch.evidence_pack import UserPatchEvidencePackRuntime


WORKBENCH_RUNTIME_CONFIG_REF = "config://workbench/text-first-python-owned"
WORKBENCH_CAPABILITY_SNAPSHOT_REF = "capability://workbench/slow-system-v1"
WORKBENCH_CAPABILITY_VERSION = "workbench.slow-system.v1"
WORKBENCH_DEFAULT_PROVIDER_MODE = "codex_cli_local"
WORKBENCH_DEFAULT_ALLOW_LOCAL_CODEX_CLI = True
SAFE_TEXT_MAX_LENGTH = 1200
SAFE_SUMMARY_MAX_LENGTH = 320
_SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_UNSAFE_INPUT_PATTERN = re.compile(
    r"(bearer\s+\S+|api[_-]?key\s*=|authorization\s*=|token\s*=|password\s*=|/Users/|file://)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkbenchRuntimeConfig:
    """Provider configuration injected at session construction time."""

    provider_mode: str = WORKBENCH_DEFAULT_PROVIDER_MODE
    allow_local_codex_cli: bool = WORKBENCH_DEFAULT_ALLOW_LOCAL_CODEX_CLI
    codex_bin: str = "codex"
    timeout_seconds: int = 30
    model_name: str | None = None
    reasoning_effort: str | None = None
    max_repair_attempts: int = 2

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "WorkbenchRuntimeConfig":
        value = {} if value is None else value
        provider_mode = str(value.get("provider_mode", WORKBENCH_DEFAULT_PROVIDER_MODE))
        if provider_mode not in CODEX_PROVIDER_MODES:
            raise ValueError(f"provider_mode must be one of {sorted(CODEX_PROVIDER_MODES)}")
        model_name = value.get("model_name")
        reasoning_effort = value.get("reasoning_effort")
        return cls(
            provider_mode=provider_mode,
            allow_local_codex_cli=bool(
                value.get("allow_local_codex_cli", WORKBENCH_DEFAULT_ALLOW_LOCAL_CODEX_CLI)
            ),
            codex_bin=str(value.get("codex_bin", "codex")),
            timeout_seconds=int(value.get("timeout_seconds", 30)),
            model_name=None if model_name in (None, "") else str(model_name),
            reasoning_effort=None if reasoning_effort in (None, "") else str(reasoning_effort),
            max_repair_attempts=int(value.get("max_repair_attempts", 2)),
        )

    @classmethod
    def from_environment(cls) -> "WorkbenchRuntimeConfig":
        provider_mode = os.environ.get(
            "VOICE_AGENT_WORKBENCH_CODEX_MODE",
            WORKBENCH_DEFAULT_PROVIDER_MODE,
        )
        allow = os.environ.get(
            "VOICE_AGENT_ALLOW_LOCAL_CODEX_CLI",
            "1" if WORKBENCH_DEFAULT_ALLOW_LOCAL_CODEX_CLI else "0",
        ).lower() in {
            "1",
            "true",
            "yes",
        }
        return cls.from_mapping(
            {
                "provider_mode": provider_mode,
                "allow_local_codex_cli": allow,
                "codex_bin": os.environ.get("VOICE_AGENT_CODEX_BIN", "codex"),
                "timeout_seconds": os.environ.get("VOICE_AGENT_CODEX_TIMEOUT_SECONDS", "30"),
                "model_name": os.environ.get("VOICE_AGENT_CODEX_MODEL"),
                "reasoning_effort": os.environ.get("VOICE_AGENT_CODEX_REASONING_EFFORT"),
            }
        )


@dataclass
class _SessionClock:
    monotonic_ms: int = 0
    wall_clock_ms: int = 1_700_000_000_000
    step_ms: int = 100

    def reserve(self) -> tuple[int, int]:
        monotonic = self.monotonic_ms
        wall_clock = self.wall_clock_ms
        self.monotonic_ms += self.step_ms
        self.wall_clock_ms += self.step_ms
        return monotonic, wall_clock


class WorkbenchSession:
    """One persistent session with serialized control-plane operations."""

    def __init__(
        self,
        *,
        session_id: str,
        config: WorkbenchRuntimeConfig | None = None,
    ) -> None:
        self.session_id = _safe_token(session_id, "session_id")
        self._config = config or WorkbenchRuntimeConfig()
        self._lock = asyncio.Lock()
        self._reset_generation = 0
        self._initialize_runtime()

    @property
    def config(self) -> WorkbenchRuntimeConfig:
        return self._config

    @property
    def journal(self) -> InMemoryEventJournal:
        """Expose the session journal for deterministic tests/debug tooling."""

        return self._journal

    @property
    def latest_proposal(self) -> Mapping[str, Any] | None:
        return self._codex_proposals[-1] if self._codex_proposals else None

    @property
    def codex_capability(self) -> Mapping[str, Any]:
        return self._codex_adapter.capability

    async def snapshot(self) -> dict[str, Any]:
        if self._lock.locked():
            return self._live_snapshot()
        async with self._lock:
            snapshot = self._snapshot().to_dict()
            self._stable_snapshot = deepcopy(snapshot)
            return snapshot

    async def process_message(
        self,
        text: str,
        *,
        action: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            return await self._process_message_locked(text, action=action)

    async def confirm(
        self,
        confirmation_id: str,
        *,
        accepted: bool,
    ) -> dict[str, Any]:
        async with self._lock:
            confirmation_id = _safe_token(confirmation_id, "confirmation_id")
            task_state, focus_state, _ = self._projections()
            task = self._task_for_focus(task_state, focus_state)
            if task is None:
                raise ValueError("no active SlowTask is waiting for confirmation")
            pending = task.confirmation_state.pending_confirmation_id
            if pending != confirmation_id:
                raise ValueError("confirmation_id does not match the current-plan pending confirmation")
            text = "确认取消当前任务" if accepted else "保留当前任务，不取消"
            return await self._process_message_locked(
                text,
                action="confirmation",
                confirmation_signal="accepted" if accepted else "rejected",
                expected_confirmation_id=confirmation_id,
            )

    async def reset(self) -> dict[str, Any]:
        async with self._lock:
            self._reset_generation += 1
            self._initialize_runtime()
            return self._snapshot().to_dict()

    async def _process_message_locked(
        self,
        text: str,
        *,
        action: str | None,
        confirmation_signal: str | None = None,
        expected_confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        safe_text = _safe_user_text(text)
        self._begin_live_stream()
        task_state, focus_state, _ = self._projections()
        resolved_action = _resolve_action(safe_text, action, task_state=task_state, focus_state=focus_state)
        turn, asr_frame, thinker_frame, router_result = self._append_text_turn_and_router(
            safe_text,
            action=resolved_action,
        )

        decision = str(router_result.router_decision_event["router_decision"])
        self._record_live_progress(
            kind="router_decision_emitted",
            status="observed",
            phase="routing",
            label=f"Router 已分类为 {decision}",
            detail="输入已写入 event journal，正在决定是否进入 SlowTask。",
        )
        self._publish_stable_snapshot_locked()
        if decision == "SPAWN_SLOW_TASK":
            result = await self._handle_spawn(
                text=safe_text,
                turn=turn,
                router_event=router_result.router_decision_event,
            )
        elif decision == "PATCH_ACTIVE_SLOW_TASK":
            if resolved_action == "confirmation":
                result = await self._handle_confirmation_patch(
                    text=safe_text,
                    turn=turn,
                    asr_frame=asr_frame,
                    thinker_frame=thinker_frame,
                    router_event=router_result.router_decision_event,
                    confirmation_signal=confirmation_signal or "rejected",
                    expected_confirmation_id=expected_confirmation_id,
                )
            elif resolved_action == "cancel_candidate":
                result = await self._handle_user_patch(
                    text=safe_text,
                    turn=turn,
                    asr_frame=asr_frame,
                    thinker_frame=thinker_frame,
                    router_event=router_result.router_decision_event,
                    candidate_patch_types=("cancel_candidate",),
                    patch_kind="cancel",
                )
            elif resolved_action == "adopt_stale_evidence":
                result = await self._handle_adopt_stale_evidence(
                    text=safe_text,
                    turn=turn,
                    asr_frame=asr_frame,
                    thinker_frame=thinker_frame,
                    router_event=router_result.router_decision_event,
                )
            elif resolved_action == "complete_current":
                result = await self._handle_complete_current(
                    text=safe_text,
                    router_event=router_result.router_decision_event,
                )
            else:
                result = await self._handle_user_patch(
                    text=safe_text,
                    turn=turn,
                    asr_frame=asr_frame,
                    thinker_frame=thinker_frame,
                    router_event=router_result.router_decision_event,
                    candidate_patch_types=("constraint_update_candidate",),
                    patch_kind="material",
                )
        else:
            result = self._handle_foreground_chat(
                text=safe_text,
                decision=decision,
                task_focus=str(router_result.router_decision_event.get("task_focus", "FOREGROUND_CHAT")),
            )

        self._conversation.append(
            {
                "id": self._next_id("conversation_assistant"),
                "speaker": "assistant_fast" if decision == "FAST_ONLY" else "system",
                "text": result["assistant_summary"],
                "summary": result["assistant_summary"],
                "owner": "router" if decision == "FAST_ONLY" else "slowtask",
                "source": "python_workbench",
                "note": "该文本是状态摘要；任务事实仍由 event journal / reducer 投影拥有。",
            }
        )
        self._record_live_progress(
            kind="turn_completed",
            status="completed",
            phase="finalizing",
            label="本轮状态已汇总",
            detail="最终回复只引用已校验的状态摘要和 proposal 边界。",
        )
        self._stream_active = False
        self._stream_phase = "complete"
        self._stream_label = "本轮执行完成"
        self._stream_detail = "可以继续补充消息或选择左侧快捷问题。"
        snapshot = self._snapshot().to_dict()
        self._stable_snapshot = deepcopy(snapshot)
        return {
            "status": result["status"],
            "session_id": self.session_id,
            "snapshot": snapshot,
        }

    def _initialize_runtime(self) -> None:
        self._clock = _SessionClock(
            monotonic_ms=self._reset_generation * 100_000,
            wall_clock_ms=1_700_000_000_000 + self._reset_generation * 100_000,
        )
        self._id_counter = 0
        self._conversation: list[dict[str, Any]] = []
        self._evidence_catalog: dict[str, dict[str, Any]] = {}
        self._provider_trace: list[ProviderTraceItem] = []
        self._live_provider_progress: list[ProviderTraceItem] = []
        self._stream_sequence = 0
        self._stream_active = False
        self._stream_phase = "idle"
        self._stream_label = "等待新的 Workbench turn"
        self._stream_detail = ""
        self._codex_proposals: list[dict[str, Any]] = []
        self._in_flight_handles: dict[str, ToolExecutionHandle] = {}
        self._last_user_input: str | None = None
        self._resolved_argument_values: dict[str, Any] = {}
        self.conversation_id = f"conv_{self.session_id}_{self._reset_generation + 1}"

        codex_matrix = build_codex_slow_llm_capability(
            provider_mode=self._config.provider_mode,
            allow_local_codex_cli=self._config.allow_local_codex_cli,
            model_name=self._config.model_name,
        )
        codex_matrix["unsupported_capabilities"] = tuple(codex_matrix["unsupported_capabilities"])
        capabilities = (*mvp0_mock_adapter_capabilities(), AdapterCapability(**codex_matrix))
        startup = start_configured_session(
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            runtime_config_ref=WORKBENCH_RUNTIME_CONFIG_REF,
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            assembly_config=RuntimeAdapterAssemblyConfig(
                stage="mvp0_mock",
                capability_snapshot_ref=WORKBENCH_CAPABILITY_SNAPSHOT_REF,
                capability_version=WORKBENCH_CAPABILITY_VERSION,
            ),
            capabilities=capabilities,
        )
        self._journal = startup.journal
        self._boundary = AdapterCallbackAppendBoundary(self._journal)
        self._codex_adapter = CodexSlowLLMAdapter(
            boundary=self._boundary,
            config=CodexSlowLLMAdapterConfig(
                provider_mode=self._config.provider_mode,
                allow_local_codex_cli=self._config.allow_local_codex_cli,
                codex_bin=self._config.codex_bin,
                timeout_seconds=self._config.timeout_seconds,
                model_name=self._config.model_name,
                reasoning_effort=self._config.reasoning_effort,
                max_repair_attempts=self._config.max_repair_attempts,
            ),
        )
        self._tool_executor = DemoToolExecutor(
            journal=self._journal,
            registry=ToolRegistry(mvp2_demo_tool_manifests()),
            backend=InMemoryDemoBackend(),
        )
        self._slowtask_runtime = MockSlowTaskRuntime(self._journal)
        self._user_patch_runtime = UserPatchEvidencePackRuntime(self._journal)
        self._capability_snapshot = startup.capability_snapshot
        self._capability_matrices = tuple(capability.to_dict() for capability in capabilities)
        self._stable_snapshot = self._snapshot().to_dict()

    def _append_text_turn_and_router(
        self,
        text: str,
        *,
        action: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any]:
        turn_id = self._next_id("turn")
        utterance_id = self._next_id("utterance")
        input_span_id = self._next_id("input")
        text_span_id = self._next_id("text")
        base_monotonic, base_wall = self._clock.reserve()
        text_event = receive_text_input(
            self._journal,
            event_id=self._next_id("text_input"),
            caused_by_event_id=self._journal.events()[-1]["event_id"],
            created_monotonic_ms=base_monotonic,
            created_wall_clock_ms=base_wall,
            input_span_id=input_span_id,
            text_span_id=text_span_id,
            redacted_text=text,
            language_hint="zh-CN",
        )
        ingress = InteractionController(self._journal).commit_text_ingress(
            text_event,
            turn_id=turn_id,
            utterance_id=utterance_id,
            created_monotonic_ms=base_monotonic + 10,
            created_wall_clock_ms=base_wall + 10,
        )
        task_focus_hint, task_like, complexity_hint = _frame_hints(action)
        asr_frame = self._journal.append(
            event_name="MOCK_ASR_FRAME_EMITTED",
            event_id=self._next_id("asr_frame"),
            source_module="mock_asr_adapter",
            caused_by_event_id=str(ingress.turn_committed["event_id"]),
            created_monotonic_ms=base_monotonic + 20,
            created_wall_clock_ms=base_wall + 20,
            trace_redaction_level="metadata_only",
            turn_id=turn_id,
            utterance_id=utterance_id,
            input_modality="text",
            asr_frame_ref=f"asr://synthetic/{self.session_id}/{utterance_id}",
            output_mode="mock",
            task_focus_hint=task_focus_hint,
            task_like=task_like,
            complexity_hint=complexity_hint,
            focus_confidence=0.96,
            evidence_uncertainty="low",
        )
        thinker_frame = self._journal.append(
            event_name="MOCK_THINKER_FRAME_EMITTED",
            event_id=self._next_id("thinker_frame"),
            source_module="mock_thinker_adapter",
            caused_by_event_id=str(ingress.turn_committed["event_id"]),
            created_monotonic_ms=base_monotonic + 30,
            created_wall_clock_ms=base_wall + 30,
            trace_redaction_level="metadata_only",
            turn_id=turn_id,
            utterance_id=utterance_id,
            input_modality="text",
            semantic_frame_ref=f"semantic://synthetic/{self.session_id}/{utterance_id}",
            semantic_summary_ref=f"summary://synthetic/{self.session_id}/{utterance_id}",
            output_mode="mock",
            task_focus_hint=task_focus_hint,
            task_like=task_like,
            complexity_hint=complexity_hint,
            focus_confidence=0.96,
            evidence_uncertainty="low",
        )
        task_state, focus_state, _ = self._projections()
        router_result = MVP1Router(self._journal).emit_decision(
            turn_committed_event=ingress.turn_committed,
            asr_frame_event=asr_frame,
            thinker_frame_event=thinker_frame,
            router_context=RouterContext(task_focus_snapshot=self._task_focus_snapshot(task_state, focus_state)),
            event_id=self._next_id("router_decision"),
            task_focus_state_event_id=self._next_id("task_focus_state"),
            created_monotonic_ms=base_monotonic + 40,
            created_wall_clock_ms=base_wall + 40,
        )
        self._last_user_input = text
        self._conversation.append(
            {
                "id": self._next_id("conversation_user"),
                "speaker": "user",
                "text": text,
                "summary": text,
                "owner": "event_journal",
                "source": "text_input",
                "note": "文本先经过 TEXT_INPUT_RECEIVED / TURN_INGRESS_COMMITTED，再进入 Router。",
            }
        )
        return ingress.turn_committed, asr_frame, thinker_frame, router_result

    async def _handle_spawn(
        self,
        *,
        text: str,
        turn: Mapping[str, Any],
        router_event: Mapping[str, Any],
    ) -> dict[str, str]:
        task_id = self._next_id("task")
        evidence_ref = self._next_ref("evidence", "user")
        self._record_evidence(
            evidence_ref,
            task_id=task_id,
            plan_version=1,
            label="用户初始任务",
            summary=text,
            source="user_text",
            trust_level="authoritative_user_evidence",
        )
        prefix = self._next_id("spawn")
        created = self._slowtask_runtime.create_from_router_spawn(
            router_decision_event=router_event,
            task_id=task_id,
            initial_goal_ref=f"goal://synthetic/{task_id}",
            event_id_prefix=prefix,
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            source_evidence_refs=(evidence_ref,),
        )
        focus_base_mono, focus_base_wall = self._clock.reserve()
        MVP1TaskFocusUpdateEmitter(self._journal).emit_update(
            router_decision_event=router_event,
            event_id=self._next_id("task_focus_active"),
            created_monotonic_ms=focus_base_mono,
            created_wall_clock_ms=focus_base_wall,
            active_task_id=task_id,
            foreground_mode="SLOWTASK_ACTIVE",
            default_patch_policy="ACTIVE_TASK_PATCH_ONLY",
        )
        planning = self._slowtask_runtime.run_planning_started(
            task_id=task_id,
            plan_version=1,
            caused_by_event_id=str(created.produced_events[-1]["event_id"]),
            event_id_prefix=self._next_id("planning"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=3,
        )
        planning_event = planning.produced_events[0]
        provider_result = await self._call_codex(
            intent=text,
            slowtask_event=planning_event,
            source_evidence_refs=(evidence_ref,),
        )
        codex_ref = self._next_ref("evidence", "codex")
        self._record_evidence(
            codex_ref,
            task_id=task_id,
            plan_version=1,
            label="Codex structured proposal",
            summary="Codex 只返回了经过 schema 校验的 proposal candidate；不拥有 SlowTask facts。",
            source="codex_adapter",
            trust_level="evidence_candidate_only",
        )
        self._codex_proposals.append(provider_result.proposal)
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        review = self._slowtask_runtime.review_evidence(
            task_id=task_id,
            plan_version=task.current_plan_version,
            caused_by_event_id=str(provider_result.structured_output_event["event_id"]),
            event_id_prefix=self._next_id("review"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=task.current_task_event_seq + 1,
            evidence_refs=(evidence_ref, codex_ref),
            required_fields=("company_location", "days"),
            resolved_fields=("company_location", "days"),
            resolved_arguments_ref=self._next_ref("args", "resolved"),
            provenance_ref=self._next_ref("provenance", "arguments"),
            field_provenance_refs=(evidence_ref, codex_ref),
        )
        self._resolved_argument_values = {
            "company_location": "Synthetic Central Office",
            "days": 2,
            "time_window": "flexible",
        }
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        arguments_event = next(
            event for event in review.produced_events if event["event_name"] == "ARGUMENTS_RESOLVED"
        )
        provenance_event = next(
            event for event in review.produced_events if event["event_name"] == "ARGUMENT_RESOLUTION_PROVENANCE"
        )
        request = ToolExecutionRequest(
            tool_call_id=self._next_id("tool_call"),
            tool_name="demo.itinerary.search",
            task_id=task_id,
            plan_version=task.current_plan_version,
            current_plan_version=task.current_plan_version,
            start_task_event_seq=task.current_task_event_seq + 1,
            caused_by_event_id=str(review.produced_events[-1]["event_id"]),
            event_id_prefix=self._next_id("itinerary_search"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            idempotency_key=self._next_id("idempotency"),
            arguments=dict(self._resolved_argument_values),
            argument_provenance={
                "company_location": str(provenance_event["event_id"]),
                "days": str(provenance_event["event_id"]),
            },
            resolved_arguments_ref=str(arguments_event["event_id"]),
            provenance_ref=str(provenance_event["event_id"]),
            preview_ref=self._next_ref("preview", "itinerary"),
        )
        started = self._tool_executor.begin(request)
        if started.handle is None:
            return {
                "status": "tool_blocked",
                "assistant_summary": f"demo.itinerary.search 被阻塞：{', '.join(started.blocking_fields)}。",
            }
        handle = started.handle
        waiting = self._slowtask_runtime.emit_waiting_for_tool(
            task_id=task_id,
            plan_version=task.current_plan_version,
            caused_by_event_id=handle.caused_by_event_id,
            event_id_prefix=self._next_id("waiting_tool"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=handle.next_task_event_seq,
            tool_call_id=request.tool_call_id,
        )
        for event in waiting.produced_events:
            handle.record_task_event(event)
        self._in_flight_handles[request.tool_call_id] = handle
        return {
            "status": "tool_running",
            "assistant_summary": "已进入 PLANNING → EXECUTING，demo.itinerary.search 正在 sandbox 中运行；你可以继续补充约束。",
        }

    async def _handle_user_patch(
        self,
        *,
        text: str,
        turn: Mapping[str, Any],
        asr_frame: Mapping[str, Any],
        thinker_frame: Mapping[str, Any],
        router_event: Mapping[str, Any],
        candidate_patch_types: Sequence[str],
        patch_kind: str,
    ) -> dict[str, str]:
        task_state, focus_state, _ = self._projections()
        task = self._task_for_focus(task_state, focus_state)
        if task is None:
            raise ValueError("PATCH_ACTIVE_SLOW_TASK requires an active SlowTask")
        patch_id = self._next_id("patch")
        evidence_ref = self._next_ref("evidence", "patch")
        patch_result = self._user_patch_runtime.receive_patch_from_router_decision(
            router_decision_event=router_event,
            turn_committed_event=turn,
            task_id=task.task_id,
            current_plan_version=task.current_plan_version,
            next_task_event_seq=task.current_task_event_seq + 1,
            patch_id=patch_id,
            event_id=self._next_id("user_patch_received"),
            evidence_ref=evidence_ref,
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            text_input_event=self._latest_text_input_event(turn_id=str(turn["turn_id"])),
            asr_frame_event=asr_frame,
            thinker_frame_event=thinker_frame,
            candidate_patch_types=candidate_patch_types,
            patch_hint=text,
            semantic_summary_ref=str(thinker_frame["semantic_summary_ref"]),
        )
        self._record_evidence(
            evidence_ref,
            task_id=task.task_id,
            plan_version=task.current_plan_version,
            label="用户 material patch" if patch_kind == "material" else "用户控制 patch",
            summary=text,
            source="user_patch",
            trust_level="authoritative_user_evidence",
        )
        handle = self._handle_for_task(task.task_id)
        if handle is not None:
            handle.record_task_event(patch_result.user_patch_event)
        interpretation = self._slowtask_runtime.interpret_user_patch(
            user_patch_event=patch_result.user_patch_event,
            event_id_prefix=self._next_id("patch_interpretation"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            current_lifecycle_state=task.lifecycle_state,
            confirmation_id=self._next_id("confirmation") if patch_kind == "cancel" else None,
            prompt_ref=self._next_ref("prompt", "cancel") if patch_kind == "cancel" else None,
        )
        if handle is not None:
            for event in interpretation.produced_events:
                handle.record_task_event(event)

        if patch_kind == "cancel":
            return {
                "status": "confirmation_required",
                "assistant_summary": "我已记录取消候选，但不会直接取消；请在当前 plan 的 confirmation gate 中确认。",
            }

        restarted = next(
            (
                event
                for event in interpretation.produced_events
                if event["event_name"] == "PLANNING_RESTARTED"
            ),
            None,
        )
        if restarted is None:
            return {
                "status": "patch_recorded",
                "assistant_summary": "用户 patch 已记录为 evidence，但没有改变当前 plan_version。",
            }
        provider_result = await self._call_codex(
            intent=text,
            slowtask_event=restarted,
            source_evidence_refs=(evidence_ref,),
        )
        self._codex_proposals.append(provider_result.proposal)

        if handle is None:
            return {
                "status": "plan_advanced",
                "assistant_summary": "material UserPatch 已推进 current plan_version 并重新进入 PLANNING。",
            }

        completion_monotonic, completion_wall = self._clock.reserve()
        completion = self._tool_executor.complete(
            handle,
            created_monotonic_ms=completion_monotonic,
            created_wall_clock_ms=completion_wall,
        )
        self._in_flight_handles.pop(handle.request.tool_call_id, None)
        old_result = next(
            event for event in completion.produced_events if event["event_name"] == "TOOL_RESULT_RECEIVED"
        )
        task_state, _, _ = self._projections()
        current_task = task_state.tasks[task.task_id]
        stale_ref = self._next_ref("evidence", "stale_tool_result")
        stale = self._slowtask_runtime.mark_tool_result_stale(
            task_id=current_task.task_id,
            current_plan_version=current_task.current_plan_version,
            result_plan_version=handle.plan_version,
            tool_call_id=handle.request.tool_call_id,
            caused_by_event_id=str(old_result["event_id"]),
            event_id_prefix=self._next_id("stale_result"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=current_task.current_task_event_seq + 1,
            stale_evidence_ref=stale_ref,
            stale_reason="late_tool_result_after_material_user_patch",
        )
        self._record_evidence(
            stale_ref,
            task_id=current_task.task_id,
            plan_version=handle.plan_version,
            label="旧 plan ToolResult",
            summary="demo.itinerary.search 在 plan_version=1 返回；SlowTask 已把它放入 stale_evidence。",
            source="tool_result",
            trust_level="stale_evidence",
            stale=True,
        )
        self._conversation.append(
            {
                "id": self._next_id("conversation_tool"),
                "speaker": "tool",
                "text": "旧 plan 的 demo tool result 已晚到；它被记录并标记为 stale，不推进当前 plan。",
                "summary": "旧 plan ToolResult → stale_evidence",
                "owner": "tool_executor",
                "source": "demo_backend",
                "note": "只有 SlowTask 显式 adopt/rebase 后，旧结果才可复用。",
            }
        )
        return {
            "status": "plan_advanced_stale_result",
            "assistant_summary": "material UserPatch 已将 plan_version 推进到 2；旧工具结果晚到后进入 stale_evidence。",
        }

    async def _handle_confirmation_patch(
        self,
        *,
        text: str,
        turn: Mapping[str, Any],
        asr_frame: Mapping[str, Any],
        thinker_frame: Mapping[str, Any],
        router_event: Mapping[str, Any],
        confirmation_signal: str,
        expected_confirmation_id: str | None,
    ) -> dict[str, str]:
        task_state, focus_state, _ = self._projections()
        task = self._task_for_focus(task_state, focus_state)
        if task is None:
            raise ValueError("confirmation requires an active SlowTask")
        pending = task.confirmation_state.pending_confirmation_id
        if pending is None or (expected_confirmation_id is not None and pending != expected_confirmation_id):
            raise ValueError("confirmation does not match current pending confirmation")
        patch_id = self._next_id("confirmation_patch")
        evidence_ref = self._next_ref("evidence", "confirmation")
        patch_result = self._user_patch_runtime.receive_patch_from_router_decision(
            router_decision_event=router_event,
            turn_committed_event=turn,
            task_id=task.task_id,
            current_plan_version=task.current_plan_version,
            next_task_event_seq=task.current_task_event_seq + 1,
            patch_id=patch_id,
            event_id=self._next_id("confirmation_patch_received"),
            evidence_ref=evidence_ref,
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            text_input_event=self._latest_text_input_event(turn_id=str(turn["turn_id"])),
            asr_frame_event=asr_frame,
            thinker_frame_event=thinker_frame,
            candidate_patch_types=("confirmation_candidate",),
            patch_hint=text,
            semantic_summary_ref=str(thinker_frame["semantic_summary_ref"]),
        )
        self._record_evidence(
            evidence_ref,
            task_id=task.task_id,
            plan_version=task.current_plan_version,
            label="用户 confirmation response",
            summary="用户对当前 TASK_CANCEL confirmation gate 的回应。",
            source="user_confirmation",
            trust_level="authoritative_user_evidence",
        )
        handle = self._handle_for_task(task.task_id)
        if handle is not None:
            handle.record_task_event(patch_result.user_patch_event)
        result = self._slowtask_runtime.interpret_user_patch(
            user_patch_event=patch_result.user_patch_event,
            event_id_prefix=self._next_id("confirmation_interpretation"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            current_lifecycle_state=task.lifecycle_state,
            pending_confirmation_id=pending,
            pending_confirmation_scope=task.confirmation_state.confirmation_scope,
            confirmation_signal=confirmation_signal,
            authorization_ref=self._next_ref("authorization", "cancel") if confirmation_signal == "accepted" else None,
            return_to_state="EXECUTING" if handle is not None else "PLANNING",
        )
        if handle is not None:
            for event in result.produced_events:
                handle.record_task_event(event)
        if confirmation_signal == "accepted" and handle is not None:
            cancel_mono, cancel_wall = self._clock.reserve()
            self._tool_executor.cancel(
                handle,
                cancel_reason="slowtask_cancel_confirmation_accepted",
                created_monotonic_ms=cancel_mono,
                created_wall_clock_ms=cancel_wall,
            )
            self._in_flight_handles.pop(handle.request.tool_call_id, None)
        if confirmation_signal == "accepted":
            clear_mono, clear_wall = self._clock.reserve()
            MVP1TaskFocusUpdateEmitter(self._journal).emit_update(
                router_decision_event=router_event,
                event_id=self._next_id("task_focus_cleared"),
                created_monotonic_ms=clear_mono,
                created_wall_clock_ms=clear_wall,
                active_task_id=None,
                foreground_mode="IDLE",
                default_patch_policy="NO_ACTIVE_TASK",
            )
            return {
                "status": "cancelled",
                "assistant_summary": "当前 confirmation 已接受；SlowTask 已进入 CANCELLED，未产生外部副作用。",
            }
        return {
            "status": "confirmation_rejected",
            "assistant_summary": "已拒绝取消；当前任务回到可继续处理的状态。",
        }

    async def _handle_adopt_stale_evidence(
        self,
        *,
        text: str,
        turn: Mapping[str, Any],
        asr_frame: Mapping[str, Any],
        thinker_frame: Mapping[str, Any],
        router_event: Mapping[str, Any],
    ) -> dict[str, str]:
        task_state, focus_state, _ = self._projections()
        task = self._task_for_focus(task_state, focus_state)
        if task is None or not task.stale_evidence_refs:
            return {"status": "no_stale_evidence", "assistant_summary": "当前没有可 adopt 的 stale evidence。"}
        patch = await self._handle_user_patch(
            text=text,
            turn=turn,
            asr_frame=asr_frame,
            thinker_frame=thinker_frame,
            router_event=router_event,
            candidate_patch_types=("feedback_candidate",),
            patch_kind="feedback",
        )
        task_state, _, _ = self._projections()
        task = task_state.tasks[task.task_id]
        stale_ref = task.stale_evidence_refs[-1]
        pending = next(
            item for item in reversed(task.pending_stale_tool_results) if item.stale_evidence_ref == stale_ref
        )
        adopted = self._slowtask_runtime.adopt_stale_evidence_for_commitment(
            task_id=task.task_id,
            plan_version=task.current_plan_version,
            caused_by_event_id=str(task.last_slowtask_event_id),
            event_id_prefix=self._next_id("adopt_stale"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=task.current_task_event_seq + 1,
            stale_evidence_ref=stale_ref,
            source_tool_result_event_id=pending.source_tool_result_event_id,
            adopted_from_plan_version=pending.result_plan_version,
            adoption_reason="user explicitly requested adopt stale demo result",
            adopted_scope=("itinerary_options", "tool_result_summary"),
            adopted_by_event_id=str(task.last_slowtask_event_id),
            resolved_arguments_ref=self._next_ref("args", "adopted"),
            provenance_ref=self._next_ref("provenance", "adopted"),
            field_provenance_refs=(stale_ref,),
            commitment_id=self._next_id("commitment"),
            commitment_ref=self._next_ref("commitment", "adopted"),
            current_lifecycle_state=task.lifecycle_state,
        )
        return {
            "status": "stale_evidence_adopted",
            "assistant_summary": "SlowTask 已显式 adopt/rebase stale evidence，并提交当前计划的 SemanticCommitment。",
        }

    async def _handle_complete_current(
        self,
        *,
        text: str,
        router_event: Mapping[str, Any],
    ) -> dict[str, str]:
        task_state, focus_state, _ = self._projections()
        task = self._task_for_focus(task_state, focus_state)
        if task is None:
            return {"status": "no_active_task", "assistant_summary": "当前没有可完成的 SlowTask。"}
        finalized = self._slowtask_runtime.finalize_current_task(
            task_id=task.task_id,
            plan_version=task.current_plan_version,
            current_lifecycle_state=task.lifecycle_state,
            caused_by_event_id=str(task.last_slowtask_event_id),
            event_id_prefix=self._next_id("finalize"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=task.current_task_event_seq + 1,
            commitment_id=self._next_id("commitment"),
            commitment_ref=self._next_ref("commitment", "current"),
        )
        clear_mono, clear_wall = self._clock.reserve()
        MVP1TaskFocusUpdateEmitter(self._journal).emit_update(
            router_decision_event=router_event,
            event_id=self._next_id("task_focus_cleared"),
            created_monotonic_ms=clear_mono,
            created_wall_clock_ms=clear_wall,
            active_task_id=None,
            foreground_mode="IDLE",
            default_patch_policy="NO_ACTIVE_TASK",
        )
        return {
            "status": "completed",
            "assistant_summary": "当前 plan 已由 SlowTask finalize，并发出 SemanticCommitment。",
        }

    def _handle_foreground_chat(self, *, text: str, decision: str, task_focus: str) -> dict[str, str]:
        return {
            "status": "foreground_chat",
            "assistant_summary": f"Router={decision}, task_focus={task_focus}；这条消息没有改写 SlowTask facts。",
        }

    async def _call_codex(
        self,
        *,
        intent: str,
        slowtask_event: Mapping[str, Any],
        source_evidence_refs: Sequence[str],
    ) -> Any:
        context = self._context_pack().to_dict()
        start_mono, start_wall = self._clock.reserve()
        self._record_live_progress(
            kind="context_pack_ready",
            status="ready",
            phase="codex",
            label="SlowTask context 已准备",
            detail="只把受边界约束的 context pack 交给 Codex adapter。",
        )
        self._publish_stable_snapshot_locked()
        result = await self._codex_adapter.propose(
            task_context_pack=context,
            intent=intent,
            slowtask_event=slowtask_event,
            source_evidence_refs=source_evidence_refs,
            event_id_prefix=self._next_id("codex"),
            created_monotonic_ms=start_mono,
            created_wall_clock_ms=start_wall,
            on_progress=self._receive_provider_progress,
        )
        self._append_provider_trace(result.trace_items, base_monotonic_ms=start_mono)
        self._record_live_progress(
            kind="structured_output_validated",
            status="validated",
            phase="validation",
            label="Codex structured output 已校验",
            detail="proposal 仍是 evidence candidate，不推进 plan_version，也不授权工具。",
            task_id=_optional_str(slowtask_event.get("task_id")),
            plan_version=_optional_int(slowtask_event.get("plan_version")),
            result_present=True,
        )
        self._publish_stable_snapshot_locked()
        return result

    async def _receive_provider_progress(self, item: Mapping[str, Any]) -> None:
        self._record_live_progress(
            kind=str(item.get("kind", "provider_event")),
            status=str(item.get("status", "observed")),
            phase=_provider_progress_phase(item),
            label=_provider_progress_label(item),
            detail=_provider_progress_detail(item),
            task_id=_optional_str(item.get("task_id")),
            plan_version=_optional_int(item.get("plan_version")),
            tool_name=_optional_str(item.get("tool_name")),
            proposal_only=_optional_bool(item.get("proposal_only")),
            result_present=_optional_bool(item.get("result_present")),
            usage=item.get("usage") if isinstance(item.get("usage"), Mapping) else None,
            latency_ms=_optional_int(item.get("latency_ms")),
            provider_mode=str(item.get("provider_mode", "codex_cli_local")),
            output_mode=str(item.get("output_mode", "real")),
        )

    def _begin_live_stream(self) -> None:
        self._live_provider_progress = []
        self._stream_sequence = 0
        self._stream_active = True
        self._stream_phase = "routing"
        self._stream_label = "正在接收并路由本轮输入"
        self._stream_detail = "实时进度只展示 allow-listed 的阶段、工具状态和校验结果。"
        self._record_live_progress(
            kind="turn_started",
            status="started",
            phase="routing",
            label="已收到新的用户消息",
            detail="正在进入 Interaction Controller 和 Router。",
        )

    def _record_live_progress(
        self,
        *,
        kind: str,
        status: str,
        phase: str,
        label: str,
        detail: str = "",
        task_id: str | None = None,
        plan_version: int | None = None,
        tool_name: str | None = None,
        proposal_only: bool | None = None,
        result_present: bool | None = None,
        usage: Mapping[str, Any] | None = None,
        latency_ms: int | None = None,
        provider_mode: str | None = None,
        output_mode: str | None = None,
    ) -> None:
        self._stream_sequence += 1
        normalized_usage = (
            {
                str(key): int(value)
                for key, value in usage.items()
                if isinstance(value, int) and not isinstance(value, bool)
            }
            if isinstance(usage, Mapping)
            else None
        )
        item = ProviderTraceItem(
            trace_id=f"live_progress_{self.session_id}_{self._stream_sequence:04d}",
            sequence=self._stream_sequence,
            kind=_safe_progress_token(kind),
            status=_safe_progress_token(status),
            provider_mode=provider_mode or self._config.provider_mode,
            output_mode=output_mode or "real",
            task_id=task_id,
            plan_version=plan_version,
            created_monotonic_ms=self._clock.monotonic_ms,
            tool_name=_safe_progress_token(tool_name) if tool_name else None,
            proposal_only=proposal_only,
            result_present=result_present,
            usage=normalized_usage,
            latency_ms=latency_ms,
            detail=_safe_progress_text(detail),
            phase=_safe_progress_token(phase),
            label=_safe_progress_text(label),
        )
        self._live_provider_progress.append(item)
        self._stream_phase = item.phase or phase
        self._stream_label = item.label or label
        self._stream_detail = item.detail or detail

    def _publish_stable_snapshot_locked(self) -> None:
        self._stable_snapshot = deepcopy(self._snapshot().to_dict())

    def _streaming_payload(self) -> dict[str, Any]:
        return {
            "active": self._stream_active,
            "phase": self._stream_phase,
            "label": self._stream_label,
            "detail": self._stream_detail,
            "sequence": self._stream_sequence,
        }

    def _live_snapshot(self) -> dict[str, Any]:
        base = deepcopy(self._stable_snapshot)
        base["snapshot_id"] = (
            f"{base.get('snapshot_id', f'snapshot_{self.session_id}_00000000')}"
            f":stream:{self._stream_sequence:04d}"
        )
        base["live_progress"] = [item.to_dict() for item in self._live_provider_progress[-80:]]
        base["streaming"] = self._streaming_payload()
        return base

    def _append_provider_trace(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        base_monotonic_ms: int,
    ) -> None:
        for item in items:
            sequence = len(self._provider_trace) + 1
            usage = item.get("usage")
            normalized_usage = (
                {str(key): int(value) for key, value in usage.items() if isinstance(value, int) and not isinstance(value, bool)}
                if isinstance(usage, Mapping)
                else None
            )
            self._provider_trace.append(
                ProviderTraceItem(
                    trace_id=f"provider_trace_{self.session_id}_{sequence:04d}",
                    sequence=sequence,
                    kind=str(item.get("kind", "provider_event")),
                    status=str(item.get("status", "observed")),
                    provider_mode=str(item.get("provider_mode", self._config.provider_mode)),
                    output_mode=str(item.get("output_mode", "degraded")),
                    task_id=_optional_str(item.get("task_id")),
                    plan_version=_optional_int(item.get("plan_version")),
                    created_monotonic_ms=base_monotonic_ms + sequence,
                    tool_name=_optional_str(item.get("tool_name")),
                    proposal_only=_optional_bool(item.get("proposal_only")),
                    result_present=_optional_bool(item.get("result_present")),
                    usage=normalized_usage,
                    latency_ms=_optional_int(item.get("latency_ms")),
                    detail=_optional_str(item.get("detail")),
                )
            )

    def _context_pack(self):
        slowtask_state, focus_state, tool_state = self._projections()
        active_or_last = self._task_for_context(slowtask_state)
        task_created_event_id = None
        if active_or_last is not None:
            task_created_event_id = next(
                (
                    str(event["event_id"])
                    for event in self._journal.events()
                    if event.get("event_name") == "SLOWTASK_CREATED"
                    and event.get("task_id") == active_or_last.task_id
                ),
                None,
            )
        return TaskContextPackBuilder().build(
            slowtask_state=slowtask_state,
            task_focus_state=focus_state,
            tool_execution_state=tool_state,
            evidence_catalog=self._evidence_catalog,
            conversation=self._conversation,
            latest_user_input=self._last_user_input,
            resolved_argument_values=self._resolved_argument_values,
            task_created_event_id=task_created_event_id,
        )

    @staticmethod
    def _task_for_context(state: SlowTaskState) -> SlowTaskRecord | None:
        active = [task for task in state.tasks.values() if not task.is_terminal]
        if active:
            return sorted(active, key=lambda item: item.task_id)[0]
        if state.last_task_id is not None:
            return state.tasks.get(state.last_task_id)
        return None

    def _snapshot(self) -> WorkbenchSnapshot:
        slowtask_state, focus_state, tool_state = self._projections()
        return project_workbench_snapshot(
            session_id=self.session_id,
            mode="text_first_python_owned",
            events=self._journal.events(),
            slowtask_state=slowtask_state,
            task_focus_state=focus_state,
            tool_execution_state=tool_state,
            context_pack=self._context_pack(),
            conversation=self._conversation,
            evidence_catalog=self._evidence_catalog,
            provider_trace=self._provider_trace,
            codex_proposals=self._codex_proposals,
            capability_snapshot=self._capability_snapshot,
            capability_matrices=self._capability_matrices,
            live_progress=self._live_provider_progress,
            streaming=self._streaming_payload(),
        )

    def _projections(self) -> tuple[SlowTaskState, TaskFocusState, ToolExecutionState]:
        slowtask_state = SlowTaskState()
        focus_state = TaskFocusState()
        tool_state = ToolExecutionState()
        for event in self._journal.events():
            slowtask_state.reduce_event(event)
            focus_state.reduce_event(event)
            tool_state.reduce_event(event)
        return slowtask_state, focus_state, tool_state

    def _task_focus_snapshot(
        self,
        task_state: SlowTaskState,
        focus_state: TaskFocusState,
    ) -> TaskFocusSnapshot:
        task = self._task_for_focus(task_state, focus_state)
        if task is None or task.is_terminal:
            return TaskFocusSnapshot()
        return TaskFocusSnapshot(
            active_task_id=task.task_id,
            lifecycle_phase=task.lifecycle_state,
            terminal_status=None,
            current_plan_version=task.current_plan_version,
            pending_confirmation_scope=task.confirmation_state.confirmation_scope,
        )

    def _task_for_focus(
        self,
        task_state: SlowTaskState,
        focus_state: TaskFocusState,
    ) -> SlowTaskRecord | None:
        if focus_state.active_task_id and focus_state.active_task_id in task_state.tasks:
            task = task_state.tasks[focus_state.active_task_id]
            if not task.is_terminal:
                return task
        active = [task for task in task_state.tasks.values() if not task.is_terminal]
        return sorted(active, key=lambda item: item.task_id)[0] if active else None

    def _handle_for_task(self, task_id: str) -> ToolExecutionHandle | None:
        for handle in self._in_flight_handles.values():
            if handle.task_id == task_id:
                return handle
        return None

    def _latest_text_input_event(self, *, turn_id: str) -> Mapping[str, Any]:
        opened = next(
            (
                event
                for event in reversed(self._journal.events())
                if event.get("event_name") == "TURN_OPENED" and event.get("turn_id") == turn_id
            ),
            None,
        )
        if opened is not None:
            text_span_id = opened.get("text_span_id")
            if text_span_id:
                for event in reversed(self._journal.events()):
                    if (
                        event.get("event_name") == "TEXT_INPUT_RECEIVED"
                        and event.get("text_span_id") == text_span_id
                    ):
                        return event
        for event in reversed(self._journal.events()):
            if event.get("event_name") == "TEXT_INPUT_RECEIVED":
                return event
        raise ValueError("missing TEXT_INPUT_RECEIVED event for current turn")

    def _record_evidence(
        self,
        evidence_ref: str,
        *,
        task_id: str,
        plan_version: int,
        label: str,
        summary: str,
        source: str,
        trust_level: str,
        stale: bool = False,
    ) -> None:
        self._evidence_catalog[evidence_ref] = {
            "evidence_id": evidence_ref,
            "evidence_ref": evidence_ref,
            "task_id": task_id,
            "plan_version": plan_version,
            "label": _safe_summary(label),
            "summary": _safe_summary(summary),
            "source": source,
            "trust_level": trust_level,
            "provenance": "event_journal_projection",
            "stale": stale,
        }

    def _next_id(self, label: str) -> str:
        self._id_counter += 1
        return f"evt_{self.session_id}_{self._reset_generation + 1}_{label}_{self._id_counter:05d}"

    def _next_ref(self, namespace: str, label: str) -> str:
        self._id_counter += 1
        return f"{namespace}://synthetic/{self.session_id}/{self._reset_generation + 1}/{label}_{self._id_counter:05d}"


class WorkbenchSessionManager:
    def __init__(self, *, default_config: WorkbenchRuntimeConfig | None = None) -> None:
        self._default_config = default_config or WorkbenchRuntimeConfig.from_environment()
        self._sessions: dict[str, WorkbenchSession] = {}
        self._next_session_index = 1
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        *,
        session_id: str | None = None,
        config: WorkbenchRuntimeConfig | None = None,
    ) -> WorkbenchSession:
        async with self._lock:
            resolved_id = _safe_token(
                session_id or f"workbench_{self._next_session_index:04d}",
                "session_id",
            )
            if resolved_id in self._sessions:
                raise ValueError("session_id already exists")
            self._next_session_index += 1
            session = WorkbenchSession(session_id=resolved_id, config=config or self._default_config)
            self._sessions[resolved_id] = session
            return session

    async def get(self, session_id: str) -> WorkbenchSession:
        session_id = _safe_token(session_id, "session_id")
        async with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise KeyError(f"unknown workbench session: {session_id}") from exc


def _resolve_action(
    text: str,
    action: str | None,
    *,
    task_state: SlowTaskState,
    focus_state: TaskFocusState,
) -> str:
    if action:
        aliases = {
            "start_new_task": "start",
            "send_user_patch": "material_patch",
            "receive_late_tool_result": "material_patch",
            "request_cancel_confirmation": "cancel_candidate",
            "foreground_chat": "foreground",
            "material_patch": "material_patch",
            "cancel_candidate": "cancel_candidate",
            "confirmation": "confirmation",
            "adopt_stale_evidence": "adopt_stale_evidence",
            "complete_current": "complete_current",
        }
        if action in aliases:
            return aliases[action]
    lowered = text.lower()
    if any(marker in lowered for marker in ("采用旧", "adopt stale", "use old result", "复用旧结果")):
        return "adopt_stale_evidence"
    if any(marker in lowered for marker in ("取消", "不要了", "停止任务", "cancel", "stop this")):
        return "cancel_candidate"
    if any(marker in lowered for marker in ("完成", "提交方案", "finalize", "complete")):
        return "complete_current"
    if not any(not task.is_terminal for task in task_state.tasks.values()):
        return "start"
    if focus_state.active_task_id is not None:
        if any(marker in lowered for marker in ("你好", "谢谢", "hello", "thanks", "闲聊", "chat")):
            return "foreground"
        return "material_patch"
    return "start"


def _frame_hints(action: str) -> tuple[str, bool, str]:
    if action == "start":
        return "NEW_TASK_CANDIDATE", True, "complex"
    if action == "cancel_candidate":
        return "CANCEL_OR_PAUSE_CANDIDATE", False, "task"
    if action == "confirmation" or action in {"material_patch", "adopt_stale_evidence", "complete_current"}:
        return "ACTIVE_TASK_PATCH", False, "task"
    return "FOREGROUND_CHAT", False, "simple"


def _provider_progress_phase(item: Mapping[str, Any]) -> str:
    kind = str(item.get("kind", "provider_event")).lower()
    if item.get("tool_name") or "tool" in kind:
        return "tool"
    if "degrad" in kind or str(item.get("output_mode", "")).lower() == "degraded":
        return "fallback"
    if "structur" in kind or "validat" in kind:
        return "validation"
    if "request" in kind or "thread" in kind or "turn" in kind or "item" in kind:
        return "codex"
    return "codex"


def _provider_progress_label(item: Mapping[str, Any]) -> str:
    kind = str(item.get("kind", "provider_event")).lower()
    tool_name = _optional_str(item.get("tool_name"))
    if kind == "request_started":
        return "已建立 Codex CLI 请求"
    if kind in {"thread.started", "thread_started"}:
        return "Codex 已启动本地执行线程"
    if kind in {"turn.started", "turn_started"}:
        return "Codex 正在分析当前任务"
    if kind in {"item.started", "item_started"}:
        return f"Codex 开始处理工具步骤：{tool_name}" if tool_name else "Codex 开始处理一个步骤"
    if kind in {"item.completed", "item_completed"}:
        return "Codex 完成一个步骤"
    if kind == "provider_completed":
        return "Codex CLI 已返回结构化候选"
    if kind == "structured_output_emitted":
        return "结构化输出已通过校验"
    if kind == "provider_degraded":
        return "Codex 暂不可用，已切换到受控 fallback"
    if kind == "stderr_classified":
        return "provider 诊断已分类"
    return "Codex provider 进度已更新"


def _provider_progress_detail(item: Mapping[str, Any]) -> str:
    kind = str(item.get("kind", "provider_event")).lower()
    if kind in {"item.started", "item_started"} and item.get("tool_name"):
        return "页面只展示工具名称和状态，不展示 provider 原始输出。"
    if kind in {"item.completed", "item_completed"}:
        return "该事件已被转换为安全的高层进度摘要。"
    if kind == "provider_completed":
        return "原始 provider body 不进入 snapshot；只保留校验后的 proposal candidate。"
    if kind == "structured_output_emitted":
        return "Codex proposal 仍不拥有 SlowTask facts，也不会直接推进 plan_version。"
    if kind == "provider_degraded":
        return "fallback 结果会在 provider_trace 中标注为 degraded。"
    return ""


def _safe_progress_token(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char in "._:-/" else "_" for char in str(value).lower())
    return normalized[:80] or "provider_event"


def _safe_progress_text(value: str) -> str:
    return _safe_summary(str(value)) if value else ""


def _safe_token(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or not _SAFE_TOKEN_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be a safe non-empty token")
    return value


def _safe_user_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("message text must be non-empty")
    normalized = " ".join(value.split())[:SAFE_TEXT_MAX_LENGTH]
    if _UNSAFE_INPUT_PATTERN.search(normalized):
        return "[redacted unsafe user input]"
    return normalized


def _safe_summary(value: str) -> str:
    normalized = " ".join(str(value).split())
    if _UNSAFE_INPUT_PATTERN.search(normalized):
        return "[redacted unsafe metadata]"
    return normalized[:SAFE_SUMMARY_MAX_LENGTH]


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


__all__ = [
    "WorkbenchRuntimeConfig",
    "WorkbenchSession",
    "WorkbenchSessionManager",
]
