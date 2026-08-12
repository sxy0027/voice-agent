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
from dataclasses import dataclass, replace
import os
import re
import time
from typing import Any

from voice_agent.access.text_ingress import receive_text_input
from voice_agent.adapters.capabilities import AdapterCapability
from voice_agent.adapters.codex_slow_llm import (
    CODEX_PROVIDER_MODES,
    CodexSlowLLMAdapterConfig,
    build_codex_slow_llm_capability,
)
from voice_agent.adapters.codex_roles import (
    CodexRoleAdapter,
    RoleInvocationResult,
    make_codex_cli_role_invoker,
)
from voice_agent.adapters.mock_adapters import mvp0_mock_adapter_capabilities
from voice_agent.adapters.workbench_place_search import PlaceSearchEvidence, WorkbenchPlaceSearchAdapter
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
from voice_agent.slowtask.requirement_model import (
    RequirementAssessment,
    RequirementSourceRoute,
    RequirementStage,
    TaskRequirementModel,
    assess_requirements,
    task_requirement_model_from_dict,
)
from voice_agent.slowtask.role_orchestrator import (
    PlanReviewResult,
    RoleRun,
    SlowLLMRoleOrchestrator,
    select_user_blockers,
)
from voice_agent.slowtask.slot_ledger import (
    SlotLedger,
    SlotState,
    SlotUpdate,
    build_slot_ledger,
    updates_from_evidence_catalog,
)
from voice_agent.slowtask.task_profiles import (
    builtin_task_profile_registry,
    extract_profile_updates,
)
from voice_agent.slowtask.tool_validation import (
    CompiledToolCall,
    ToolArgumentCompiler,
    ToolGapResolver,
    ToolValidationReport,
    ToolValidationStatus,
    accepted_fact_inputs_from_ledger,
)
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
WORKBENCH_DEFAULT_CODEX_MODEL: str | None = None
WORKBENCH_DEFAULT_CODEX_REASONING_EFFORT = "medium"
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
    timeout_seconds: int = 60
    model_name: str | None = WORKBENCH_DEFAULT_CODEX_MODEL
    reasoning_effort: str | None = WORKBENCH_DEFAULT_CODEX_REASONING_EFFORT
    max_repair_attempts: int = 2

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "WorkbenchRuntimeConfig":
        value = {} if value is None else value
        provider_mode = str(value.get("provider_mode", WORKBENCH_DEFAULT_PROVIDER_MODE))
        if provider_mode not in CODEX_PROVIDER_MODES:
            raise ValueError(f"provider_mode must be one of {sorted(CODEX_PROVIDER_MODES)}")
        model_name = value.get("model_name", WORKBENCH_DEFAULT_CODEX_MODEL)
        reasoning_effort = value.get(
            "reasoning_effort",
            WORKBENCH_DEFAULT_CODEX_REASONING_EFFORT,
        )
        return cls(
            provider_mode=provider_mode,
            allow_local_codex_cli=bool(
                value.get("allow_local_codex_cli", WORKBENCH_DEFAULT_ALLOW_LOCAL_CODEX_CLI)
            ),
            codex_bin=str(value.get("codex_bin", "codex")),
            timeout_seconds=int(value.get("timeout_seconds", 60)),
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
                "timeout_seconds": os.environ.get("VOICE_AGENT_CODEX_TIMEOUT_SECONDS", "60"),
                "model_name": os.environ.get("VOICE_AGENT_CODEX_MODEL", WORKBENCH_DEFAULT_CODEX_MODEL),
                "reasoning_effort": os.environ.get(
                    "VOICE_AGENT_CODEX_REASONING_EFFORT",
                    WORKBENCH_DEFAULT_CODEX_REASONING_EFFORT,
                ),
            }
        )


