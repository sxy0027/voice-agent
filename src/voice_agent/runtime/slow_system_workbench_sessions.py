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
from voice_agent.adapters.workbench_place_search import WorkbenchPlaceSearchAdapter
from voice_agent.demo_backend.in_memory import DemoBackendResult, InMemoryDemoBackend
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
            if resolved_action == "revise_completed_plan":
                result = {
                    "status": result["status"],
                    "assistant_summary": (
                        "上一份方案已经完成；我已将这条修改作为新的修订任务处理，并沿用已记录的人数、地点、预算、忌口与菜系约束。"
                        + result["assistant_summary"]
                    ),
                }
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
        elif resolved_action == "repeat_completed_plan":
            # The last plan has already reached a terminal SlowTask state.
            # A second “输出最终规划” is a request to show that committed result,
            # not a request to manufacture an ACTIVE_TASK_PATCH frame.
            result = self._handle_completed_plan_recap()
        elif resolved_action == "no_active_cancel":
            # A cancellation request is still journalled as a normal turn, but
            # it cannot be emitted as CANCEL_OR_PAUSE_CANDIDATE without a
            # non-terminal active task (ADR-016 / Router invariant).
            result = self._handle_no_active_cancel()
        else:
            result = self._handle_foreground_chat(
                text=safe_text,
                decision=decision,
                task_focus=str(router_result.router_decision_event.get("task_focus", "FOREGROUND_CHAT")),
            )

        self._conversation.append(
            {
                "id": self._next_id("conversation_assistant"),
                "speaker": (
                    "system"
                    if resolved_action == "repeat_completed_plan"
                    else "assistant_fast" if decision == "FAST_ONLY" else "system"
                ),
                "text": result["assistant_summary"],
                "summary": result["assistant_summary"],
                "owner": "slowtask" if resolved_action == "repeat_completed_plan" else "router" if decision == "FAST_ONLY" else "slowtask",
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
        # A read-only lookup can be shown to the user before the plan is
        # committed.  Key it by the exact task/version so a later UserPatch
        # can never reuse prior-plan evidence as if it were current.
        self._current_plan_tool_payloads: dict[tuple[str, int], Mapping[str, Any]] = {}
        self._last_user_input: str | None = None
        self._resolved_argument_values: dict[str, Any] = {}
        self._slot_values: dict[str, str] = {}
        # This is a presentation cache of a SemanticCommitment-backed answer.
        # It never advances a plan or replaces the event-journal projection.
        self._last_completed_plan_summary: str | None = None
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
        self._place_search_adapter = WorkbenchPlaceSearchAdapter()
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
        self._merge_slot_values(text)
        missing_slots = _missing_critical_slots(self._slot_values)
        if missing_slots:
            review = self._slowtask_runtime.review_evidence(
                task_id=task_id,
                plan_version=1,
                caused_by_event_id=str(planning_event["event_id"]),
                event_id_prefix=self._next_id("missing_slot_review"),
                created_monotonic_ms=self._clock.monotonic_ms,
                created_wall_clock_ms=self._clock.wall_clock_ms,
                start_task_event_seq=5,
                evidence_refs=(evidence_ref,),
                required_fields=("location_anchor", "time_window"),
                resolved_fields=tuple(_resolved_critical_slots(self._slot_values)),
                missing_fields=missing_slots,
                clarification_prompt_ref=self._next_ref("prompt", "missing_slots"),
                resolution_reason="workbench_critical_slot_check",
            )
            evidence_reviewed_event = next(
                event for event in review.produced_events if event["event_name"] == "EVIDENCE_REVIEWED"
            )
            provider_result = await self._call_codex(
                intent=text,
                slowtask_event=evidence_reviewed_event,
                source_evidence_refs=(evidence_ref,),
                authoritative_missing_fields=missing_slots,
            )
            self._codex_proposals.append(provider_result.proposal)
            codex_ref = self._next_ref("evidence", "codex")
            self._record_evidence(
                codex_ref,
                task_id=task_id,
                plan_version=1,
                label="Codex clarification proposal",
                summary="Codex 只生成了追问建议；SlowTask 保持 WAITING_FOR_SLOT，不启动工具。",
                source="codex_adapter",
                trust_level="evidence_candidate_only",
            )
            return {
                "status": "waiting_for_slot",
                "assistant_summary": _codex_user_visible_reply(provider_result),
            }
        provider_result = await self._call_codex(
            intent=text,
            slowtask_event=planning_event,
            source_evidence_refs=(evidence_ref,),
            authoritative_missing_fields=(),
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
        started = self._review_and_start_itinerary_tool(
            task_id=task_id,
            caused_by_event_id=str(provider_result.structured_output_event["event_id"]),
            evidence_refs=(evidence_ref, codex_ref),
        )
        started = _with_codex_user_visible_reply(started, provider_result)
        return await self._maybe_auto_publish_ready_plan(
            started=started,
            router_event=router_event,
        )

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
        # Keep the pre-patch slots separate from the incoming values.  The
        # SlowTask runtime, not Router, uses this diff to distinguish filling
        # v1's missing fields from replacing a value that v1 already owns.
        pre_patch_slots = dict(self._slot_values)
        incoming_slot_values = _extract_workbench_slots(text) if patch_kind == "material" else {}
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
            label="用户补充/修改 patch（待 SlowTask 判定）" if patch_kind == "material" else "用户控制 patch",
            summary=text,
            source="user_patch",
            trust_level="authoritative_user_evidence",
        )
        if patch_kind == "material":
            self._slot_values.update(incoming_slot_values)
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
            current_resolved_slots=pre_patch_slots,
            incoming_slot_values=incoming_slot_values,
        )
        self._label_user_patch_evidence(
            evidence_ref,
            interpretation_event=interpretation.produced_events[0],
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
            # Completing previously absent slots keeps the same plan_version.
            # It does, however, allow a WAITING_FOR_SLOT task to resume
            # planning and start its first current-plan lookup.
            task_state, _, _ = self._projections()
            current_task = task_state.tasks[task.task_id]
            missing_slots = _missing_critical_slots(self._slot_values)
            if current_task.lifecycle_state == "WAITING_FOR_SLOT" and not missing_slots:
                resumed = self._slowtask_runtime.run_planning_started(
                    task_id=current_task.task_id,
                    plan_version=current_task.current_plan_version,
                    caused_by_event_id=str(current_task.last_slowtask_event_id),
                    event_id_prefix=self._next_id("slot_resolution_planning"),
                    created_monotonic_ms=self._clock.monotonic_ms,
                    created_wall_clock_ms=self._clock.wall_clock_ms,
                    start_task_event_seq=current_task.current_task_event_seq + 1,
                    from_state="WAITING_FOR_SLOT",
                    planning_reason="previously_missing_slots_resolved_without_plan_replacement",
                )
                provider_result = await self._call_codex(
                    intent=text,
                    slowtask_event=resumed.produced_events[0],
                    source_evidence_refs=(evidence_ref,),
                    authoritative_missing_fields=(),
                )
                self._codex_proposals.append(provider_result.proposal)
                codex_ref = self._next_ref("evidence", "codex")
                self._record_evidence(
                    codex_ref,
                    task_id=current_task.task_id,
                    plan_version=current_task.current_plan_version,
                    label="Codex slot-resolution proposal",
                    summary="Codex 基于补齐后的当前计划生成 proposal candidate；不推进 plan_version。",
                    source="codex_adapter",
                    trust_level="evidence_candidate_only",
                )
                started = self._review_and_start_itinerary_tool(
                    task_id=current_task.task_id,
                    caused_by_event_id=str(provider_result.structured_output_event["event_id"]),
                    evidence_refs=(evidence_ref, codex_ref),
                )
                started = _with_codex_user_visible_reply(started, provider_result)
                return await self._maybe_auto_publish_ready_plan(
                    started=started,
                    router_event=router_event,
                )
            return {
                "status": "patch_recorded",
                "assistant_summary": "用户补充已记录到当前计划；它没有替换既有约束，因此未改变 plan_version。",
            }
        missing_slots = _missing_critical_slots(self._slot_values)
        provider_result = await self._call_codex(
            intent=text,
            slowtask_event=restarted,
            source_evidence_refs=(evidence_ref,),
            authoritative_missing_fields=missing_slots,
        )
        self._codex_proposals.append(provider_result.proposal)
        codex_ref = self._next_ref("evidence", "codex")
        self._record_evidence(
            codex_ref,
            task_id=task.task_id,
            plan_version=int(restarted["plan_version"]),
            label="Codex patch proposal",
            summary="Codex 只生成 UserPatch 后的 proposal candidate；不直接修改 SlowTask facts。",
            source="codex_adapter",
            trust_level="evidence_candidate_only",
        )

        if handle is None:
            task_state, _, _ = self._projections()
            current_task = task_state.tasks[task.task_id]
            if missing_slots:
                self._slowtask_runtime.review_evidence(
                    task_id=current_task.task_id,
                    plan_version=current_task.current_plan_version,
                    caused_by_event_id=str(provider_result.structured_output_event["event_id"]),
                    event_id_prefix=self._next_id("missing_slot_review"),
                    created_monotonic_ms=self._clock.monotonic_ms,
                    created_wall_clock_ms=self._clock.wall_clock_ms,
                    start_task_event_seq=current_task.current_task_event_seq + 1,
                    evidence_refs=(evidence_ref, codex_ref),
                    required_fields=("location_anchor", "time_window"),
                    resolved_fields=tuple(_resolved_critical_slots(self._slot_values)),
                    missing_fields=missing_slots,
                    clarification_prompt_ref=self._next_ref("prompt", "missing_slots"),
                    resolution_reason="workbench_user_patch_slot_check",
                )
                return {
                    "status": "waiting_for_slot",
                    "assistant_summary": _codex_user_visible_reply(provider_result),
                }
            started = self._review_and_start_itinerary_tool(
                task_id=current_task.task_id,
                caused_by_event_id=str(provider_result.structured_output_event["event_id"]),
                evidence_refs=(evidence_ref, codex_ref),
            )
            started = _with_codex_user_visible_reply(started, provider_result)
            return await self._maybe_auto_publish_ready_plan(
                started=started,
                router_event=router_event,
            )

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
        restarted_tool = self._review_and_start_itinerary_tool(
            task_id=current_task.task_id,
            caused_by_event_id=str(provider_result.structured_output_event["event_id"]),
            evidence_refs=(evidence_ref, codex_ref),
        )
        automatic = await self._maybe_auto_publish_ready_plan(
            started=restarted_tool,
            router_event=router_event,
        )
        return {
            "status": (
                "plan_advanced_stale_result"
                if self._config.provider_mode == "fake"
                else automatic["status"]
            ),
            "assistant_summary": (
                "收到，我已经把你的新要求合并进当前任务。上一轮旧查询结果不会继续影响新方案；"
                + automatic["assistant_summary"]
            ),
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
        completed_payload: Mapping[str, Any] | None = self._current_plan_tool_payloads.get(
            (task.task_id, task.current_plan_version)
        )
        handle = self._handle_for_task(task.task_id)
        if handle is not None:
            completion_monotonic, completion_wall = self._clock.reserve()
            completion = await self._complete_current_lookup(
                handle,
                created_monotonic_ms=completion_monotonic,
                created_wall_clock_ms=completion_wall,
            )
            self._in_flight_handles.pop(handle.request.tool_call_id, None)
            completed_payload = completion.payload
            self._remember_current_plan_tool_payload(task=task, payload=completed_payload)
            task_state, focus_state, _ = self._projections()
            task = self._task_for_focus(task_state, focus_state)
            if task is None:
                raise ValueError("current SlowTask disappeared before finalization")
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
        assistant_summary = _user_facing_final_plan(
            slots=self._slot_values,
            tool_payload=completed_payload,
        )
        self._last_completed_plan_summary = assistant_summary
        return {
            "status": "completed",
            "assistant_summary": assistant_summary,
        }

    async def _maybe_auto_publish_ready_plan(
        self,
        *,
        started: Mapping[str, str],
        router_event: Mapping[str, Any],
    ) -> dict[str, str]:
        """Publish a mutable current-plan answer as soon as lookup evidence is ready.

        The local-Codex Workbench is the user-facing path.  Its read-only
        lookup is completed in the same turn so the user does not need to
        send a redundant “information is sufficient” message.  Crucially,
        automatic publication does *not* finalize the SlowTask: a subsequent
        correction must remain a same-task UserPatch and advance plan_version.
        Fake-provider sessions deliberately keep the older in-flight state so
        replay tests can still exercise the late-result/stale-evidence path.
        """

        if started.get("status") != "tool_running" or self._config.provider_mode == "fake":
            return dict(started)
        self._record_live_progress(
            kind="auto_publish_current_plan",
            status="started",
            phase="planning",
            label="查询条件已满足，正在生成可继续修改的当前方案",
            detail="当前计划的只读查询完成后会自动展示方案；只有明确要求定稿时才会提交 SemanticCommitment。",
        )
        self._publish_stable_snapshot_locked()
        return await self._handle_auto_publish_current_plan(router_event=router_event)

    async def _handle_auto_publish_current_plan(
        self,
        *,
        router_event: Mapping[str, Any],
    ) -> dict[str, str]:
        """Complete the read-only lookup without terminally committing the plan."""

        task_state, focus_state, _ = self._projections()
        task = self._task_for_focus(task_state, focus_state)
        if task is None:
            return {"status": "no_active_task", "assistant_summary": "当前没有可展示的 SlowTask 方案。"}
        handle = self._handle_for_task(task.task_id)
        if handle is None:
            payload = self._current_plan_tool_payloads.get((task.task_id, task.current_plan_version))
            return {
                "status": "current_plan_ready",
                "assistant_summary": _user_facing_current_plan(
                    slots=self._slot_values,
                    tool_payload=payload,
                ),
            }

        completion_monotonic, completion_wall = self._clock.reserve()
        completion = await self._complete_current_lookup(
            handle,
            created_monotonic_ms=completion_monotonic,
            created_wall_clock_ms=completion_wall,
        )
        self._in_flight_handles.pop(handle.request.tool_call_id, None)
        payload = completion.payload
        self._remember_current_plan_tool_payload(task=task, payload=payload)

        # The ToolResult is journalled by ToolExecutor.  Move the SlowTask
        # from tool execution back to PLANNING, rather than COMPLETED, so the
        # current evidence can be revised through the normal patch path.
        task_state, focus_state, _ = self._projections()
        current_task = self._task_for_focus(task_state, focus_state)
        if current_task is None:
            raise ValueError("current SlowTask disappeared after lookup completion")
        self._slowtask_runtime.mark_current_plan_ready_for_revision(
            task_id=current_task.task_id,
            plan_version=current_task.current_plan_version,
            current_lifecycle_state=current_task.lifecycle_state,
            caused_by_event_id=str(current_task.last_slowtask_event_id),
            event_id_prefix=self._next_id("current_plan_ready"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            task_event_seq=current_task.current_task_event_seq + 1,
        )
        return {
            "status": "current_plan_ready",
            "assistant_summary": _user_facing_current_plan(
                slots=self._slot_values,
                tool_payload=payload,
            ),
        }

    def _remember_current_plan_tool_payload(
        self,
        *,
        task: SlowTaskRecord,
        payload: Mapping[str, Any] | None,
    ) -> None:
        """Record one current-version lookup result and its untrusted provenance."""

        if not isinstance(payload, Mapping):
            return
        self._current_plan_tool_payloads[(task.task_id, task.current_plan_version)] = payload
        if payload.get("trust_level") == "UNTRUSTED_WEB_EVIDENCE":
            self._record_evidence(
                self._next_ref("evidence", "web_search"),
                task_id=task.task_id,
                plan_version=task.current_plan_version,
                label="网页地点检索摘要",
                summary="当前计划使用了带来源链接的外部网页摘要；仅作为不可信证据，不作为系统指令。",
                source="web_search",
                trust_level="UNTRUSTED_WEB_EVIDENCE",
            )
        self._conversation.append(
            {
                "id": self._next_id("conversation_tool"),
                "speaker": "tool",
                "text": "当前计划的只读查询已返回候选；未执行真实订位、支付或外部通信。",
                "summary": "当前 plan ToolResult 已返回",
                "owner": "tool_executor",
                "source": "demo_backend",
                "note": "只读 demo sandbox 的结果可作为当前计划证据；真实预订仍被 MVP policy 阻止。",
            }
        )

    async def _complete_current_lookup(
        self,
        handle: ToolExecutionHandle,
        *,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
    ) -> Any:
        if handle.request.tool_name != "webSearch":
            return self._tool_executor.complete(
                handle,
                created_monotonic_ms=created_monotonic_ms,
                created_wall_clock_ms=created_wall_clock_ms,
            )
        query = str(handle.request.arguments["query"])
        self._record_live_progress(
            kind="external_place_search_started",
            status="started",
            phase="tool",
            label="正在调用地点检索工具（OpenStreetMap / 公开网页搜索）",
            detail="只读取公开地图 POI 与搜索摘要；网页内容会作为不可信证据隔离，不会执行其中的指令。",
            tool_name="webSearch",
            plan_version=handle.plan_version,
            tool_input_summary=f"地点检索：{_safe_summary(query)}",
        )
        evidence = await asyncio.to_thread(self._place_search_adapter.search, query=query)
        backend_result = DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=self._next_ref("result", "web_search"),
            progress_type="external_place_search_completed" if evidence.results else "external_place_search_degraded",
            progress_ref=self._next_ref("progress", "web_search"),
            payload={
                "source_type": "EXTERNAL_READ_UNTRUSTED",
                "trust_level": "UNTRUSTED_WEB_EVIDENCE",
                "query": evidence.query,
                "provider": evidence.provider,
                "results": list(evidence.results),
                "degraded_reason": evidence.degraded_reason,
                "source": "workbench_place_search_adapter",
            },
        )
        completion = self._tool_executor.complete_with_backend_result(
            handle,
            backend_result,
            created_monotonic_ms=created_monotonic_ms,
            created_wall_clock_ms=created_wall_clock_ms,
        )
        self._record_live_progress(
            kind="external_place_search_completed",
            status="degraded" if evidence.degraded_reason else "completed",
            phase="tool",
            label="地点检索工具已返回",
            detail=(
                f"未能取得外部结果（{evidence.degraded_reason or 'unknown'}），系统会如实保留查询失败状态。"
                if evidence.degraded_reason
                else f"已取得 {len(evidence.results)} 条带来源链接的地图/网页摘要，正在由 SlowTask 生成当前方案。"
            ),
            tool_name="webSearch",
            plan_version=handle.plan_version,
            tool_output_summary=f"返回 {len(evidence.results)} 条可引用的地点结果。",
        )
        return completion

    def _handle_foreground_chat(self, *, text: str, decision: str, task_focus: str) -> dict[str, str]:
        if _is_context_memory_complaint(text):
            return {
                "status": "foreground_chat",
                "assistant_summary": "你说得对，我会沿用前面已经记录的信息，不会要求你重复补充。你可以继续加预算、人数、忌口或直接让我继续推进。",
            }
        return {
            "status": "foreground_chat",
            "assistant_summary": "收到，这句话不会修改当前任务。我会继续保留前面已经记录的上下文。",
        }

    def _handle_completed_plan_recap(self) -> dict[str, str]:
        if self._last_completed_plan_summary:
            return {
                "status": "completed_plan_recap",
                "assistant_summary": (
                    "当前计划已经生成；下面重新展示同一份已完成方案（不会新建任务或改变 plan_version）：\n"
                    + self._last_completed_plan_summary
                ),
            }
        return {
            "status": "no_completed_plan",
            "assistant_summary": "当前没有已完成的规划可以展示。请先提供任务目标、地点和用餐时间，我会在信息足够时自动生成方案。",
        }

    def _handle_no_active_cancel(self) -> dict[str, str]:
        task_state, _, _ = self._projections()
        last_task = self._task_for_context(task_state)
        if last_task is not None and last_task.lifecycle_state == "COMPLETED":
            return {
                "status": "no_active_task_to_cancel",
                "assistant_summary": (
                    "当前没有正在执行的规划可取消：上一份方案已经完成，因此不会再打开取消确认。"
                    "如果你想改时间、地点、人数或预算，请直接说明修改内容，我会创建一份明确标注的修订任务。"
                ),
            }
        if last_task is not None and last_task.lifecycle_state == "CANCELLED":
            return {
                "status": "no_active_task_to_cancel",
                "assistant_summary": "当前没有正在执行的规划可取消：上一份任务已经处于 CANCELLED，不会重复触发 confirmation gate。",
            }
        return {
            "status": "no_active_task_to_cancel",
            "assistant_summary": "当前没有正在执行的规划可取消，因此不会创建取消 confirmation gate。你可以先提出需要规划的事项。",
        }

    async def _call_codex(
        self,
        *,
        intent: str,
        slowtask_event: Mapping[str, Any],
        source_evidence_refs: Sequence[str],
        authoritative_missing_fields: Sequence[str] | None = None,
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
            authoritative_missing_fields=authoritative_missing_fields,
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
            orchestration_role=_optional_str(item.get("orchestration_role")),
            subtask_id=_optional_str(item.get("subtask_id")),
            subtask_goal=_optional_str(item.get("subtask_goal")),
            public_thought=_optional_str(item.get("public_thought")),
            tool_input_summary=_optional_str(item.get("tool_input_summary")),
            tool_output_summary=_optional_str(item.get("tool_output_summary")),
            next_step=_optional_str(item.get("next_step")),
            blocked_on_user=_optional_bool(item.get("blocked_on_user")),
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
        orchestration_role: str | None = None,
        subtask_id: str | None = None,
        subtask_goal: str | None = None,
        public_thought: str | None = None,
        tool_input_summary: str | None = None,
        tool_output_summary: str | None = None,
        next_step: str | None = None,
        blocked_on_user: bool | None = None,
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
            orchestration_role=_safe_progress_token(orchestration_role) if orchestration_role else None,
            subtask_id=_safe_progress_token(subtask_id) if subtask_id else None,
            subtask_goal=_safe_progress_text(subtask_goal) if subtask_goal else None,
            public_thought=_safe_progress_text(public_thought) if public_thought else None,
            tool_input_summary=_safe_progress_text(tool_input_summary) if tool_input_summary else None,
            tool_output_summary=_safe_progress_text(tool_output_summary) if tool_output_summary else None,
            next_step=_safe_progress_text(next_step) if next_step else None,
            blocked_on_user=blocked_on_user,
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
                    orchestration_role=_optional_str(item.get("orchestration_role")),
                    subtask_id=_optional_str(item.get("subtask_id")),
                    subtask_goal=_optional_str(item.get("subtask_goal")),
                    public_thought=_optional_str(item.get("public_thought")),
                    tool_input_summary=_optional_str(item.get("tool_input_summary")),
                    tool_output_summary=_optional_str(item.get("tool_output_summary")),
                    next_step=_optional_str(item.get("next_step")),
                    blocked_on_user=_optional_bool(item.get("blocked_on_user")),
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

    def _merge_slot_values(self, text: str) -> None:
        self._slot_values.update(_extract_workbench_slots(text))

    def _review_and_start_itinerary_tool(
        self,
        *,
        task_id: str,
        caused_by_event_id: str,
        evidence_refs: Sequence[str],
    ) -> dict[str, str]:
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        review = self._slowtask_runtime.review_evidence(
            task_id=task_id,
            plan_version=task.current_plan_version,
            caused_by_event_id=caused_by_event_id,
            event_id_prefix=self._next_id("review"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=task.current_task_event_seq + 1,
            evidence_refs=evidence_refs,
            required_fields=("location_anchor", "time_window", "days"),
            resolved_fields=("location_anchor", "time_window", "days"),
            resolved_arguments_ref=self._next_ref("args", "resolved"),
            provenance_ref=self._next_ref("provenance", "arguments"),
            field_provenance_refs=evidence_refs,
        )
        self._resolved_argument_values = _resolved_tool_arguments(self._slot_values)
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        arguments_event = next(
            event for event in review.produced_events if event["event_name"] == "ARGUMENTS_RESOLVED"
        )
        provenance_event = next(
            event for event in review.produced_events if event["event_name"] == "ARGUMENT_RESOLUTION_PROVENANCE"
        )
        use_external_place_search = self._config.provider_mode == "codex_cli_local"
        tool_name = "webSearch" if use_external_place_search else "demo.itinerary.search"
        arguments = (
            {"query": _place_search_query(self._slot_values)}
            if use_external_place_search
            else dict(self._resolved_argument_values)
        )
        argument_provenance = (
            {"query": str(provenance_event["event_id"])}
            if use_external_place_search
            else {
                "company_location": str(provenance_event["event_id"]),
                "days": str(provenance_event["event_id"]),
                "time_window": str(provenance_event["event_id"]),
            }
        )
        request = ToolExecutionRequest(
            tool_call_id=self._next_id("tool_call"),
            tool_name=tool_name,
            task_id=task_id,
            plan_version=task.current_plan_version,
            current_plan_version=task.current_plan_version,
            start_task_event_seq=task.current_task_event_seq + 1,
            caused_by_event_id=str(review.produced_events[-1]["event_id"]),
            event_id_prefix=self._next_id("itinerary_search"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            idempotency_key=self._next_id("idempotency"),
            arguments=arguments,
            argument_provenance=argument_provenance,
            resolved_arguments_ref=str(arguments_event["event_id"]),
            provenance_ref=str(provenance_event["event_id"]),
            preview_ref=self._next_ref("preview", "itinerary"),
        )
        started = self._tool_executor.begin(request)
        if started.handle is None:
            return {
                "status": "tool_blocked",
                "assistant_summary": f"我还不能开始查询，因为 {tool_name} 缺少：{', '.join(started.blocking_fields)}。",
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
            "assistant_summary": (
                "信息已经足够，我正在调用只读网页/地图地点检索，并会在当前计划的结果返回后自动给出方案。"
                if use_external_place_search
                else "信息够了，我先按你补充的时间和地点范围去查一个沙盒里的行程候选。你也可以继续补充预算、人数或偏好。"
            ),
        }

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

    def _label_user_patch_evidence(
        self,
        evidence_ref: str,
        *,
        interpretation_event: Mapping[str, Any],
    ) -> None:
        """Project SlowTask's final patch interpretation into the evidence label."""

        item = self._evidence_catalog.get(evidence_ref)
        if item is None:
            return
        interpretation_type = str(interpretation_event.get("interpretation_type", "patch"))
        material = bool(interpretation_event.get("materially_changes_task"))
        reason = _safe_summary(str(interpretation_event.get("interpretation_reason", "")))
        if material:
            label = "用户 material patch"
        elif interpretation_type == "slot_update":
            label = "用户补齐信息（不改 plan_version）"
        else:
            label = "用户 non-material patch（不改 plan_version）"
        item["label"] = label
        if reason:
            item["interpretation_reason"] = reason

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
    has_active_task = any(not task.is_terminal for task in task_state.tasks.values())
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
            resolved = aliases[action]
            if resolved == "cancel_candidate" and not has_active_task:
                return "no_active_cancel"
            return resolved
    lowered = text.lower()
    if any(marker in lowered for marker in ("采用旧", "adopt stale", "use old result", "复用旧结果")):
        return "adopt_stale_evidence"
    if any(marker in lowered for marker in ("取消", "不要了", "停止任务", "cancel", "stop this")):
        return "cancel_candidate" if has_active_task else "no_active_cancel"
    # A request to render the already-prepared plan is a control intent, not a
    # new constraint.  It must not fall through to the active-task default
    # below, which classifies generic text as a material UserPatch and would
    # incorrectly advance plan_version.
    if any(
        marker in lowered
        for marker in (
            "完成",
            "提交方案",
            "输出最终规划",
            "输出最终方案",
            "给出最终规划",
            "给出最终方案",
            "最终规划",
            "最终方案",
            "请定稿",
            "定稿",
            "finalize",
            "complete",
        )
    ):
        # Auto-finalization may already have completed the task before the
        # browser sends this redundant control utterance.  In that case an
        # ACTIVE_TASK_PATCH frame is invalid (and used to become HTTP 400).
        # Replay the committed answer instead; do not create a new plan.
        if not has_active_task:
            return "repeat_completed_plan"
        return "complete_current"
    if not has_active_task:
        if task_state.last_task_id is not None and any(
            marker in lowered for marker in ("改", "调整", "修改", "换成", "改到", "晚上", "午餐", "晚餐")
        ):
            # A terminal SlowTask cannot be patched under ADR-016.  Preserve
            # the completed plan and create a clearly-labelled revision task
            # that carries forward the user-owned slot values.
            return "revise_completed_plan"
        return "start"
    if focus_state.active_task_id is not None:
        if _is_context_memory_complaint(text):
            return "foreground"
        if any(marker in lowered for marker in ("你好", "谢谢", "hello", "thanks", "闲聊", "chat")):
            return "foreground"
        return "material_patch"
    return "start"


def _is_context_memory_complaint(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "不是已经",
            "已经给",
            "给过",
            "不是说了",
            "刚才说了",
            "前面说了",
            "我不是",
        )
    )


def _frame_hints(action: str) -> tuple[str, bool, str]:
    if action in {"start", "revise_completed_plan"}:
        return "NEW_TASK_CANDIDATE", True, "complex"
    if action == "cancel_candidate":
        return "CANCEL_OR_PAUSE_CANDIDATE", False, "task"
    if action == "confirmation" or action in {"material_patch", "adopt_stale_evidence", "complete_current"}:
        return "ACTIVE_TASK_PATCH", False, "task"
    return "FOREGROUND_CHAT", False, "simple"


def _extract_workbench_slots(text: str) -> dict[str, str]:
    lowered = text.lower()
    slots: dict[str, str] = {}
    compact = re.sub(r"\s+", "", text)
    location_match = re.search(
        r"((?:北京市)?(?:海淀区)?[^，。；;]{0,32}(?:中关村|领展|欧美汇|丹棱街)[^，。；;]{0,32}(?:附近|广场|购物中心|购物广场)?)",
        text,
    )
    if location_match:
        slots["location_anchor"] = _safe_summary(location_match.group(1))
    elif any(marker in text for marker in ("公司", "办公室", "园区", "酒店", "机场", "车站", "餐厅", "会议室", "地点", "附近", "位置", "中关村", "领展", "北京")) or any(
        marker in lowered for marker in ("office", "hotel", "airport", "station", "near")
    ):
        slots["location_anchor"] = _safe_summary(text)
    explicit_time = re.search(r"(\d{1,2})\s*[点:：]\s*(半|\d{1,2})?", compact)
    if explicit_time:
        hour = int(explicit_time.group(1))
        minute = "30" if explicit_time.group(2) == "半" else (explicit_time.group(2) or "00")
        slots["time_window"] = f"{hour:02d}:{minute}"
    elif any(marker in text for marker in ("今天", "明天", "后天", "上午", "下午", "晚上", "中午", "午饭", "午餐", "晚饭", "晚餐", "周一", "周二", "周三", "周四", "周五", "周六", "周日")) or any(
        marker in lowered for marker in ("today", "tomorrow", "morning", "afternoon", "evening", "lunch", "dinner")
    ) or re.search(r"\d{1,2}月\d{1,2}[号日]?", compact):
        slots["time_window"] = "晚餐时段（建议 18:30）" if any(marker in text for marker in ("晚上", "晚饭", "晚餐")) else _safe_summary(text)
    if any(marker in text for marker in ("两天", "2天", "二天", "两日", "2日")) or any(marker in lowered for marker in ("two days", "2 days")):
        slots["days"] = "2"
    party_match = re.search(r"(\d+|[一二三四五六七八九十两]+)\s*个?人", text)
    if party_match:
        slots["party_size"] = f"{party_match.group(1)} 人"
    budget_match = re.search(r"(人均\s*)?\d+\s*(元|块|以内|以下)", text)
    if budget_match:
        budget_digits = re.search(r"\d+", budget_match.group(0))
        slots["budget"] = f"人均 {budget_digits.group(0)} 元以内" if budget_digits else budget_match.group(0)
    if any(marker in text for marker in ("不吃辣", "忌口", "过敏", "清淡", "素食", "没有其他忌口")):
        non_spicy_count = re.search(r"(\d+|[一二三四五六七八九十两]+)\s*(?:位|个)?[^，。；;]{0,8}不吃辣", text)
        slots["dietary_constraints"] = (
            f"{non_spicy_count.group(1)}位客人不吃辣"
            if non_spicy_count
            else "有客人不吃辣，菜品需可分开调味"
        )
    if "云南菜" in text or "滇菜" in text:
        slots["cuisine_preference"] = "云南菜"
    if any(marker in text for marker in ("晚上", "晚饭", "晚餐")):
        slots["meal_type"] = "晚餐"
    elif any(marker in text for marker in ("中午", "午饭", "午餐")):
        slots["meal_type"] = "午餐"
    if any(marker in text for marker in ("包间", "安静", "商务环境")):
        slots["private_room_or_quiet_space"] = _safe_summary(text)
    if any(marker in text for marker in ("发票", "停车", "报销")):
        slots["admin_needs"] = _safe_summary(text)
    return slots


def _missing_critical_slots(slots: Mapping[str, str]) -> tuple[str, ...]:
    missing: list[str] = []
    if not slots.get("time_window"):
        missing.append("time_window")
    if not slots.get("location_anchor"):
        missing.append("location_anchor")
    return tuple(missing)


def _resolved_critical_slots(slots: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(field for field in ("location_anchor", "time_window") if slots.get(field))


def _resolved_tool_arguments(slots: Mapping[str, str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "company_location": slots.get("location_anchor", "Synthetic Central Office"),
        "days": int(slots.get("days", "2")) if str(slots.get("days", "2")).isdigit() else 2,
        "time_window": slots.get("time_window", "flexible"),
    }
    if slots.get("budget"):
        budget_digits = re.search(r"\d+", slots["budget"])
        if budget_digits:
            arguments["budget_max"] = int(budget_digits.group(0))
    return arguments


def _place_search_query(slots: Mapping[str, str]) -> str:
    location = slots.get("location_anchor", "")
    cuisine = slots.get("cuisine_preference", "餐厅")
    # Search engines and public map geocoders treat party size / budget as
    # noise.  Keep those as planning constraints and reduce a verbose anchor
    # (e.g. “海淀区中关村领展购物广场附近”) to its searchable locality.
    locality = next(
        (candidate for candidate in ("中关村", "五道口", "望京", "国贸", "三里屯", "上地", "海淀") if candidate in location),
        location,
    )
    return _safe_summary(f"{locality} {cuisine}")


def _codex_user_visible_reply(provider_result: Any) -> str:
    """Select a bounded Codex realization without giving it state ownership.

    SlowTask has already emitted the state/evidence events before this function
    runs.  The adapter summary can therefore control natural-language wording
    and the order of questions, but cannot add facts, change missing fields,
    advance a plan, or start a tool.
    """

    proposal = getattr(provider_result, "proposal", None)
    candidate = proposal.get("summary") if isinstance(proposal, Mapping) else None
    if isinstance(candidate, str):
        normalized = _safe_summary(candidate)
        internal_markers = (
            "time_window",
            "location_anchor",
            "party_size",
            "dietary_constraints",
            "plan_version",
            "task_event_seq",
            "explicit_user_confirmation",
        )
        if normalized and not any(marker in normalized.lower() for marker in internal_markers):
            return normalized
    # This is a degraded adapter-safety message, not a domain-specific prompt.
    # It is reachable only when the provider did not produce a valid safe
    # realization candidate; SlowTask's journalled WAITING state remains intact.
    return "当前模型没有生成可安全展示的说明；任务状态已保留，等待你补充相关信息后继续。"


def _with_codex_user_visible_reply(
    started: Mapping[str, str],
    provider_result: Any,
) -> dict[str, str]:
    result = dict(started)
    if result.get("status") == "tool_running":
        result["assistant_summary"] = _codex_user_visible_reply(provider_result)
    return result


def _user_facing_final_plan(
    *,
    slots: Mapping[str, str],
    tool_payload: Mapping[str, Any] | None,
) -> str:
    """Render a grounded, customer-facing plan from current-plan evidence only."""

    party_size = slots.get("party_size", "人数待最终确认")
    time_window = slots.get("time_window", "具体到店时间待确认")
    budget = slots.get("budget", "预算待确认")
    dietary = slots.get("dietary_constraints", "忌口待确认")
    meal_type = slots.get("meal_type", "用餐")
    cuisine = slots.get("cuisine_preference", "餐饮")
    external_results = tool_payload.get("results", []) if isinstance(tool_payload, Mapping) else []
    if isinstance(external_results, Sequence) and external_results:
        candidates: list[str] = []
        for item in external_results[:3]:
            if not isinstance(item, Mapping):
                continue
            title = _safe_summary(str(item.get("source_title", "网页地点结果")))
            url = _safe_summary(str(item.get("source_url", "")))
            snippet = _safe_summary(str(item.get("snippet_or_summary", "")))
            provider = _safe_summary(str(item.get("source_provider", "公开网页/地图")))
            if title and url:
                candidates.append(f"- {title}：{snippet}（{provider}；来源：{url}）")
        if candidates:
            return (
                f"当前方案（{meal_type}接待）：{party_size}，建议时段为 {time_window}；"
                f"菜系偏好为{cuisine}，预算按{budget}控制，并满足{dietary}。\n"
                "本轮只读网页/地图检索返回了以下可核验候选（按“中关村 + 云南菜”检索，不能据此替代门店实时确认）：\n"
                + "\n".join(candidates)
                + "\n点菜时优先选择可做不辣或分开调味的菜品，并在联系门店前再次确认营业、余位、包间、菜单与价格。"
                "这些网页摘要属于不可信外部证据；系统没有执行订位、支付或任何外部写操作。"
            )
    if isinstance(tool_payload, Mapping) and tool_payload.get("trust_level") == "UNTRUSTED_WEB_EVIDENCE":
        degraded_reason = _safe_summary(str(tool_payload.get("degraded_reason", "unknown")))
        return (
            f"我已按 {party_size}、{meal_type}、{budget} 和{dietary}发起只读网页/地图地点检索，"
            "但当前外部来源没有返回可列名的候选，因此不会编造餐厅名称。"
            f"本次检索状态：{degraded_reason or 'unknown'}。"
            "系统已保留地点、人数、预算与忌口；你可以修改地点锚点或再次检索。系统不会执行真实订位。"
        )
    return (
        f"最终规划草案：按 {party_size} 的{meal_type}接待处理，时间为 {time_window}，"
        f"预算控制在{budget}，优先满足{dietary}，菜系偏好为{cuisine}。\n"
        "当前尚未取得可验证的地点检索结果；请补充像“北京市海淀区中关村领展购物广场附近”这样的明确位置，或在本地 Codex 模式下重试只读地点检索。"
    )


def _user_facing_current_plan(
    *,
    slots: Mapping[str, str],
    tool_payload: Mapping[str, Any] | None,
) -> str:
    """Make the non-terminal status explicit without changing grounded facts."""

    return (
        "当前可修改方案（尚未定稿）：\n"
        + _user_facing_final_plan(slots=slots, tool_payload=tool_payload)
        + "\n如需改时间、地点、人数、预算或忌口，直接告诉我；系统会在同一任务中生成新的 plan_version。"
    )


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
    if kind == "main_thread_context_loaded":
        return "Codex 主控已读取受控上下文"
    if kind == "subtask_completed" and item.get("subtask_id") == "slot_gap_analysis":
        return "关键槽位检查完成"
    if kind == "subtask_started" and item.get("subtask_id") == "tool_candidate_review":
        return "开始审查 demo 工具候选"
    if kind == "structured_candidate_ready":
        return "结构化候选已生成"
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
    if kind == "main_thread_context_loaded":
        return "这是 adapter 的公开主控阶段，不是隐藏思维链。"
    if kind == "subtask_completed" and item.get("subtask_id") == "slot_gap_analysis":
        return "如果缺少关键槽位，SlowTask 会等待用户补充，不启动工具。"
    if kind == "subtask_started" and item.get("subtask_id") == "tool_candidate_review":
        return "工具候选仍需 Tool Executor 做参数、plan_version 和 sandbox policy 校验。"
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