@dataclass(frozen=True)
class _DynamicClarificationPlan:
    clarification_id: str
    blocked_stage: str
    ask_fields: tuple[str, ...]
    known_fields: tuple[str, ...]
    reason: str
    attempt: int


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
        return build_codex_slow_llm_capability(
            provider_mode=self._config.provider_mode,
            allow_local_codex_cli=self._config.allow_local_codex_cli,
            model_name=self._config.model_name,
        )

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
        turn_started = time.perf_counter()
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
                        "上一份方案已经完成；我已将这条修改作为新的修订任务处理，并只沿用仍与当前目标相关的已接受条件。"
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
                "turn_id": turn.get("turn_id"),
                "source_role": result.get("user_visible_source_role", "PYTHON_DETERMINISTIC"),
                "proposal_id": result.get("user_visible_proposal_id"),
                "context_hash": result.get("user_visible_context_hash"),
                "caused_by_event_id": result.get("user_visible_caused_by_event_id"),
                "note": "该文本是状态摘要；任务事实仍由 event journal / reducer 投影拥有。",
            }
        )
        self._record_live_progress(
            kind="turn_completed",
            status="completed",
            phase="finalizing",
            label="本轮状态已汇总",
            detail="最终回复只引用已校验的状态摘要和 proposal 边界。",
            latency_ms=max(0, int((time.perf_counter() - turn_started) * 1000)),
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
        for background_task in getattr(self, "_background_tasks", ()):
            background_task.cancel()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._background_failures: list[str] = []
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
        self._current_planner_results: dict[tuple[str, int], RoleInvocationResult] = {}
        self._last_user_input: str | None = None
        self._resolved_argument_values: dict[str, Any] = {}
        self._slot_values: dict[str, Any] = {}
        self._role_state: dict[str, Any] = {
            "current_role": None,
            "prior_role_proposal_refs": [],
            "planner_mode": None,
            "current_plan_proposal_ref": None,
            "reviewer_status": None,
        }
        self._remodel_context_hashes: set[str] = set()
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
        self._codex_config = CodexSlowLLMAdapterConfig(
            provider_mode=self._config.provider_mode,
            allow_local_codex_cli=self._config.allow_local_codex_cli,
            codex_bin=self._config.codex_bin,
            timeout_seconds=self._config.timeout_seconds,
            model_name=self._config.model_name,
            reasoning_effort=self._config.reasoning_effort,
            max_repair_attempts=self._config.max_repair_attempts,
        )
        self._tool_registry = ToolRegistry(mvp2_demo_tool_manifests())
        self._tool_executor = DemoToolExecutor(
            journal=self._journal,
            registry=self._tool_registry,
            backend=InMemoryDemoBackend(),
        )
        self._role_adapter = CodexRoleAdapter(
            boundary=self._boundary,
            tool_registry=self._tool_registry,
            provider_mode="fake" if self._config.provider_mode == "fake" else self._config.provider_mode,
            provider_invoker=(
                make_codex_cli_role_invoker(self._codex_config)
                if self._config.provider_mode == "codex_cli_local"
                and self._config.allow_local_codex_cli
                else None
            ),
        )
        self._role_orchestrator = SlowLLMRoleOrchestrator(
            adapter=self._role_adapter,
            tool_registry=self._tool_registry,
        )
        self._task_profiles = builtin_task_profile_registry()
        self._tool_gap_resolver = ToolGapResolver()
        self._tool_argument_compiler = ToolArgumentCompiler()
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
        run = self._role_orchestrator.new_task_run()
        model, modeler = await self._run_task_modeler(
            run=run,
            task_id=task_id,
            intent=text,
            caused_by_event_id=str(planning.produced_events[-1]["event_id"]),
            source_evidence_refs=(evidence_ref,),
        )
        self._track_role_result(modeler, next_role="REQUIREMENT_ANALYST")
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        accepted_event = self._slowtask_runtime.accept_task_requirement_model(
            task_id=task_id,
            plan_version=task.current_plan_version,
            task_event_seq=task.current_task_event_seq + 1,
            caused_by_event_id=str(modeler.structured_output_event["event_id"]),
            event_id=self._next_id("task_requirement_model_accepted"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            model_payload=model.to_dict(),
            source_proposal_ref=modeler.proposal_ref,
            accepted_context_hash=model.accepted_context_hash,
        )
        updates, analyst = await self._analyze_registered_or_role(
            run=run,
            task_id=task_id,
            intent=text,
            evidence_ref=evidence_ref,
            caused_by_event_id=str(accepted_event["event_id"]),
        )
        self._accept_requirement_updates(evidence_ref=evidence_ref, updates=updates)
        self._record_requirement_state_acceptance(
            task_id=task_id,
            updates=updates,
            caused_by_event_id=(
                str(analyst.structured_output_event["event_id"])
                if analyst is not None
                else str(accepted_event["event_id"])
            ),
        )
        if analyst is not None:
            self._track_role_result(analyst, next_role="CLARIFIER_OR_PLANNER")
        return await self._advance_after_requirement_analysis(
            run=run,
            task_id=task_id,
            intent=text,
            caused_by_event_id=(
                str(analyst.structured_output_event["event_id"])
                if analyst is not None
                else str(accepted_event["event_id"])
            ),
            evidence_refs=(evidence_ref,),
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
        patch_id = self._next_id("patch")
        evidence_ref = self._next_ref("evidence", "patch")
        pre_patch_slots = self._slot_ledger_for_task(task.task_id).value_map(resolved_only=False)
        suggested_kind = self._role_adapter.fake_task_kind_for_intent(text)
        goal_rewrite = bool(
            patch_kind == "material"
            and suggested_kind is not None
            and task.task_kind is not None
            and suggested_kind != task.task_kind
            and not task.task_model_needs_remodeling
        )
        if goal_rewrite:
            candidate_patch_types = ("goal_rewrite_candidate",)
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
        current_context_hash = self._context_pack().context_hash
        current_requirement_model = self._current_requirement_model(task.task_id)
        remodel_model = bool(
            patch_kind == "material"
            and not goal_rewrite
            and task.task_model_needs_remodeling
            and (
                current_requirement_model is None
                or not self._task_profiles.has_deterministic_extractor(
                    current_requirement_model.task_kind
                )
            )
            and current_context_hash not in self._remodel_context_hashes
        )
        if remodel_model:
            self._remodel_context_hashes.add(current_context_hash)
        run = (
            self._role_orchestrator.new_task_run()
            if goal_rewrite or remodel_model
            else self._role_orchestrator.patch_run()
        )
        incoming_slot_updates: tuple[SlotUpdate, ...] = ()
        if remodel_model:
            task_state, _, _ = self._projections()
            current_task = task_state.tasks[task.task_id]
            if current_task.task_requirement_model_ref is None:
                raise ValueError("provisional model remodeling requires an accepted model")
            previous_model_version = current_task.task_requirement_model_version or 1
            invalidated = self._slowtask_runtime.invalidate_task_requirement_model(
                task_id=current_task.task_id,
                plan_version=current_task.current_plan_version,
                task_event_seq=current_task.current_task_event_seq + 1,
                caused_by_event_id=str(patch_result.user_patch_event["event_id"]),
                event_id=self._next_id("task_requirement_model_invalidated"),
                created_monotonic_ms=self._clock.monotonic_ms,
                created_wall_clock_ms=self._clock.wall_clock_ms,
                model_ref=current_task.task_requirement_model_ref,
                invalidation_reason="bounded_context_remodeling",
            )
            model, modeler = await self._run_task_modeler(
                run=run,
                task_id=current_task.task_id,
                intent=text,
                caused_by_event_id=str(invalidated["event_id"]),
                source_evidence_refs=tuple(self._context_pack().accepted_evidence_refs),
            )
            model = replace(model, model_version=previous_model_version + 1)
            self._track_role_result(modeler, next_role="REQUIREMENT_ANALYST")
            task_state, _, _ = self._projections()
            current_task = task_state.tasks[task.task_id]
            accepted = self._slowtask_runtime.accept_task_requirement_model(
                task_id=current_task.task_id,
                plan_version=current_task.current_plan_version,
                task_event_seq=current_task.current_task_event_seq + 1,
                caused_by_event_id=str(modeler.structured_output_event["event_id"]),
                event_id=self._next_id("task_requirement_model_accepted"),
                created_monotonic_ms=self._clock.monotonic_ms,
                created_wall_clock_ms=self._clock.wall_clock_ms,
                model_payload=model.to_dict(),
                source_proposal_ref=modeler.proposal_ref,
                accepted_context_hash=model.accepted_context_hash,
            )
            incoming_slot_updates, analyst = await self._analyze_registered_or_role(
                run=run,
                task_id=current_task.task_id,
                intent=text,
                evidence_ref=evidence_ref,
                caused_by_event_id=str(accepted["event_id"]),
            )
            self._accept_requirement_updates(evidence_ref=evidence_ref, updates=incoming_slot_updates)
            if analyst is not None:
                self._track_role_result(analyst, next_role="CLARIFIER_OR_PLANNER")
        elif patch_kind == "material" and not goal_rewrite:
            incoming_slot_updates, analyst = await self._analyze_registered_or_role(
                run=run,
                task_id=task.task_id,
                intent=text,
                evidence_ref=evidence_ref,
                caused_by_event_id=str(patch_result.user_patch_event["event_id"]),
            )
            self._accept_requirement_updates(evidence_ref=evidence_ref, updates=incoming_slot_updates)
            if analyst is not None:
                self._track_role_result(analyst, next_role="CLARIFIER_OR_PLANNER")
        incoming_slot_values = _slot_value_map(incoming_slot_updates)
        if patch_kind == "material":
            self._slot_values.update(incoming_slot_values)
        handle = self._handle_for_task(task.task_id)
        if handle is not None:
            handle.record_task_event(patch_result.user_patch_event)
        task_state, _, _ = self._projections()
        interpretation_task_event_seq = task_state.tasks[task.task_id].current_task_event_seq + 1
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
            next_task_event_seq=interpretation_task_event_seq,
        )
        self._label_user_patch_evidence(
            evidence_ref,
            interpretation_event=interpretation.produced_events[0],
        )
        if handle is not None:
            for event in interpretation.produced_events:
                handle.record_task_event(event)

        accepted_state_event = self._record_requirement_state_acceptance(
            task_id=task.task_id,
            updates=incoming_slot_updates,
            caused_by_event_id=str(interpretation.produced_events[-1]["event_id"]),
        )
        if handle is not None and accepted_state_event is not None:
            handle.record_task_event(accepted_state_event)

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
        replaced_requirement_ids = tuple(
            update.name
            for update in incoming_slot_updates
            if pre_patch_slots.get(update.name) is not None
            and pre_patch_slots.get(update.name) != ""
            and pre_patch_slots.get(update.name) != "UNKNOWN"
            and pre_patch_slots.get(update.name) != update.normalized_value
        )
        if replaced_requirement_ids == ("time_window",):
            patch_acknowledgement = (
                f"已把时间调整到{_safe_summary(str(incoming_slot_values['time_window']))}，"
                "其他条件保持不变；"
            )
        else:
            patch_acknowledgement = "收到，我已经把你的新要求合并进当前任务；"
        analysis_cause = str(interpretation.produced_events[-1]["event_id"])
        if goal_rewrite:
            task_state, _, _ = self._projections()
            current_task = task_state.tasks[task.task_id]
            if current_task.task_requirement_model_ref is None:
                raise ValueError("goal rewrite requires an accepted model to invalidate")
            invalidated = self._slowtask_runtime.invalidate_task_requirement_model(
                task_id=current_task.task_id,
                plan_version=current_task.current_plan_version,
                task_event_seq=current_task.current_task_event_seq + 1,
                caused_by_event_id=analysis_cause,
                event_id=self._next_id("task_requirement_model_invalidated"),
                created_monotonic_ms=self._clock.monotonic_ms,
                created_wall_clock_ms=self._clock.wall_clock_ms,
                model_ref=current_task.task_requirement_model_ref,
                invalidation_reason="material_goal_rewrite",
            )
            self._invalidate_requirement_evidence(current_task.task_id)
            model, modeler = await self._run_task_modeler(
                run=run,
                task_id=current_task.task_id,
                intent=text,
                caused_by_event_id=str(invalidated["event_id"]),
                source_evidence_refs=(evidence_ref,),
                goal_override=text,
            )
            self._track_role_result(modeler, next_role="REQUIREMENT_ANALYST")
            task_state, _, _ = self._projections()
            current_task = task_state.tasks[task.task_id]
            accepted = self._slowtask_runtime.accept_task_requirement_model(
                task_id=current_task.task_id,
                plan_version=current_task.current_plan_version,
                task_event_seq=current_task.current_task_event_seq + 1,
                caused_by_event_id=str(modeler.structured_output_event["event_id"]),
                event_id=self._next_id("task_requirement_model_accepted"),
                created_monotonic_ms=self._clock.monotonic_ms,
                created_wall_clock_ms=self._clock.wall_clock_ms,
                model_payload=model.to_dict(),
                source_proposal_ref=modeler.proposal_ref,
                accepted_context_hash=model.accepted_context_hash,
            )
            incoming_slot_updates, analyst = await self._analyze_registered_or_role(
                run=run,
                task_id=current_task.task_id,
                intent=text,
                evidence_ref=evidence_ref,
                caused_by_event_id=str(accepted["event_id"]),
            )
            self._accept_requirement_updates(evidence_ref=evidence_ref, updates=incoming_slot_updates)
            self._record_requirement_state_acceptance(
                task_id=current_task.task_id,
                updates=incoming_slot_updates,
                caused_by_event_id=(
                    str(analyst.structured_output_event["event_id"])
                    if analyst is not None
                    else str(accepted["event_id"])
                ),
            )
            if analyst is not None:
                self._track_role_result(analyst, next_role="CLARIFIER_OR_PLANNER")
                analysis_cause = str(analyst.structured_output_event["event_id"])
            else:
                analysis_cause = str(accepted["event_id"])

        if restarted is None or handle is None:
            advanced = await self._advance_after_requirement_analysis(
                run=run,
                task_id=task.task_id,
                intent=text,
                caused_by_event_id=analysis_cause,
                evidence_refs=(evidence_ref,),
                router_event=router_event,
            )
            if restarted is not None:
                return {
                    **advanced,
                    "assistant_summary": patch_acknowledgement + advanced["assistant_summary"],
                }
            return advanced

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
            summary=f"{handle.request.tool_name} 的旧计划结果已返回；SlowTask 已把它放入 stale_evidence。",
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
        automatic = await self._advance_after_requirement_analysis(
            run=run,
            task_id=current_task.task_id,
            intent=text,
            # The stale-result events are recorded, but the new current-plan
            # compilation remains caused by the accepted UserPatch evidence.
            # Otherwise stale evidence would become a causal input to vN+1.
            caused_by_event_id=analysis_cause,
            evidence_refs=(evidence_ref,),
            router_event=router_event,
        )
        return {
            "status": (
                "plan_advanced_stale_result"
                if self._config.provider_mode == "fake"
                else automatic["status"]
            ),
            "assistant_summary": (
                patch_acknowledgement
                + "上一轮旧查询结果不会继续影响新方案；"
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
            adopted_scope=("tool_requirement_evidence", "tool_result_summary"),
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
        clarification = self._clarification_plan(task_id=task.task_id)
        if clarification is not None:
            return await self._request_schema_clarification(
                task=task,
                caused_by_event_id=str(task.last_slowtask_event_id),
                evidence_refs=tuple(task.source_evidence_refs[-4:]),
                clarification=clarification,
                intent=text,
            )
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
        model = self._current_requirement_model(task.task_id)
        if model is None:
            return {"status": "planning_blocked", "assistant_summary": "没有 accepted TaskRequirementModel，不能提交最终结果。"}
        assessment = assess_requirements(model=model, ledger=self._slot_ledger_for_task(task.task_id))
        if not assessment.ready_for[RequirementStage.COMMITMENT.value]:
            return {"status": "planning_blocked", "assistant_summary": "当前 requirement 仍未满足 commitment gate，不能提前提交。"}
        if (
            self._config.provider_mode != "fake"
            and (task.task_id, task.current_plan_version) not in self._current_planner_results
        ):
            return {
                "status": "planner_running",
                "assistant_summary": "只读结果已经可用，Planner 正在生成当前版本的语义草案；Reviewer 会在草案就绪后的 commitment gate 执行。",
            }
        # Reviewer runs only at the final commitment gate.  Read-only search
        # has already started (and, above, completed) independently of it.
        reviewed = await self._advance_after_requirement_analysis(
            run=self._role_orchestrator.patch_run(),
            task_id=task.task_id,
            intent=text,
            caused_by_event_id=str(task.last_slowtask_event_id),
            evidence_refs=tuple(self._context_pack().accepted_evidence_refs),
            router_event=router_event,
            review_existing_plan=True,
        )
        if reviewed.get("status") == "planning_blocked":
            return reviewed
        if self._role_state.get("reviewer_status") != "PASS":
            return {"status": "planning_blocked", "assistant_summary": "Reviewer 尚未 PASS，当前按 fail-closed 阻止 SemanticCommitment。"}
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
            model=model,
            plan_payload=self._role_state.get("current_plan_payload"),
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
        """Schedule mutable plan publication without blocking the HTTP turn.

        The tracked task is bound to the exact task, plan version and reset
        generation. It journals the ToolResult before publishing a mutable
        draft, while the foreground turn returns ``tool_running`` immediately.
        Fake-provider sessions deliberately retain the in-flight handle so
        replay tests can exercise late-result and stale-evidence handling.
        """

        if started.get("status") != "tool_running" or self._config.provider_mode == "fake":
            return dict(started)
        task_state, focus_state, _ = self._projections()
        task = self._task_for_focus(task_state, focus_state)
        if task is None:
            return dict(started)
        generation = self._reset_generation
        background = asyncio.create_task(
            self._background_auto_publish_current_plan(
                task_id=task.task_id,
                plan_version=task.current_plan_version,
                router_event=dict(router_event),
                reset_generation=generation,
            ),
            name=f"workbench-tool-{task.task_id}-v{task.current_plan_version}",
        )
        self._background_tasks.add(background)
        background.add_done_callback(self._background_task_finished)
        self._record_live_progress(
            kind="auto_publish_current_plan",
            status="started",
            phase="planning",
            label="只读查询已转入受追踪后台任务",
            detail="当前 HTTP turn 立即返回 tool_running；后台结果仍绑定 task_id、plan_version，并通过 journal 更新。",
        )
        self._publish_stable_snapshot_locked()
        return dict(started)

    async def _background_auto_publish_current_plan(
        self,
        *,
        task_id: str,
        plan_version: int,
        router_event: Mapping[str, Any],
        reset_generation: int,
    ) -> None:
        await asyncio.sleep(0)
        async with self._lock:
            if reset_generation != self._reset_generation:
                return
            task_state, focus_state, _ = self._projections()
            task = self._task_for_focus(task_state, focus_state)
            if (
                task is None
                or task.task_id != task_id
                or task.current_plan_version != plan_version
            ):
                return
            handle = self._handle_for_task(task_id)
            if handle is None:
                return
            query = str(handle.request.arguments.get("query", ""))
            self._record_live_progress(
                kind="external_place_search_started",
                status="started",
                phase="tool",
                label="正在调用只读网页检索工具",
                detail="只读取公开地图 POI 与搜索摘要；网页内容会作为不可信证据隔离，不会执行其中的指令。",
                tool_name="webSearch",
                plan_version=handle.plan_version,
                tool_input_summary="只读 webSearch query（参数值已从 shareable trace 省略）。",
            )

        # External I/O must never hold the session lock. A new UserPatch or
        # reset can therefore advance/cancel the current plan immediately.
        search_started = time.perf_counter()
        try:
            evidence = await asyncio.to_thread(self._place_search_adapter.search, query=query)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._background_failures.append(type(exc).__name__)
            self._background_failures[:] = self._background_failures[-8:]
            evidence = PlaceSearchEvidence(
                query=query,
                provider="unavailable",
                results=(),
                degraded_reason="provider_unavailable",
            )
        search_latency_ms = max(0, int((time.perf_counter() - search_started) * 1000))

        async with self._lock:
            if reset_generation != self._reset_generation:
                return
            task_state, focus_state, _ = self._projections()
            task = self._task_for_focus(task_state, focus_state)
            if (
                task is None
                or task.task_id != task_id
                or task.current_plan_version != plan_version
                or self._in_flight_handles.get(handle.request.tool_call_id) is not handle
            ):
                return
            result = await self._handle_auto_publish_current_plan(
                router_event=router_event,
                prefetched_evidence=evidence,
                external_search_latency_ms=search_latency_ms,
            )
            if result.get("status") == "current_plan_ready":
                self._conversation.append(
                    {
                        "id": self._next_id("conversation_background_plan"),
                        "speaker": "system",
                        "text": result["assistant_summary"],
                        "summary": result["assistant_summary"],
                        "owner": "slowtask",
                        "source": "tracked_background_tool_result",
                        "source_role": "PYTHON_DETERMINISTIC",
                        "note": "后台结果严格绑定发起时的 task_id / plan_version。",
                    }
                )
                task_state, focus_state, _ = self._projections()
                current_task = self._task_for_focus(task_state, focus_state)
                if current_task is None:
                    return
                planner_invocation = self._build_planner_invocation(
                    task_id=current_task.task_id,
                    caused_by_event_id=str(current_task.last_slowtask_event_id),
                    fallback_evidence_refs=tuple(self._context_pack().accepted_evidence_refs),
                )
                planner_orchestrator = self._role_orchestrator
            else:
                planner_invocation = None
                planner_orchestrator = None
            self._stable_snapshot = self._snapshot().to_dict()

        if planner_invocation is None or planner_orchestrator is None:
            return
        planner = await planner_orchestrator.plan_only(
            run=planner_orchestrator.patch_run(),
            invocation=planner_invocation,
        )
        async with self._lock:
            if reset_generation != self._reset_generation:
                return
            task_state, focus_state, _ = self._projections()
            task = self._task_for_focus(task_state, focus_state)
            if (
                task is None
                or task.task_id != task_id
                or task.current_plan_version != plan_version
            ):
                return
            self._track_role_result(planner, next_role="REVIEWER_AT_COMMITMENT_GATE")
            self._current_planner_results[(task_id, plan_version)] = planner
            self._role_state["planner_mode"] = planner.proposal["payload"].get("planning_mode")
            self._role_state["current_plan_proposal_ref"] = planner.proposal_ref
            self._role_state["current_plan_payload"] = dict(planner.proposal["payload"])
            self._codex_proposals.append(
                _legacy_role_proposal(planner, model=self._current_requirement_model(task_id))
            )
            payload = self._current_plan_tool_payloads.get((task_id, plan_version))
            refined_summary = _user_facing_current_plan(
                slots=self._slot_values,
                tool_payload=payload,
                model=self._current_requirement_model(task_id),
                plan_payload=self._role_state.get("current_plan_payload"),
            )
            self._conversation.append(
                {
                    "id": self._next_id("conversation_background_planner"),
                    "speaker": "system",
                    "text": refined_summary,
                    "summary": refined_summary,
                    "owner": "slowtask",
                    "source": "tracked_background_planner",
                    "source_role": "PLANNER",
                    "proposal_id": planner.proposal["proposal_id"],
                    "context_hash": planner.proposal["input_context_hash"],
                    "note": "Planner 只生成当前 plan 的语义草案；Reviewer 留待 commitment gate。",
                }
            )
            self._stable_snapshot = self._snapshot().to_dict()

    def _background_task_finished(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:  # safe bounded diagnostic; never provider body
            self._background_failures.append(type(exc).__name__)
            self._background_failures[:] = self._background_failures[-8:]

    async def _handle_auto_publish_current_plan(
        self,
        *,
        router_event: Mapping[str, Any],
        prefetched_evidence: PlaceSearchEvidence | None = None,
        external_search_latency_ms: int | None = None,
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
                    model=self._current_requirement_model(task.task_id),
                    plan_payload=self._role_state.get("current_plan_payload"),
                ),
            }

        completion_monotonic, completion_wall = self._clock.reserve()
        completion = await self._complete_current_lookup(
            handle,
            created_monotonic_ms=completion_monotonic,
            created_wall_clock_ms=completion_wall,
            prefetched_evidence=prefetched_evidence,
            external_search_latency_ms=external_search_latency_ms,
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
                model=self._current_requirement_model(current_task.task_id),
                plan_payload=self._role_state.get("current_plan_payload"),
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
        model = self._current_requirement_model(task.task_id)
        ledger = self._slot_ledger_for_task(task.task_id)
        tool_gaps = assess_requirements(model=model, ledger=ledger).tool_gaps if model is not None else ()
        tool_evidence_ref = self._next_ref("evidence", "tool_requirement")
        tool_updates = tuple(
            SlotUpdate(
                name=requirement_id,
                normalized_value=payload.get("result_ref", payload.get("results", "tool result available")),
                raw_evidence="current-plan ToolResult",
                state=SlotState.RESOLVED,
                evidence_ref=tool_evidence_ref,
                source="tool_result",
                plan_version=task.current_plan_version,
                explicit_or_inferred="tool",
            )
            for requirement_id in tool_gaps
        )
        self._record_evidence(
            tool_evidence_ref,
            task_id=task.task_id,
            plan_version=task.current_plan_version,
            label="当前计划只读工具证据",
            summary="当前计划的只读工具结果已作为 TOOL requirement 证据记录；它不具有指令权限。",
            source="web_search" if payload.get("trust_level") == "UNTRUSTED_WEB_EVIDENCE" else "demo_tool",
            trust_level=str(payload.get("trust_level", "TRUSTED_DEMO_TOOL_RESULT")),
            slot_updates=tool_updates,
        )
        self._slot_values.update(_slot_value_map(tool_updates))
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
        prefetched_evidence: PlaceSearchEvidence | None = None,
        external_search_latency_ms: int | None = None,
    ) -> Any:
        if handle.request.tool_name != "webSearch" or self._config.provider_mode == "fake":
            return self._tool_executor.complete(
                handle,
                created_monotonic_ms=created_monotonic_ms,
                created_wall_clock_ms=created_wall_clock_ms,
            )
        query = str(handle.request.arguments["query"])
        evidence = prefetched_evidence
        if evidence is None:
            self._record_live_progress(
                kind="external_place_search_started",
                status="started",
                phase="tool",
                label="正在调用只读网页检索工具",
                detail="只读取公开地图 POI 与搜索摘要；网页内容会作为不可信证据隔离，不会执行其中的指令。",
                tool_name="webSearch",
                plan_version=handle.plan_version,
                tool_input_summary="只读 webSearch query（参数值已从 shareable trace 省略）。",
            )
            search_started = time.perf_counter()
            evidence = await asyncio.to_thread(self._place_search_adapter.search, query=query)
            external_search_latency_ms = max(
                0,
                int((time.perf_counter() - search_started) * 1000),
            )
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
            label="只读网页检索工具已返回",
            detail=(
                f"未能取得外部结果（{evidence.degraded_reason or 'unknown'}），系统会如实保留查询失败状态。"
                if evidence.degraded_reason
                else f"已取得 {len(evidence.results)} 条带来源链接的地图/网页摘要，正在由 SlowTask 生成当前方案。"
            ),
            tool_name="webSearch",
            plan_version=handle.plan_version,
            tool_output_summary=f"返回 {len(evidence.results)} 条可引用的检索结果。",
            latency_ms=external_search_latency_ms,
        )
        return completion

    def _handle_foreground_chat(self, *, text: str, decision: str, task_focus: str) -> dict[str, str]:
        if _is_context_memory_complaint(text):
            return {
                "status": "foreground_chat",
                "assistant_summary": "你说得对，我会沿用前面已经接受的 requirement，不会要求你重复补充。你可以继续修改约束，或直接让我继续推进。",
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
            "assistant_summary": "当前没有已完成的规划可以展示。请先说明任务目标和关键约束，我会按动态 requirement model 继续处理。",
        }

    def _handle_no_active_cancel(self) -> dict[str, str]:
        task_state, _, _ = self._projections()
        last_task = self._task_for_context(task_state)
        if last_task is not None and last_task.lifecycle_state == "COMPLETED":
            return {
                "status": "no_active_task_to_cancel",
                "assistant_summary": (
                    "当前没有正在执行的规划可取消：上一份方案已经完成，因此不会再打开取消确认。"
                    "如果你想修改已完成方案，请直接说明要调整的 requirement，我会创建一份明确标注的修订任务。"
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
                    task_event_seq=_optional_int(item.get("task_event_seq")),
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
                    role=_optional_str(item.get("role")),
                    proposal_id=_optional_str(item.get("proposal_id")),
                    context_hash=_optional_str(item.get("context_hash")),
                    public_summary=_optional_str(item.get("public_summary")),
                    validation_status=_optional_str(item.get("validation_status")),
                    degraded_reason=_optional_str(item.get("degraded_reason")),
                    next_role=_optional_str(item.get("next_step")),
                    accepted=_optional_bool(item.get("accepted")),
                    rejected=_optional_bool(item.get("rejected")),
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
        replayed_role_state = self._replayed_role_state(active_or_last)
        return TaskContextPackBuilder().build(
            slowtask_state=slowtask_state,
            task_focus_state=focus_state,
            tool_execution_state=tool_state,
            evidence_catalog=self._evidence_catalog,
            conversation=self._conversation,
            latest_user_input=self._last_user_input,
            resolved_argument_values=self._resolved_argument_values,
            task_created_event_id=task_created_event_id,
            role_state=replayed_role_state,
        )

    def _replayed_role_state(self, task: SlowTaskRecord | None) -> Mapping[str, Any]:
        """Project auditable role state from the journal, with local plan payload kept private."""

        if task is None:
            return dict(self._role_state)
        events = [
            event
            for event in self._journal.events()
            if event.get("event_name") == "SLOW_LLM_STRUCTURED_OUTPUT_EMITTED"
            and event.get("task_id") == task.task_id
        ]
        current_events = [
            event for event in events if int(event.get("plan_version", 0)) == task.current_plan_version
        ]
        latest = current_events[-1] if current_events else None
        planners = [event for event in current_events if event.get("role") == "PLANNER"]
        reviewers = [event for event in current_events if event.get("role") == "REVIEWER"]
        deterministic_planning_mode = (
            "INFORMATION_GATHERING"
            if task.tool_validation_reports and task.lifecycle_state in {"EXECUTING", "PLANNING"}
            else None
        )
        return {
            "current_role": latest.get("role") if latest is not None else None,
            "prior_role_proposal_refs": [
                str(event["structured_output_ref"])
                for event in events[-24:]
                if event.get("structured_output_ref")
            ],
            "planner_mode": (
                planners[-1].get("planning_mode")
                if planners
                else deterministic_planning_mode
            ),
            "current_plan_proposal_ref": (
                planners[-1].get("structured_output_ref") if planners else None
            ),
            "reviewer_status": reviewers[-1].get("reviewer_verdict") if reviewers else None,
            "current_plan_payload": self._role_state.get("current_plan_payload"),
        }

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

    def _slot_ledger_for_task(self, task_id: str | None = None) -> SlotLedger:
        updates = list(updates_from_evidence_catalog(self._evidence_catalog, task_id=task_id))
        task_state, _, _ = self._projections()
        task = task_state.tasks.get(task_id) if task_id is not None else self._task_for_context(task_state)
        if task is not None and task.task_requirement_model is not None:
            model = task_requirement_model_from_dict(
                task.task_requirement_model, tool_registry=self._tool_registry
            )
            tool_requirement_ids = {
                spec.requirement_id
                for spec in model.requirements
                if spec.source_route == RequirementSourceRoute.TOOL
            }
            updates = [
                update
                for update in updates
                if update.name not in tool_requirement_ids
                or update.plan_version == task.current_plan_version
            ]
        known_names = {update.name for update in updates}
        if task is not None:
            for requirement_id, status in task.requirement_states.items():
                if requirement_id in known_names or status in {"UNKNOWN", "NOT_APPLICABLE"}:
                    continue
                refs = task.requirement_state_evidence_refs.get(requirement_id, ())
                updates.append(
                    SlotUpdate(
                        name=requirement_id,
                        normalized_value=None,
                        raw_evidence="recorded requirement state",
                        state=SlotState(status),
                        evidence_ref=refs[0] if refs else "evidence://journal/requirement-state",
                        source="event_journal_replay",
                        plan_version=task.current_plan_version,
                    )
                )
        return build_slot_ledger(
            updates,
            asked_counts=self._clarification_asked_counts(),
        )

    def _current_requirement_model(self, task_id: str | None = None) -> TaskRequirementModel | None:
        task_state, _, _ = self._projections()
        task = task_state.tasks.get(task_id) if task_id is not None else self._task_for_context(task_state)
        if task is None or task.task_requirement_model is None:
            return None
        return task_requirement_model_from_dict(task.task_requirement_model, tool_registry=self._tool_registry)

    def _requirement_assessment(self, task_id: str | None = None) -> RequirementAssessment:
        model = self._current_requirement_model(task_id)
        if model is None:
            raise ValueError("SlowTask has no accepted TaskRequirementModel")
        return assess_requirements(model=model, ledger=self._slot_ledger_for_task(task_id))

    def _clarification_plan(
        self,
        *,
        task_id: str | None = None,
        clarification_id: str | None = None,
    ) -> _DynamicClarificationPlan | None:
        model = self._current_requirement_model(task_id)
        if model is None:
            return None
        ledger = self._slot_ledger_for_task(task_id)
        assessment = assess_requirements(model=model, ledger=ledger)
        selected = select_user_blockers(model=model, ledger=ledger)
        if not selected:
            return None
        blocked_stage = next(
            (stage.value for stage in RequirementStage if set(selected) & set(assessment.missing_by_stage[stage.value])),
            RequirementStage.PLAN.value,
        )
        counts = self._clarification_asked_counts()
        reason = "conflicting" if set(selected) & set(assessment.conflicting) else "ambiguous" if set(selected) & set(assessment.ambiguous) else "missing"
        return _DynamicClarificationPlan(
            clarification_id=clarification_id or self._next_id("clarification"),
            blocked_stage=blocked_stage,
            ask_fields=selected,
            known_fields=ledger.resolved_fields(),
            reason=reason,
            attempt=1 + max((counts.get(item, 0) for item in selected), default=0),
        )

    def _clarification_asked_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self._journal.events():
            if event.get("event_name") != "CLARIFICATION_REQUESTED":
                continue
            for requirement_id in event.get("missing_or_ambiguous_fields", ()):
                name = str(requirement_id)
                counts[name] = counts.get(name, 0) + 1
        return counts

    def _role_invocation(
        self,
        *,
        task_id: str,
        caused_by_event_id: str,
        source_evidence_refs: Sequence[str],
        role_context: Mapping[str, Any],
        prefix: str,
    ) -> dict[str, Any]:
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        context_pack = self._context_pack().to_dict()
        mono, wall = self._clock.reserve()
        return {
            "task_context_pack": context_pack,
            "role_context": dict(role_context),
            "task_binding": {
                "task_id": task.task_id,
                "plan_version": task.current_plan_version,
                "task_event_seq": task.current_task_event_seq,
            },
            "source_evidence_refs": tuple(source_evidence_refs),
            "event_id_prefix": self._next_id(prefix),
            "caused_by_event_id": caused_by_event_id,
            "created_monotonic_ms": mono,
            "created_wall_clock_ms": wall,
            "context_hash": context_pack["context_hash"],
        }

    async def _run_task_modeler(
        self,
        *,
        run: RoleRun,
        task_id: str,
        intent: str,
        caused_by_event_id: str,
        source_evidence_refs: Sequence[str],
        goal_override: str | None = None,
    ) -> tuple[TaskRequirementModel, RoleInvocationResult]:
        context = self._context_pack().to_dict()
        invocation = self._role_invocation(
            task_id=task_id,
            caused_by_event_id=caused_by_event_id,
            source_evidence_refs=source_evidence_refs,
            role_context={
                "goal": goal_override or context.get("current_goal", intent),
                "latest_user_input": intent,
                "accepted_evidence_summary": context.get("accepted_evidence_summaries", ()),
                "known_facts": [
                    item
                    for item in context.get("requirement_summary", ())
                    if item.get("status") == "RESOLVED"
                ],
                "available_tool_manifest_summaries": context.get("available_tool_manifest_summaries", ()),
                "system_constraints": ["proposal_only", "demo_sandbox_only", "no_secrets"],
            },
            prefix="task_modeler",
        )
        return await self._role_orchestrator.model_task(run=run, invocation=invocation)

    async def _run_requirement_analyst(
        self,
        *,
        run: RoleRun,
        task_id: str,
        intent: str,
        evidence_ref: str,
        caused_by_event_id: str,
    ) -> tuple[tuple[SlotUpdate, ...], RoleInvocationResult]:
        model = self._current_requirement_model(task_id)
        if model is None:
            raise ValueError("Requirement Analyst requires an accepted TaskRequirementModel")
        ledger = self._slot_ledger_for_task(task_id)
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        invocation = self._role_invocation(
            task_id=task_id,
            caused_by_event_id=caused_by_event_id,
            source_evidence_refs=(evidence_ref,),
            role_context={
                "task_requirement_model": model.to_dict(),
                "requirement_summary": list(ledger.summary({item.requirement_id: item.label for item in model.requirements})),
                "evidence_text": intent,
                "source_evidence_refs": [evidence_ref],
                "stale_evidence_refs": list(task.stale_evidence_refs),
            },
            prefix="requirement_analyst",
        )
        return await self._role_orchestrator.analyze_requirements(
            run=run,
            model=model,
            ledger=ledger,
            stale_evidence_refs=task.stale_evidence_refs,
            invocation=invocation,
        )

    async def _analyze_registered_or_role(
        self,
        *,
        run: RoleRun,
        task_id: str,
        intent: str,
        evidence_ref: str,
        caused_by_event_id: str,
    ) -> tuple[tuple[SlotUpdate, ...], RoleInvocationResult | None]:
        """Use the registered extractor for explicit facts; call Codex only otherwise."""

        model = self._current_requirement_model(task_id)
        if model is not None and self._task_profiles.has_deterministic_extractor(model.task_kind):
            task_state, _, _ = self._projections()
            task = task_state.tasks[task_id]
            updates = extract_profile_updates(
                profile_id=model.task_kind,
                text=intent,
                evidence_ref=evidence_ref,
                plan_version=task.current_plan_version,
                source="deterministic_registered_requirement_extractor",
            )
            self._record_live_progress(
                kind="deterministic_requirement_analysis",
                status="completed",
                phase="validation",
                label="已用注册规则提取明确条件",
                detail=f"Python 提取并校验了 {len(updates)} 个显式 requirement；未启动 Requirement Analyst provider。",
                task_id=task_id,
                plan_version=task.current_plan_version,
            )
            return updates, None
        return await self._run_requirement_analyst(
            run=run,
            task_id=task_id,
            intent=intent,
            evidence_ref=evidence_ref,
            caused_by_event_id=caused_by_event_id,
        )

    def _accept_requirement_updates(self, *, evidence_ref: str, updates: Sequence[SlotUpdate]) -> None:
        if evidence_ref not in self._evidence_catalog:
            raise ValueError("accepted requirement updates require recorded evidence")
        self._evidence_catalog[evidence_ref]["slot_updates"] = [update.to_metadata() for update in updates]
        self._slot_values.update(_slot_value_map(updates))

    def _record_requirement_state_acceptance(
        self,
        *,
        task_id: str,
        updates: Sequence[SlotUpdate],
        caused_by_event_id: str,
    ) -> Mapping[str, Any] | None:
        if not updates:
            return None
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        evidence_refs = tuple(dict.fromkeys(update.evidence_ref for update in updates))
        return self._slowtask_runtime.accept_requirement_states(
            task_id=task.task_id,
            plan_version=task.current_plan_version,
            task_event_seq=task.current_task_event_seq + 1,
            caused_by_event_id=caused_by_event_id,
            event_id=self._next_id("requirement_states_accepted"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            evidence_refs=evidence_refs,
            accepted_requirement_states=[
                {
                    "requirement_id": update.name,
                    "status": update.state.value,
                    "source_evidence_refs": [update.evidence_ref],
                }
                for update in updates
            ],
        )

    def _invalidate_requirement_evidence(self, task_id: str) -> None:
        for item in self._evidence_catalog.values():
            if str(item.get("task_id", "")) == task_id:
                item["slot_updates"] = []
        self._slot_values.clear()

    def _track_role_result(self, result: RoleInvocationResult, *, next_role: str | None) -> None:
        self._role_state["current_role"] = result.role.value
        refs = list(self._role_state.get("prior_role_proposal_refs", ()))
        refs.append(result.proposal_ref)
        self._role_state["prior_role_proposal_refs"] = refs[-24:]
        trace = dict(result.trace_item)
        trace["provider_mode"] = self._config.provider_mode
        trace["output_mode"] = str(result.structured_output_event.get("output_mode", "degraded"))
        trace["result_present"] = True
        trace["next_step"] = next_role
        trace["task_event_seq"] = result.proposal["task_binding"].get("task_event_seq")
        trace["accepted"] = True
        trace["rejected"] = result.validation_failed_event is not None
        self._append_provider_trace((trace,), base_monotonic_ms=self._clock.monotonic_ms)
        self._record_live_progress(
            kind="role_invocation",
            status=result.validation_status,
            phase="codex_role",
            label=f"{result.role.value} 结构化 proposal 已校验",
            detail=str(result.proposal["public_summary"]),
            task_id=_optional_str(result.proposal["task_binding"].get("task_id")),
            plan_version=_optional_int(result.proposal["task_binding"].get("plan_version")),
            proposal_only=True,
            result_present=True,
            orchestration_role=result.role.value,
            next_step=next_role,
            blocked_on_user=result.role.value == "CLARIFIER",
        )

    def _build_planner_invocation(
        self,
        *,
        task_id: str,
        caused_by_event_id: str,
        fallback_evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        model = self._current_requirement_model(task_id)
        if model is None:
            raise ValueError("Planner requires an accepted TaskRequirementModel")
        ledger = self._slot_ledger_for_task(task_id)
        assessment = assess_requirements(model=model, ledger=ledger)
        allowed_contracts = self._tool_gap_resolver.allowed_contracts(
            model=model,
            active_tool_gap_ids=assessment.tool_gaps,
            tool_registry=self._tool_registry,
        )
        planner_facts = accepted_fact_inputs_from_ledger(
            ledger=ledger,
            requirement_ids=tuple(spec.requirement_id for spec in model.requirements),
            stale_evidence_refs=task.stale_evidence_refs,
        )
        planner_evidence_refs = tuple(
            dict.fromkeys(
                ref
                for fact in planner_facts
                for ref in fact.source_evidence_refs
            )
        ) or tuple(fallback_evidence_refs)
        role_context = {
            "task_requirement_model": model.to_dict(),
            "task_summary": model.task_summary,
            "resolved_requirement_ids": list(ledger.resolved_fields()),
            "accepted_facts": ledger.value_map(),
            "accepted_fact_inputs": [fact.to_safe_dict() for fact in planner_facts],
            "tool_gap_ids": list(assessment.tool_gaps),
            "allowed_tool_contracts": [contract.to_dict() for contract in allowed_contracts],
            "current_plan_evidence": list(planner_evidence_refs),
            "stale_evidence_refs": list(task.stale_evidence_refs),
            "success_criteria": list(model.success_criteria),
            "planner_mode": (
                "INFORMATION_GATHERING"
                if assessment.tool_gaps
                else "FINAL_PLAN_CANDIDATE"
            ),
            "tool_validation_report": self._role_state.get("latest_tool_validation_report"),
            "accepted_requirement_coverage": list(ledger.resolved_fields()),
        }
        return self._role_invocation(
            task_id=task_id,
            caused_by_event_id=caused_by_event_id,
            source_evidence_refs=planner_evidence_refs,
            role_context=role_context,
            prefix="planner_reviewer",
        )

    async def _advance_after_requirement_analysis(
        self,
        *,
        run: RoleRun,
        task_id: str,
        intent: str,
        caused_by_event_id: str,
        evidence_refs: Sequence[str],
        router_event: Mapping[str, Any],
        review_existing_plan: bool = False,
    ) -> dict[str, str]:
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        model = self._current_requirement_model(task_id)
        if model is None:
            return {"status": "planning_blocked", "assistant_summary": "任务需求模型尚未通过校验，当前不会规划或启动工具。"}
        ledger = self._slot_ledger_for_task(task_id)
        assessment = assess_requirements(model=model, ledger=ledger)
        clarification = self._clarification_plan(task_id=task_id)
        if clarification is not None:
            return await self._request_schema_clarification(
                task=task,
                caused_by_event_id=caused_by_event_id,
                evidence_refs=evidence_refs,
                clarification=clarification,
                intent=intent,
                run=run,
            )
        if assessment.derived_or_system_gaps:
            return {
                "status": "planning_blocked",
                "assistant_summary": "仍有只能由系统或受验证推导提供的条件，当前按 fail-closed 保持规划状态。",
            }
        if task.lifecycle_state == "WAITING_FOR_SLOT":
            resumed = self._slowtask_runtime.run_planning_started(
                task_id=task.task_id,
                plan_version=task.current_plan_version,
                caused_by_event_id=str(task.last_slowtask_event_id),
                event_id_prefix=self._next_id("requirement_resolution_planning"),
                created_monotonic_ms=self._clock.monotonic_ms,
                created_wall_clock_ms=self._clock.wall_clock_ms,
                start_task_event_seq=task.current_task_event_seq + 1,
                from_state="WAITING_FOR_SLOT",
                planning_reason="selected_user_requirements_resolved_without_plan_replacement",
            )
            caused_by_event_id = str(resumed.produced_events[-1]["event_id"])
            task_state, _, _ = self._projections()
            task = task_state.tasks[task_id]
        if assessment.tool_gaps:
            existing_handle = self._handle_for_task(task_id)
            if (
                existing_handle is not None
                and existing_handle.plan_version == task.current_plan_version
            ):
                return {
                    "status": "tool_running",
                    "assistant_summary": "已合并本轮明确条件；当前版本的只读查询仍在进行，不会重复启动相同工具链路。",
                }
            started = self._compile_and_start_active_tool_gap(
                task_id=task_id,
                caused_by_event_id=caused_by_event_id,
                active_tool_gap_ids=assessment.tool_gaps,
            )
            published = await self._maybe_auto_publish_ready_plan(
                started=started,
                router_event=router_event,
            )
            return published
        invocation = self._build_planner_invocation(
            task_id=task_id,
            caused_by_event_id=caused_by_event_id,
            fallback_evidence_refs=evidence_refs,
        )
        existing_planner = self._current_planner_results.get(
            (task.task_id, task.current_plan_version)
        )
        if review_existing_plan and existing_planner is not None:
            reviewed = await self._role_orchestrator.review_existing_plan(
                run=run,
                invocation=invocation,
                planner=existing_planner,
            )
        else:
            reviewed = await self._role_orchestrator.plan_and_review(
                run=run,
                invocation=invocation,
            )
            self._track_role_result(reviewed.planner, next_role="REVIEWER")
            self._current_planner_results[(task.task_id, task.current_plan_version)] = reviewed.planner
        self._track_role_result(reviewed.reviewer, next_role=None if reviewed.accepted else "BLOCKED")
        self._role_state["planner_mode"] = reviewed.planner.proposal["payload"].get("planning_mode")
        self._role_state["current_plan_proposal_ref"] = reviewed.planner.proposal_ref
        self._role_state["current_plan_payload"] = dict(reviewed.planner.proposal["payload"])
        self._role_state["reviewer_status"] = reviewed.reviewer.proposal["payload"].get("verdict", "BLOCK")
        self._codex_proposals.append(_legacy_role_proposal(reviewed.planner, model=model))
        if not reviewed.accepted:
            return _user_visible_role_result(
                status="planning_blocked",
                assistant_summary=_reviewer_block_user_summary(
                    reviewed.reviewer.proposal["payload"]
                ),
                role_result=reviewed.reviewer,
            )
        tool_calls = reviewed.planner.proposal["payload"].get("proposed_tool_calls", ())
        if tool_calls:
            self._record_live_progress(
                kind="planner_raw_tool_candidate_ignored",
                status="observed",
                phase="validation",
                label="已忽略 Planner 低层工具候选",
                detail="proposed_tool_calls 只作诊断；没有 active Python binding 时不会送入 Tool Executor。",
                task_id=task_id,
                plan_version=task.current_plan_version,
            )
        return _user_visible_role_result(
            status="plan_ready",
            assistant_summary=str(reviewed.planner.proposal["payload"]["plan_summary"]),
            role_result=reviewed.planner,
        )

    def _compile_and_start_active_tool_gap(
        self,
        *,
        task_id: str,
        caused_by_event_id: str,
        active_tool_gap_ids: Sequence[str],
        diagnostic_candidate: Mapping[str, Any] | None = None,
        caused_by_proposal_ref: str | None = None,
    ) -> dict[str, str]:
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        model = self._current_requirement_model(task_id)
        if model is None:
            return {
                "status": "partial_plan",
                "assistant_summary": "任务需求模型尚未恢复；我先保留已确认条件和接待流程骨架，地点候选暂未检索。",
            }
        contracts = self._tool_gap_resolver.allowed_contracts(
            model=model,
            active_tool_gap_ids=active_tool_gap_ids,
            tool_registry=self._tool_registry,
        )
        if not contracts:
            self._record_live_progress(
                kind="tool_validation_report",
                status="blocked",
                phase="validation",
                label="当前 TOOL gap 没有注册 binding",
                detail="missing_validated_tool_binding；不会让模型自由选择其他工具。",
                task_id=task_id,
                plan_version=task.current_plan_version,
            )
            return {
                "status": "partial_plan",
                "assistant_summary": "已保留全部已确认条件并生成接待流程骨架；当前没有可用的只读地点检索绑定，因此地点候选尚未检索。",
            }
        contract = contracts[0]
        ledger = self._slot_ledger_for_task(task_id)
        accepted_facts = accepted_fact_inputs_from_ledger(
            ledger=ledger,
            requirement_ids=contract.input_requirement_ids,
            stale_evidence_refs=task.stale_evidence_refs,
        )
        compile_started = time.perf_counter()
        compiled, report = self._tool_argument_compiler.compile(
            task_id=task_id,
            plan_version=task.current_plan_version,
            contract=contract,
            accepted_fact_inputs=accepted_facts,
            tool_registry=self._tool_registry,
            caused_by_proposal_ref=caused_by_proposal_ref,
            diagnostic_candidate=diagnostic_candidate,
        )
        compile_latency_ms = max(0, int((time.perf_counter() - compile_started) * 1000))
        self._role_state["latest_tool_validation_report"] = report.to_dict()
        self._role_state["allowed_tool_contracts"] = [item.to_dict() for item in contracts]
        self._role_state["accepted_fact_inputs"] = [item.to_safe_dict() for item in accepted_facts]
        self._record_live_progress(
            kind="tool_validation_report",
            status=report.status.value.lower(),
            phase="validation",
            label=f"Python ToolValidationReport {report.status.value}",
            detail=(
                f"binding={report.binding_id}; tool={report.tool_name}; "
                f"arguments={','.join(report.provided_arguments) or 'none'}; "
                f"reason_codes={','.join(report.reason_codes) or 'none'}"
            ),
            task_id=task_id,
            plan_version=task.current_plan_version,
            tool_name=report.tool_name,
            latency_ms=compile_latency_ms,
        )
        if compiled is None or report.status != ToolValidationStatus.PASS:
            missing = ", ".join((*report.missing_arguments, *report.missing_provenance)) or "可编译的查询依据"
            return {
                "status": "partial_plan",
                "assistant_summary": f"已保留全部已确认条件并生成接待流程骨架；只读查询暂未启动，因为缺少 {missing}。",
            }
        return self._start_compiled_tool(
            task_id=task_id,
            caused_by_event_id=caused_by_event_id,
            compiled=compiled,
            report=report,
            accepted_facts=accepted_facts,
        )

    def _start_compiled_tool(
        self,
        *,
        task_id: str,
        caused_by_event_id: str,
        compiled: CompiledToolCall,
        report: ToolValidationReport,
        accepted_facts: Sequence[Any],
    ) -> dict[str, str]:
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        ledger = self._slot_ledger_for_task(task_id)
        evidence_refs = tuple(
            dict.fromkeys(
                ref
                for fact in accepted_facts
                for ref in fact.source_evidence_refs
            )
        )
        resolved_fields = tuple(
            requirement_id
            for requirement_id in compiled.contract.input_requirement_ids
            if requirement_id in ledger.resolved_fields()
        )
        review = self._slowtask_runtime.review_evidence(
            task_id=task_id,
            plan_version=task.current_plan_version,
            caused_by_event_id=caused_by_event_id,
            event_id_prefix=self._next_id("compiled_tool_review"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=task.current_task_event_seq + 1,
            evidence_refs=evidence_refs,
            required_fields=resolved_fields,
            resolved_fields=resolved_fields,
            resolved_arguments_ref=self._next_ref("args", "compiled"),
            provenance_ref=self._next_ref("provenance", "compiled"),
            field_provenance_refs=evidence_refs,
            resolved_arguments_metadata={
                "tool_validation_report": report.to_dict(),
                "compiled_argument_fingerprint": compiled.argument_fingerprint,
                "tool_binding": compiled.contract.to_dict(),
                "accepted_fact_inputs": [fact.to_safe_dict() for fact in accepted_facts],
            },
        )
        arguments_event = next(
            event for event in review.produced_events if event["event_name"] == "ARGUMENTS_RESOLVED"
        )
        provenance_event = next(
            event for event in review.produced_events
            if event["event_name"] == "ARGUMENT_RESOLUTION_PROVENANCE"
        )
        arguments = dict(compiled.arguments)
        self._resolved_argument_values = arguments
        task_state, _, _ = self._projections()
        task = task_state.tasks[task_id]
        request = ToolExecutionRequest(
            tool_call_id=self._next_id("tool_call"),
            tool_name=compiled.contract.tool_name,
            task_id=task_id,
            plan_version=task.current_plan_version,
            current_plan_version=task.current_plan_version,
            start_task_event_seq=task.current_task_event_seq + 1,
            caused_by_event_id=str(review.produced_events[-1]["event_id"]),
            event_id_prefix=self._next_id("compiled_tool"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            idempotency_key=self._next_id("idempotency"),
            arguments=arguments,
            argument_provenance={name: str(provenance_event["event_id"]) for name in arguments},
            resolved_arguments_ref=str(arguments_event["event_id"]),
            provenance_ref=str(provenance_event["event_id"]),
            preview_ref=self._next_ref("preview", "compiled_tool"),
        )
        tool_start_started = time.perf_counter()
        started = self._tool_executor.begin(request)
        tool_start_latency_ms = max(0, int((time.perf_counter() - tool_start_started) * 1000))
        self._record_live_progress(
            kind="tool_start",
            status="completed" if started.handle is not None else "blocked",
            phase="tool",
            label="Tool Executor 启动校验已完成",
            detail=(
                "Tool Executor 已通过当前 plan 的 manifest、provenance 与授权 gate。"
                if started.handle is not None
                else f"Tool Executor 阻止启动；blocking_fields={','.join(started.blocking_fields) or 'none'}。"
            ),
            task_id=task_id,
            plan_version=task.current_plan_version,
            tool_name=compiled.contract.tool_name,
            latency_ms=tool_start_latency_ms,
        )
        if started.handle is None:
            blocking = ", ".join(started.blocking_fields) or "未知字段"
            return {
                "status": "tool_blocked",
                "assistant_summary": f"只读工具未启动：Tool Executor 确认缺少 {blocking}。已保留全部条件和接待流程骨架。",
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
            "assistant_summary": "已保留全部已确认条件；Python 已确定性编译并校验只读查询，正在筛选地点候选。",
        }

    async def _request_schema_clarification(
        self,
        *,
        task: SlowTaskRecord,
        caused_by_event_id: str,
        evidence_refs: Sequence[str],
        clarification: _DynamicClarificationPlan,
        intent: str,
        run: RoleRun | None = None,
    ) -> dict[str, str]:
        review_cause = caused_by_event_id
        task_state, _, _ = self._projections()
        current_task = task_state.tasks[task.task_id]
        if current_task.lifecycle_state == "WAITING_FOR_SLOT":
            recheck = self._slowtask_runtime.run_planning_started(
                task_id=current_task.task_id,
                plan_version=current_task.current_plan_version,
                caused_by_event_id=str(current_task.last_slowtask_event_id),
                event_id_prefix=self._next_id("slot_clarification_recheck"),
                created_monotonic_ms=self._clock.monotonic_ms,
                created_wall_clock_ms=self._clock.wall_clock_ms,
                start_task_event_seq=current_task.current_task_event_seq + 1,
                from_state="WAITING_FOR_SLOT",
                planning_reason="schema_clarification_recheck_same_plan_version",
            )
            review_cause = str(recheck.produced_events[0]["event_id"])
            task_state, _, _ = self._projections()
            current_task = task_state.tasks[task.task_id]

        assessment = self._requirement_assessment(task.task_id)
        selected_fields = clarification.ask_fields
        ambiguous_or_conflicting = (
            selected_fields
            if clarification.reason in {"ambiguous", "conflicting"}
            else tuple((*assessment.ambiguous, *assessment.conflicting))
        )
        model = self._current_requirement_model(task.task_id)
        if model is None:
            return {"status": "planning_blocked", "assistant_summary": "任务需求模型不存在，当前无法生成追问。"}
        review = self._slowtask_runtime.review_evidence(
            task_id=current_task.task_id,
            plan_version=current_task.current_plan_version,
            caused_by_event_id=review_cause,
            event_id_prefix=self._next_id("schema_clarification_review"),
            created_monotonic_ms=self._clock.monotonic_ms,
            created_wall_clock_ms=self._clock.wall_clock_ms,
            start_task_event_seq=current_task.current_task_event_seq + 1,
            evidence_refs=evidence_refs,
            required_fields=tuple(item.requirement_id for item in model.requirements),
            resolved_fields=self._slot_ledger_for_task(current_task.task_id).resolved_fields(),
            ambiguous_fields=ambiguous_or_conflicting,
            missing_fields=selected_fields if clarification.reason == "missing" else (),
            clarification_prompt_ref=self._next_ref("prompt", "missing_slots"),
            resolution_reason=f"schema_requirement_{clarification.reason}",
        )
        evidence_reviewed_event = next(
            event for event in review.produced_events if event["event_name"] == "EVIDENCE_REVIEWED"
        )
        specs = model.requirements_by_id
        labels = {item: specs[item].label for item in selected_fields}
        question = _deterministic_clarification_question(selected_fields, labels)
        proposal_id = self._next_id("deterministic_clarifier")
        self._codex_proposals.append(
            {
                "proposal_id": proposal_id,
                "proposal_type": "clarification",
                "status": "validated",
                "summary": question,
                "question_text": question,
                "covered_fields": list(selected_fields),
                "backend_selected_ask_fields": list(selected_fields),
                "diagnostic_model_missing_fields": list(selected_fields),
                "known_fields": list(self._slot_ledger_for_task(current_task.task_id).resolved_fields()),
                "missing_fields": list(selected_fields),
                "proposal_only": True,
                "role": "PYTHON_DETERMINISTIC_CLARIFIER",
                "context_hash": self._context_pack().context_hash,
                "degraded_reason": None,
                "suggested_next_steps": ["等待用户补充后重新评估 requirement。"],
                "requires_confirmation": False,
                "risk_notes": [],
                "source_evidence_refs": list(evidence_refs),
                "safety": {
                    "codex_is_fact_owner": False,
                    "advances_plan_version": False,
                    "authorizes_tool": False,
                    "contains_secret": False,
                    "mutates_task_snapshot": False,
                    "emits_canonical_event": False,
                    "executes_external_tool": False,
                    "contains_raw_provider_body": False,
                    "no_state_mutation": True,
                    "no_plan_version_advance": True,
                    "no_tool_execution": True,
                    "no_tool_authorization": True,
                    "no_semantic_commitment": True,
                    "no_ui_mutation": True,
                    "no_chain_of_thought": True,
                },
            }
        )
        self._record_live_progress(
            kind="deterministic_clarification",
            status="completed",
            phase="clarification",
            label="已生成确定性追问",
            detail="追问字段由 Python 选择并按注册 label 渲染；本轮未启动 Clarifier provider。",
            task_id=current_task.task_id,
            plan_version=current_task.current_plan_version,
            blocked_on_user=True,
        )
        return {
            "status": "waiting_for_slot",
            "assistant_summary": question,
            "user_visible_source_role": "PYTHON_DETERMINISTIC_CLARIFIER",
            "user_visible_proposal_id": proposal_id,
            "user_visible_context_hash": self._context_pack().context_hash,
            "user_visible_caused_by_event_id": str(evidence_reviewed_event["event_id"]),
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
        slot_updates: Sequence[SlotUpdate] = (),
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
            "slot_updates": [update.to_metadata() for update in slot_updates],
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


def _legacy_role_proposal(
    result: RoleInvocationResult, *, model: TaskRequirementModel
) -> dict[str, Any]:
    payload = result.proposal["payload"]
    safety = {
        "codex_is_fact_owner": False,
        "advances_plan_version": False,
        "authorizes_tool": False,
        "contains_secret": False,
        "mutates_task_snapshot": False,
        "emits_canonical_event": False,
        "executes_external_tool": False,
        "contains_raw_provider_body": False,
    }
    if result.role.value == "CLARIFIER":
        covered = list(payload.get("covered_requirement_ids", ()))
        return {
            "proposal_id": result.proposal["proposal_id"],
            "proposal_type": "clarification",
            "status": result.validation_status,
            "summary": result.proposal["public_summary"],
            "question_text": payload.get("question_text", ""),
            "covered_fields": covered,
            "backend_selected_ask_fields": covered,
            "diagnostic_model_missing_fields": covered,
            "known_fields": [],
            "missing_fields": covered,
            "proposal_only": True,
            "role": result.role.value,
            "context_hash": result.proposal["input_context_hash"],
            "degraded_reason": result.degraded_reason,
            "suggested_next_steps": ["等待用户补充后交回 Requirement Analyst。"],
            "requires_confirmation": False,
            "risk_notes": [],
            "source_evidence_refs": list(result.proposal.get("source_evidence_refs", ())),
            "safety": safety,
        }
    return {
        "proposal_id": result.proposal["proposal_id"],
        "proposal_type": "plan",
        "status": result.validation_status,
        "summary": payload.get("plan_summary", result.proposal["public_summary"]),
        "covered_fields": list(payload.get("requirement_coverage", ())),
        "known_fields": list(payload.get("requirement_coverage", ())),
        "missing_fields": [],
        "proposal_only": True,
        "role": result.role.value,
        "task_kind": model.task_kind,
        "planning_mode": payload.get("planning_mode"),
        "proposed_tool_calls": list(payload.get("proposed_tool_calls", ())),
        "context_hash": result.proposal["input_context_hash"],
        "degraded_reason": result.degraded_reason,
        "suggested_next_steps": list(payload.get("ordered_steps", ())),
        "requires_confirmation": bool(payload.get("requires_confirmation", False)),
        "risk_notes": list(payload.get("risks", ())),
        "source_evidence_refs": list(result.proposal.get("source_evidence_refs", ())),
        "safety": safety,
    }


def _slot_value_map(updates: Sequence[SlotUpdate]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for update in updates:
        if update.state == SlotState.RESOLVED:
            values[update.name] = update.normalized_value
        elif update.state in {SlotState.AMBIGUOUS, SlotState.CONFLICTING}:
            values[update.name] = update.normalized_value
    return values


def _deterministic_clarification_question(
    selected_requirement_ids: Sequence[str],
    labels: Mapping[str, str],
) -> str:
    rendered = [str(labels.get(requirement_id, requirement_id)) for requirement_id in selected_requirement_ids]
    if len(rendered) == 1:
        return f"请补充{rendered[0]}。"
    return "请补充" + "、".join(rendered) + "。"


def _user_facing_final_plan(
    *,
    slots: Mapping[str, str],
    tool_payload: Mapping[str, Any] | None,
    model: TaskRequirementModel,
    plan_payload: Mapping[str, Any] | None,
) -> str:
    """Render any accepted task model without adding domain-specific facts."""

    labels = model.requirements_by_id
    fact_lines = [
        f"- {labels[name].label}：{_display_slot_value(value)}"
        for name, value in slots.items()
        if name in labels
    ]
    ordered_steps = list(plan_payload.get("ordered_steps", ())) if isinstance(plan_payload, Mapping) else []
    sections = [f"最终规划草案：{model.task_summary}"]
    if fact_lines:
        sections.append("已接受的条件：\n" + "\n".join(fact_lines))
    if ordered_steps:
        sections.append("执行步骤：\n" + "\n".join(f"{index + 1}. {_safe_summary(str(step))}" for index, step in enumerate(ordered_steps)))
    external_results = tool_payload.get("results", []) if isinstance(tool_payload, Mapping) else []
    if isinstance(external_results, Sequence) and external_results:
        candidates: list[str] = []
        for item in external_results[:3]:
            if not isinstance(item, Mapping):
                continue
            title = _safe_summary(str(item.get("source_title", "网页检索结果")))
            url = _safe_summary(str(item.get("source_url", "")))
            snippet = _safe_summary(str(item.get("snippet_or_summary", "")))
            provider = _safe_summary(str(item.get("source_provider", "公开网页/地图")))
            if title and url:
                candidates.append(f"- {title}：{snippet}（{provider}；来源：{url}）")
        if candidates:
            sections.append("只读工具返回的可核验候选：\n" + "\n".join(candidates))
    if isinstance(tool_payload, Mapping) and tool_payload.get("trust_level") == "UNTRUSTED_WEB_EVIDENCE":
        degraded_reason = _safe_summary(str(tool_payload.get("degraded_reason", "unknown")))
        if not external_results:
            sections.append("只读检索没有返回可引用候选；已保留现有条件和流程骨架。")
        if degraded_reason:
            sections.append(f"外部只读证据处于 degraded 状态：{degraded_reason}。")
    sections.append("工具结果仅作为证据；系统没有执行预订、支付、消息发送或其他真实外部写操作。")
    return "\n".join(sections)


def _display_slot_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "无" if not value else "、".join(str(item) for item in value)
    return str(value)


def _user_facing_current_plan(
    *,
    slots: Mapping[str, str],
    tool_payload: Mapping[str, Any] | None,
    model: TaskRequirementModel | None,
    plan_payload: Mapping[str, Any] | None,
) -> str:
    """Make the non-terminal status explicit without changing grounded facts."""

    if model is None:
        return "当前任务模型尚未恢复，不能展示未验证的方案。"
    return (
        "当前可修改方案（尚未定稿）：\n"
        + _user_facing_final_plan(slots=slots, tool_payload=tool_payload, model=model, plan_payload=plan_payload)
        + "\n如需修改已接受条件，直接说明；SlowTask 会判断是否需要新的 plan_version。"
    )


def _user_visible_role_result(
    *,
    status: str,
    assistant_summary: str,
    role_result: RoleInvocationResult,
) -> dict[str, str]:
    return {
        "status": status,
        "assistant_summary": assistant_summary,
        "user_visible_source_role": role_result.role.value,
        "user_visible_proposal_id": str(role_result.proposal["proposal_id"]),
        "user_visible_context_hash": str(role_result.proposal["input_context_hash"]),
        "user_visible_caused_by_event_id": str(role_result.structured_output_event["event_id"]),
    }


def _reviewer_block_user_summary(payload: Mapping[str, Any]) -> str:
    if payload.get("premature_commitment") is True:
        reason = "方案包含尚未得到证据支持的承诺"
    elif payload.get("stale_evidence_usage"):
        reason = "方案引用了旧计划证据"
    elif payload.get("tool_binding_errors"):
        # Reviewer free text is not a parameter-validation fact owner.  A
        # concrete tool error may only be rendered from Python's report or a
        # canonical Tool Executor blocked event elsewhere in the session.
        reason = "Reviewer 提出了未被确定性工具报告支持的异议"
    elif payload.get("missing_requirement_coverage"):
        reason = "方案尚未覆盖已确认的必要条件"
    else:
        reason = "方案尚未通过安全与证据校验"
    return f"{reason}。我已保留你确认的条件，但当前不会提交或执行这个方案。"


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
