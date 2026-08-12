from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time

from voice_agent.runtime.slow_system_workbench_api import WorkbenchApi
from voice_agent.adapters import codex_slow_llm
from voice_agent.adapters.codex_slow_llm import (
    CodexSlowLLMAdapterConfig,
    _proposal_from_structured_output,
    parse_codex_jsonl_output,
    _run_codex_cli_async,
)
from voice_agent.runtime.slow_system_workbench_sessions import (
    WorkbenchRuntimeConfig,
    WorkbenchSession,
    WorkbenchSessionManager,
)
from voice_agent.adapters.workbench_place_search import PlaceSearchEvidence
from voice_agent.state.slowtask_state import SlowTaskState


async def _wait_for_lifecycle(session: WorkbenchSession, lifecycle: str) -> dict[str, object]:
    for _ in range(100):
        snapshot = await session.snapshot()
        if snapshot.get("task") and snapshot["task"]["lifecycle"] == lifecycle:
            return snapshot
        await asyncio.sleep(0)
    raise AssertionError(f"session did not reach lifecycle {lifecycle}")


async def _wait_for_planner(session: WorkbenchSession) -> dict[str, object]:
    for _ in range(100):
        snapshot = await session.snapshot()
        if snapshot.get("task") and snapshot["task"].get("current_plan_proposal_ref"):
            return snapshot
        await asyncio.sleep(0)
    raise AssertionError("session did not publish a current Planner proposal")


def test_exact_three_turn_customer_reception_uses_deterministic_tool_fast_path() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="exact_three_turn_customer_reception",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        trace_cursor = 0

        started = time.perf_counter()
        first = await session.process_message(
            "请帮忙规划一个接待午饭，选云南菜。",
            action="start",
        )
        first_elapsed_ms = int((time.perf_counter() - started) * 1000)
        first_trace = first["snapshot"]["provider_trace"][trace_cursor:]
        trace_cursor = len(first["snapshot"]["provider_trace"])

        started = time.perf_counter()
        second = await session.process_message(
            "8个人，地点在北京市海淀区中关村领展购物广场附近，人均200以内，有两位不吃辣，最好12点半。"
        )
        second_elapsed_ms = int((time.perf_counter() - started) * 1000)
        second_trace = second["snapshot"]["provider_trace"][trace_cursor:]
        trace_cursor = len(second["snapshot"]["provider_trace"])
        second_task = second["snapshot"]["task"]
        second_report = second_task["latest_tool_validation_report"]
        second_query = second["snapshot"]["context_pack"]["resolved_arguments"]["synthetic_values"]["query"]

        started = time.perf_counter()
        third = await session.process_message("中间我插一句，把时间修改到晚上吧。")
        third_elapsed_ms = int((time.perf_counter() - started) * 1000)
        third_trace = third["snapshot"]["provider_trace"][trace_cursor:]
        third_task = third["snapshot"]["task"]
        third_query = third["snapshot"]["context_pack"]["resolved_arguments"]["synthetic_values"]["query"]

        assert first["status"] == "waiting_for_slot"
        assert first["snapshot"]["task"]["task_kind"] == "customer_reception"
        assert first["snapshot"]["task"]["missing_fields"] == ["location_anchor", "party_size"]
        assert "位置范围" in first["snapshot"]["conversation"][-1]["text"]
        assert "参与人数" in first["snapshot"]["conversation"][-1]["text"]
        assert [item["role"] for item in first_trace if item.get("role")] == ["TASK_MODELER"]

        assert second["status"] == "tool_running"
        assert "工具参数未通过校验" not in second["snapshot"]["conversation"][-1]["text"]
        assert [item["role"] for item in second_trace if item.get("role")] == []
        assert second_report["status"] == "PASS"
        assert second_report["tool_name"] == "webSearch"
        assert second_report["provided_arguments"] == ["query"]
        assert all(value in second_query for value in (
            "北京市海淀区中关村领展购物广场附近", "云南菜", "8人",
            "人均200以内", "两位不吃辣", "12:30",
        ))
        second_events = [event["event_name"] for event in second["snapshot"]["timeline"]]
        assert "TOOL_MANIFEST_LOADED" in second_events
        assert "TOOL_ARGUMENTS_READY" in second_events
        assert "TOOL_EXECUTION_STARTED" in second_events
        provenance_refs = second["snapshot"]["context_pack"]["resolved_arguments"]["provenance_refs"]
        assert any("user_" in ref for ref in provenance_refs)
        assert any("patch_" in ref for ref in provenance_refs)

        assert third_task["current_plan_version"] == second_task["current_plan_version"] + 1
        assert [item["role"] for item in third_trace if item.get("role")] == []
        assert "晚上" in third_query
        assert "12:30" not in third_query
        requirements = {
            item["requirement_id"]: item for item in third_task["requirement_summary"]
        }
        assert {
            "party_size", "location_anchor", "budget", "dietary_constraints",
            "cuisine_preference", "time_window",
        } <= {key for key, item in requirements.items() if item["status"] == "RESOLVED"}
        assert any(
            event["event_name"] == "TOOL_RESULT_MARKED_STALE"
            for event in third["snapshot"]["timeline"]
        )

        replayed = SlowTaskState()
        for event in session.journal.events():
            replayed.reduce_event(event)
        replayed_task = replayed.tasks[third_task["task_id"]]
        assert replayed_task.current_plan_version == third_task["current_plan_version"]
        assert replayed_task.tool_validation_reports[-1]["argument_fingerprint"] == (
            third_task["latest_tool_validation_report"]["argument_fingerprint"]
        )
        assert replayed_task.stale_evidence_refs

        # Fake-clock/provider instrumentation should remain comfortably under
        # the interactive SLO while exposing safe per-stage latency metadata.
        assert max(first_elapsed_ms, second_elapsed_ms, third_elapsed_ms) < 1000
        assert any(
            item.get("kind") == "tool_validation_report" and item.get("latency_ms") is not None
            for item in second["snapshot"]["live_progress"]
        )

    asyncio.run(scenario())


def test_python_owned_session_starts_dynamic_tool_phase_with_fake_codex() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_dynamic_workbench",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        initial = await session.snapshot()
        assert initial["task"] is None
        result = await session.process_message(
            "帮我规划一个明天上午两天的客户来访行程，地点尽量靠近公司，6个人。",
            action="start",
        )
        snapshot = result["snapshot"]

        assert result["status"] == "tool_running"
        assert snapshot["mode"] == "text_first_python_owned"
        assert snapshot["router"]["router_decision"] == "SPAWN_SLOW_TASK"
        assert snapshot["task"]["lifecycle"] == "EXECUTING"
        assert snapshot["task"]["current_plan_version"] == 1
        assert snapshot["task"]["in_flight_tool_calls"]
        assert any(event["event_name"] == "TOOL_EXECUTION_STARTED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "WAITING_FOR_TOOL" for event in snapshot["timeline"])
        assert snapshot["capability_matrices"][-1]["output_mode"] == "mock"
        assert snapshot["provider_trace"][-1]["output_mode"] == "fake"
        assert snapshot["safety"]["raw_provider_body_included"] is False
        user_visible_reply = snapshot["conversation"][-1]["text"]
        assert "确定性编译并校验" in user_visible_reply
        assert snapshot["task"]["latest_tool_validation_report"]["status"] == "PASS"
        assert "synthetic company fixture" not in user_visible_reply
        assert "fixture" not in user_visible_reply
        assert "行程候选" not in user_visible_reply

    asyncio.run(scenario())


def test_python_owned_session_waits_for_user_when_critical_slots_are_missing() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_missing_slots_workbench",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        result = await session.process_message(
            "帮我规划一个接待客户流程。",
            action="start",
        )
        snapshot = result["snapshot"]

        assert result["status"] == "waiting_for_slot"
        assert snapshot["task"]["lifecycle"] == "WAITING_FOR_SLOT"
        assert snapshot["task"]["missing_fields"] == ["time_window", "location_anchor", "party_size"]
        assert snapshot["task"]["in_flight_tool_calls"] == []
        assert snapshot["codex_proposals"][-1]["proposal_type"] == "clarification"
        assert snapshot["codex_proposals"][-1]["backend_selected_ask_fields"] == [
            "time_window",
            "location_anchor",
            "party_size",
        ]
        assert snapshot["codex_proposals"][-1]["covered_fields"] == [
            "time_window",
            "location_anchor",
            "party_size",
        ]
        assert any(item.get("blocked_on_user") is True for item in snapshot["live_progress"])
        assert "接待时间" in result["snapshot"]["conversation"][-1]["text"]

    asyncio.run(scenario())


def test_workbench_remembers_user_supplied_slots_across_turns() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_slot_memory_workbench",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        first = await session.process_message(
            "请帮忙规划一个接待午饭，选云南菜。",
            action="start",
        )
        assert first["status"] == "waiting_for_slot"
        assert first["snapshot"]["task"]["missing_fields"] == ["location_anchor", "party_size"]
        assert first["snapshot"]["task"]["in_flight_tool_calls"] == []

        supplied = await session.process_message(
            "公司位置在北京中关村领展附近，具体时间为 7 月 25 号中午 12 点，6个人，人均200以内，没有忌口",
        )
        supplied_snapshot = supplied["snapshot"]
        assert supplied["status"] == "tool_running"
        assert supplied_snapshot["task"]["lifecycle"] == "EXECUTING"
        assert supplied_snapshot["task"]["current_plan_version"] == 1
        assert supplied_snapshot["task"]["missing_fields"] == []
        assert supplied_snapshot["task"]["in_flight_tool_calls"]
        resolved_requirements = {
            item["requirement_id"]
            for item in supplied_snapshot["task"]["requirement_summary"]
            if item["status"] == "RESOLVED"
        }
        assert resolved_requirements >= {
            "location_anchor",
            "time_window",
            "party_size",
        }
        assert supplied_snapshot["task"]["missing_fields"] == []
        assert "确定性编译并校验" in supplied_snapshot["conversation"][-1]["text"]

        plan_version = supplied_snapshot["task"]["current_plan_version"]
        complaint = await session.process_message("我不是已经把信息给你了吗")
        complaint_snapshot = complaint["snapshot"]
        assert complaint["status"] == "foreground_chat"
        assert complaint_snapshot["task"]["current_plan_version"] == plan_version
        assert complaint_snapshot["task"]["missing_fields"] == []
        assert "已经接受的 requirement" in complaint_snapshot["conversation"][-1]["text"]

    asyncio.run(scenario())


def test_customer_reception_core_fill_keeps_plan_version_and_passes_readiness() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_customer_reception_core_fill",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        initial = await session.process_message("帮我规划一个接待客户流程。", action="start")
        assert initial["status"] == "waiting_for_slot"
        assert initial["snapshot"]["task"]["missing_fields"] == [
            "time_window",
            "location_anchor",
            "party_size",
        ]
        assert initial["snapshot"]["task"]["in_flight_tool_calls"] == []

        supplied = await session.process_message("下周二下午，在中关村，6个人。")
        snapshot = supplied["snapshot"]

        assert supplied["status"] == "tool_running"
        assert snapshot["task"]["current_plan_version"] == 1
        assert snapshot["task"]["readiness"]["search"] is True
        assert snapshot["task"]["readiness"]["plan"] is False
        assert snapshot["task"]["planner_mode"] == "INFORMATION_GATHERING"
        assert snapshot["task"]["missing_fields"] == []
        slot_states = {item["name"]: item["state"] for item in snapshot["task"]["slot_summary"]}
        assert slot_states["time_window"] == "RESOLVED"
        assert slot_states["location_anchor"] == "RESOLVED"
        assert slot_states["party_size"] == "RESOLVED"
        assert not any(event["event_name"] == "PLAN_VERSION_ADVANCED" for event in snapshot["timeline"])

    asyncio.run(scenario())


def test_meal_planning_asks_budget_and_dietary_after_core_fields() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_meal_second_group",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        await session.process_message("帮我规划客户接待午餐，选云南菜。", action="start")
        result = await session.process_message("下周二下午，在中关村，6个人。")
        snapshot = result["snapshot"]

        assert result["status"] == "waiting_for_slot"
        assert snapshot["task"]["clarification"]["blocked_stage"] == "plan"
        assert snapshot["task"]["clarification"]["ask_fields"] == ["budget", "dietary_constraints"]
        assert snapshot["task"]["missing_fields"] == ["budget", "dietary_constraints"]
        assert snapshot["task"]["in_flight_tool_calls"] == []
        assert snapshot["codex_proposals"][-1]["backend_selected_ask_fields"] == [
            "budget",
            "dietary_constraints",
        ]

        filled = await session.process_message("人均300以内，没有忌口。")
        filled_snapshot = filled["snapshot"]
        assert filled["status"] == "tool_running"
        dietary = next(
            item for item in filled_snapshot["task"]["slot_summary"] if item["name"] == "dietary_constraints"
        )
        assert dietary["state"] == "RESOLVED"
        assert dietary["value_preview"] == "无"
        if filled_snapshot["task"]["clarification"]:
            assert "time_window" not in filled_snapshot["task"]["clarification"]["ask_fields"]

    asyncio.run(scenario())


def test_ambiguous_time_patch_reasks_time_without_tool_start() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_ambiguous_time_reask",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        await session.process_message("帮我规划一个接待客户流程。", action="start")
        result = await session.process_message("下周吧，在中关村，6个人。")
        snapshot = result["snapshot"]

        assert result["status"] == "waiting_for_slot"
        assert snapshot["task"]["in_flight_tool_calls"] == []
        assert snapshot["task"]["clarification"]["reason"] == "ambiguous"
        assert snapshot["task"]["clarification"]["ask_fields"] == ["time_window"]
        assert snapshot["task"]["missing_fields"] == ["time_window"]
        assert "接待时间" in snapshot["conversation"][-1]["text"]

    asyncio.run(scenario())


def test_no_silent_fixture_defaults_in_missing_or_partial_inputs() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_no_silent_fixture_defaults",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        result = await session.process_message("帮我规划一个接待客户流程。", action="start")
        payload = json.dumps(result["snapshot"], ensure_ascii=False)

        assert "Synthetic Central Office" not in payload
        assert "flexible" not in payload
        assert "2 days" not in payload

    asyncio.run(scenario())


def test_filling_missing_slots_keeps_v1_then_replacing_time_advances_to_v2() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_slot_completion_does_not_advance_plan",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        initial = await session.process_message("请帮忙规划一个接待午饭，选云南菜。", action="start")
        assert initial["snapshot"]["task"]["current_plan_version"] == 1
        assert initial["snapshot"]["task"]["lifecycle"] == "WAITING_FOR_SLOT"

        filled = await session.process_message(
            "8个人，地点在北京市海淀区中关村领展购物广场附近，人均200以内，有两位不吃辣，最好12点半。"
        )
        filled_snapshot = filled["snapshot"]
        assert filled["status"] == "tool_running"
        assert filled_snapshot["task"]["current_plan_version"] == 1
        assert not any(
            event["event_name"] == "PLAN_VERSION_ADVANCED"
            for event in filled_snapshot["timeline"]
        )
        interpretation = next(
            event
            for event in reversed(session.journal.events())
            if event["event_name"] == "USER_PATCH_INTERPRETED"
        )
        assert interpretation["materially_changes_task"] is False
        assert "previously_unresolved_slots_filled" in interpretation["interpretation_reason"]
        assert any(
            item["label"] == "用户补齐信息（不改 plan_version）"
            for item in filled_snapshot["task"]["evidence"]
        )

        changed = await session.process_message("中间我插一句，把时间修改到晚上吧。")
        changed_snapshot = changed["snapshot"]
        assert changed_snapshot["task"]["current_plan_version"] == 2
        assert any(
            event["event_name"] == "PLAN_VERSION_ADVANCED"
            for event in changed_snapshot["timeline"]
        )
        interpretation = next(
            event
            for event in reversed(session.journal.events())
            if event["event_name"] == "USER_PATCH_INTERPRETED"
        )
        assert interpretation["materially_changes_task"] is True
        assert "time_window" in interpretation["interpretation_reason"]
        plan_versions = changed_snapshot["task"]["plan_versions"]
        assert [(item["plan_version"], item["status"]) for item in plan_versions] == [
            (1, "superseded"),
            (2, "current"),
        ]
        assert plan_versions[-1]["reason"] == (
            "user_patch:established_slot_values_replaced:time_window"
        )
        assert plan_versions[-1]["summary"] == "中间我插一句，把时间修改到晚上吧。"

    asyncio.run(scenario())


def test_workbench_environment_defaults_to_local_codex_cli(monkeypatch) -> None:
    monkeypatch.delenv("VOICE_AGENT_WORKBENCH_CODEX_MODE", raising=False)
    monkeypatch.delenv("VOICE_AGENT_ALLOW_LOCAL_CODEX_CLI", raising=False)
    monkeypatch.delenv("VOICE_AGENT_CODEX_MODEL", raising=False)
    monkeypatch.delenv("VOICE_AGENT_CODEX_REASONING_EFFORT", raising=False)

    config = WorkbenchRuntimeConfig.from_environment()

    assert config.provider_mode == "codex_cli_local"
    assert config.allow_local_codex_cli is True
    assert config.model_name is None
    assert config.reasoning_effort == "medium"


def test_workbench_environment_allows_codex_model_override(monkeypatch) -> None:
    monkeypatch.setenv("VOICE_AGENT_CODEX_MODEL", "custom-local-model")
    monkeypatch.setenv("VOICE_AGENT_CODEX_REASONING_EFFORT", "medium")

    config = WorkbenchRuntimeConfig.from_environment()

    assert config.model_name == "custom-local-model"
    assert config.reasoning_effort == "medium"


def test_workbench_mapping_uses_cli_default_model_and_medium_reasoning() -> None:
    config = WorkbenchRuntimeConfig.from_mapping({})

    assert config.model_name is None
    assert config.reasoning_effort == "medium"


def test_local_codex_cli_passes_output_schema_as_short_lived_file(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, prompt: bytes) -> tuple[bytes, bytes]:
            command = captured["command"]
            assert isinstance(command, tuple)
            schema_path = Path(command[command.index("--output-schema") + 1])
            captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
            captured["prompt"] = prompt.decode("utf-8")
            return b'{"type":"turn.completed"}\n', b""

    async def fake_create_subprocess_exec(*command: str, **_kwargs: object) -> FakeProcess:
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(codex_slow_llm.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    stdout, stderr = asyncio.run(
        _run_codex_cli_async(
            CodexSlowLLMAdapterConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=True,
            ),
            "Return one structured proposal.",
        )
    )

    command = captured["command"]
    assert isinstance(command, tuple)
    schema_path = Path(command[command.index("--output-schema") + 1])
    assert captured["schema"]["type"] == "object"
    assert captured["schema"]["additionalProperties"] is False
    assert "schema_version" in captured["schema"]["required"]
    assert captured["prompt"] == "Return one structured proposal."
    assert not schema_path.exists()
    assert stdout == '{"type":"turn.completed"}\n'
    assert stderr == ""


def test_local_codex_cli_passes_model_and_reasoning_effort(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, prompt: bytes) -> tuple[bytes, bytes]:
            captured["prompt"] = prompt.decode("utf-8")
            return b'{"type":"turn.completed"}\n', b""

    async def fake_create_subprocess_exec(*command: str, **_kwargs: object) -> FakeProcess:
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(codex_slow_llm.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(
        _run_codex_cli_async(
            CodexSlowLLMAdapterConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=True,
                model_name="5.5",
                reasoning_effort="high",
            ),
            "Return one structured proposal.",
        )
    )

    command = captured["command"]
    assert isinstance(command, tuple)
    assert command[command.index("--model") + 1] == "5.5"
    assert ("-c", "model_reasoning_effort=high") in zip(command, command[1:])


def test_codex_jsonl_parser_extracts_item_completed_text() -> None:
    output = {
        "schema_version": "slow_llm_qwen_evidence_v1",
        "task_binding": {},
    }
    jsonl = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(output),
            },
        }
    )

    parsed = parse_codex_jsonl_output(jsonl)

    assert parsed.structured_output == output
    assert parsed.provider_event_count == 1


def test_local_codex_cli_reports_allowlisted_jsonl_progress_without_raw_text(monkeypatch) -> None:
    progress: list[dict[str, object]] = []

    class FakeStdin:
        def write(self, _value: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeStdout:
        def __init__(self, lines: list[bytes]) -> None:
            self._lines = iter(lines)

        async def readline(self) -> bytes:
            return next(self._lines, b"")

    class FakeStderr:
        async def read(self, _size: int) -> bytes:
            return b""

    class FakeProcess:
        returncode = 0

        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = FakeStdout(
                [
                    b'{"type":"thread.started"}\n',
                    b'{"type":"item.completed","item":{"text":"hidden provider text"}}\n',
                ]
            )
            self.stderr = FakeStderr()

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*_command: str, **_kwargs: object) -> FakeProcess:
        return FakeProcess()

    async def on_progress(item) -> None:
        progress.append(dict(item))

    monkeypatch.setattr(codex_slow_llm.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    stdout, stderr = asyncio.run(
        _run_codex_cli_async(
            CodexSlowLLMAdapterConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=True,
            ),
            "Return one structured proposal.",
            on_provider_event=on_progress,
        )
    )

    assert stdout.count("\n") == 2
    assert stderr == ""
    assert [item["kind"] for item in progress] == [
        "subprocess_startup",
        "thread.started",
        "item.completed",
    ]
    assert isinstance(progress[0]["latency_ms"], int)
    assert all("text" not in item for item in progress)


def test_session_snapshot_exposes_live_progress_while_provider_is_waiting() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_live_progress",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        original_invoke = session._role_adapter.invoke
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_invoke(**kwargs):
            started.set()
            await release.wait()
            return await original_invoke(**kwargs)

        session._role_adapter.invoke = slow_invoke
        task = asyncio.create_task(session.process_message("明天上午在公司附近规划两天客户行程", action="start"))
        await started.wait()

        try:
            live = await session.snapshot()
            assert live["streaming"]["active"] is True
            assert live["live_progress"]
            assert live["conversation"][-1]["speaker"] == "user"
            assert ":stream:" in live["snapshot_id"]
        finally:
            release.set()
        final = await task
        assert final["snapshot"]["streaming"]["active"] is False
        assert final["snapshot"]["live_progress"]

    asyncio.run(scenario())


def test_codex_proposal_projection_uses_validated_model_analysis() -> None:
    proposal = _proposal_from_structured_output(
        {
            "task_binding": {"adapter_request_id": "request_real_projection"},
            "task_analysis": {
                "summary": "根据用户的上午时间窗，建议先筛选靠近公司的候选地点。",
                "intent": "morning_itinerary_with_budget_constraint",
            },
            "tool_proposal": {"tool_name": "demo.itinerary.search"},
            "confirmation_risk_hints": ["只使用 synthetic demo sandbox。"],
            "known_fields": ["location_anchor", "time_window"],
            "missing_fields": [],
        },
        source_evidence_refs=("evidence://synthetic/user/request",),
        provider_mode="codex_cli_local",
        output_mode="real",
    )

    assert "上午时间窗" in proposal["summary"]
    assert any("上午时间窗" in step for step in proposal["suggested_next_steps"])
    assert not any("Codex intent classification" in step for step in proposal["suggested_next_steps"])
    assert any("synthetic demo sandbox" in note for note in proposal["risk_notes"])
    assert proposal["known_fields"] == ["location_anchor", "time_window"]


def test_codex_proposal_projection_keeps_model_gap_analysis_separate_from_state_hint() -> None:
    proposal = _proposal_from_structured_output(
        {
            "task_binding": {"adapter_request_id": "request_model_gap_analysis"},
            "task_analysis": {
                "summary": "我看到地点已经有了，但人数还没说清楚。",
                "intent": "clarify_model_detected_gap",
            },
            "tool_proposal": {"tool_name": "demo.itinerary.search"},
            "confirmation_risk_hints": [],
            "known_fields": ["location_anchor"],
            "missing_fields": ["party_size"],
        },
        source_evidence_refs=("evidence://synthetic/user/request",),
        provider_mode="codex_cli_local",
        output_mode="real",
        state_missing_fields=("time_window",),
    )

    assert proposal["known_fields"] == ["location_anchor"]
    assert proposal["missing_fields"] == ["party_size"]
    assert proposal["status"] == "degraded"
    assert proposal["backend_selected_ask_fields"] == ["time_window"]
    assert proposal["covered_fields"] == ["time_window"]
    assert any("后端当前状态" in note for note in proposal["risk_notes"])


def test_codex_clarification_contract_rejects_extra_ask_fields() -> None:
    proposal = _proposal_from_structured_output(
        {
            "task_binding": {"adapter_request_id": "request_extra_field"},
            "task_analysis": {
                "summary": "我还想问地点和预算。",
                "intent": "clarify_extra_field",
            },
            "tool_proposal": {"tool_name": "demo.itinerary.search"},
            "confirmation_risk_hints": [],
            "known_fields": [],
            "missing_fields": ["location_anchor", "budget"],
        },
        source_evidence_refs=("evidence://synthetic/user/request",),
        provider_mode="codex_cli_local",
        output_mode="real",
        state_missing_fields=("location_anchor",),
    )

    assert proposal["status"] == "degraded"
    assert proposal["backend_selected_ask_fields"] == ["location_anchor"]
    assert proposal["covered_fields"] == ["location_anchor"]
    assert proposal["diagnostic_model_missing_fields"] == ["location_anchor", "budget"]


def test_material_patch_advances_plan_and_late_tool_result_is_stale() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_dynamic_patch",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        await session.process_message("明天上午在公司附近规划两天客户行程，6个人", action="start")
        result = await session.process_message(
            "改成明天下午，预算不超过 500 元。",
            action="material_patch",
        )
        snapshot = result["snapshot"]

        assert result["status"] == "plan_advanced_stale_result"
        assert snapshot["task"]["current_plan_version"] == 2
        assert snapshot["task"]["lifecycle"] == "EXECUTING"
        assert snapshot["task"]["in_flight_tool_calls"]
        assert snapshot["task"]["stale_evidence"]
        assert any(event["event_name"] == "PLAN_VERSION_ADVANCED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "TOOL_RESULT_RECEIVED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "TOOL_RESULT_MARKED_STALE" for event in snapshot["timeline"])
        assert any(event["event_name"] == "STALE_EVIDENCE_RECORDED" for event in snapshot["timeline"])
        assert snapshot["replay"]["replay_reruns_provider"] is False
        assert snapshot["replay"]["replay_reruns_tool"] is False

        again = await session.snapshot()
        assert again["context_hash"] == snapshot["context_hash"]
        assert again["replay"]["state_digest"] == snapshot["replay"]["state_digest"]

    asyncio.run(scenario())


def test_final_plan_request_does_not_become_a_material_patch() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_final_plan_control_intent",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        await session.process_message(
            "请规划 8 人客户晚餐，选云南菜；地点在北京市海淀区中关村领展购物广场附近，晚上 18 点半，人均 200 元以内，两位不吃辣。",
            action="start",
        )
        changed = await session.process_message("改到晚上 19 点。")
        assert changed["snapshot"]["task"]["current_plan_version"] == 2

        final = await session.process_message("信息够了，请输出最终规划。")
        snapshot = final["snapshot"]

        assert final["status"] == "completed"
        assert snapshot["task"]["current_plan_version"] == 2
        assert snapshot["task"]["lifecycle"] == "COMPLETED"
        assert len(
            [event for event in snapshot["timeline"] if event["event_name"] == "PLAN_VERSION_ADVANCED"]
        ) == 1
        assert "系统没有执行预订、支付、消息发送或其他真实外部写操作" in snapshot["conversation"][-1]["text"]

    asyncio.run(scenario())


def test_local_workbench_auto_publishes_a_mutable_current_plan_from_place_search_evidence() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_external_place_search",
            config=WorkbenchRuntimeConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=False,
            ),
        )
        session._role_adapter._provider_mode = "fake"

        class StubPlaceSearch:
            def search(self, *, query: str) -> PlaceSearchEvidence:
                assert "中关村" in query
                return PlaceSearchEvidence(
                    query=query,
                    provider="stub_external_read",
                    results=(
                        {
                            "source_title": "可验证的云南菜候选",
                            "source_url": "https://example.test/place/yunnan",
                            "snippet_or_summary": "地址与营业信息需联系门店复核。",
                        },
                    ),
                )

        session._place_search_adapter = StubPlaceSearch()
        result = await session.process_message(
            "请规划 8 人客户晚餐，选云南菜；地点在北京市海淀区中关村领展购物广场附近，晚上 18 点半，人均 200 元以内，两位不吃辣。",
            action="start",
        )
        assert result["status"] == "tool_running"
        assert result["snapshot"]["task"]["lifecycle"] == "EXECUTING"
        await _wait_for_lifecycle(session, "PLANNING")
        snapshot = await _wait_for_planner(session)

        assert snapshot["task"]["lifecycle"] == "PLANNING"
        assert any(call["tool_name"] == "webSearch" for call in snapshot["task"]["tool_calls"])
        assert "可验证的云南菜候选" in snapshot["conversation"][-1]["text"]
        assert "云海肴" not in snapshot["conversation"][-1]["text"]
        assert any(item["source"] == "web_search" for item in snapshot["task"]["evidence"])
        assert [
            item["role"] for item in snapshot["provider_trace"] if item.get("role")
        ][-1:] == ["PLANNER"]
        assert snapshot["task"]["reviewer_status"] is None
        assert not any(event["event_name"] == "SEMANTIC_COMMITMENT_EMITTED" for event in snapshot["timeline"])

    asyncio.run(scenario())


def test_background_search_does_not_hold_session_lock_during_material_patch() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_background_search_patch_lock",
            config=WorkbenchRuntimeConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=False,
            ),
        )
        session._role_adapter._provider_mode = "fake"
        search_started = threading.Event()
        release_search = threading.Event()

        class BlockingPlaceSearch:
            def search(self, *, query: str) -> PlaceSearchEvidence:
                search_started.set()
                release_search.wait(timeout=5)
                return PlaceSearchEvidence(
                    query=query,
                    provider="synthetic_blocking_read",
                    results=(),
                    degraded_reason="synthetic_no_results",
                )

        session._place_search_adapter = BlockingPlaceSearch()
        first = await session.process_message(
            "请规划 8 人客户午餐，选云南菜；地点在北京市海淀区中关村附近，12 点半，人均 200 元以内，没有忌口。",
            action="start",
        )
        assert first["status"] == "tool_running"
        for _ in range(100):
            if search_started.is_set():
                break
            await asyncio.sleep(0)
        assert search_started.is_set()

        patch_started = time.perf_counter()
        revised = await asyncio.wait_for(
            session.process_message("把时间修改到晚上吧。"),
            timeout=1,
        )
        patch_latency_ms = (time.perf_counter() - patch_started) * 1000
        assert patch_latency_ms < 1000
        assert revised["snapshot"]["task"]["current_plan_version"] == 2
        assert "已把时间调整到晚上，其他条件保持不变" in revised["snapshot"]["conversation"][-1]["text"]
        assert any(
            event["event_name"] == "TOOL_RESULT_MARKED_STALE"
            for event in revised["snapshot"]["timeline"]
        )

        release_search.set()
        await _wait_for_lifecycle(session, "PLANNING")

    asyncio.run(scenario())


def test_reset_cancels_tracked_background_search_without_late_state_write() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_background_search_reset",
            config=WorkbenchRuntimeConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=False,
            ),
        )
        session._role_adapter._provider_mode = "fake"
        search_started = threading.Event()
        release_search = threading.Event()

        class BlockingPlaceSearch:
            def search(self, *, query: str) -> PlaceSearchEvidence:
                search_started.set()
                release_search.wait(timeout=5)
                return PlaceSearchEvidence(query=query, provider="synthetic", results=())

        session._place_search_adapter = BlockingPlaceSearch()
        await session.process_message(
            "请规划 8 人客户午餐，选云南菜；地点在中关村附近，12 点半，人均 200 元以内，没有忌口。",
            action="start",
        )
        for _ in range(100):
            if search_started.is_set():
                break
            await asyncio.sleep(0)
        assert search_started.is_set()

        reset_snapshot = await asyncio.wait_for(session.reset(), timeout=1)
        assert reset_snapshot["task"] is None
        release_search.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert (await session.snapshot())["task"] is None

    asyncio.run(scenario())


def test_background_search_exception_becomes_partial_plan_and_is_consumed() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_background_search_exception",
            config=WorkbenchRuntimeConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=False,
            ),
        )
        session._role_adapter._provider_mode = "fake"

        class FailingPlaceSearch:
            def search(self, *, query: str) -> PlaceSearchEvidence:
                raise RuntimeError("synthetic sensitive provider body")

        session._place_search_adapter = FailingPlaceSearch()
        result = await session.process_message(
            "请规划 8 人客户午餐，选云南菜；地点在中关村附近，12 点半，人均 200 元以内，没有忌口。",
            action="start",
        )
        assert result["status"] == "tool_running"
        snapshot = await _wait_for_lifecycle(session, "PLANNING")
        await asyncio.sleep(0)
        assert session._background_failures == ["RuntimeError"]
        assert not session._background_tasks
        assert "synthetic sensitive provider body" not in json.dumps(snapshot, ensure_ascii=False)
        assert "没有返回可引用候选" in snapshot["conversation"][-1]["text"]
        assert "provider_unavailable" in snapshot["conversation"][-1]["text"]

    asyncio.run(scenario())


def test_completed_plan_recap_is_safe_and_does_not_become_active_task_patch() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_completed_plan_recap",
            config=WorkbenchRuntimeConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=False,
            ),
        )
        session._role_adapter._provider_mode = "fake"

        class StubPlaceSearch:
            def search(self, *, query: str) -> PlaceSearchEvidence:
                return PlaceSearchEvidence(
                    query=query,
                    provider="stub_external_read",
                    results=(
                        {
                            "source_title": "可重新展示的候选",
                            "source_url": "https://example.test/place/replay",
                            "snippet_or_summary": "来自当前计划的已提交证据。",
                        },
                    ),
                )

        session._place_search_adapter = StubPlaceSearch()
        first = await session.process_message(
            "请规划 8 人客户晚餐，选云南菜；地点在北京市海淀区中关村附近，晚上 18 点半，人均 200 元以内，没有忌口。",
            action="start",
        )
        assert first["status"] == "tool_running"
        await _wait_for_lifecycle(session, "PLANNING")
        planned = await _wait_for_planner(session)
        trace_cursor_before_commit = len(planned["provider_trace"])
        committed = await session.process_message("信息够了，请输出最终规划。")
        assert committed["status"] == "completed"
        assert committed["snapshot"]["task"]["lifecycle"] == "COMPLETED"
        commit_roles = [
            item["role"]
            for item in committed["snapshot"]["provider_trace"][trace_cursor_before_commit:]
            if item.get("role")
        ]
        assert commit_roles == ["REVIEWER"]
        final = await session.process_message("信息够了，请输出最终规划。")

        assert final["status"] == "completed_plan_recap"
        assert final["snapshot"]["task"]["lifecycle"] == "COMPLETED"
        assert final["snapshot"]["task"]["current_plan_version"] == 1
        assert "可重新展示的候选" in final["snapshot"]["conversation"][-1]["text"]
        assert "不会新建任务或改变 plan_version" in final["snapshot"]["conversation"][-1]["text"]

    asyncio.run(scenario())


def test_revision_after_auto_published_plan_advances_same_task_version() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_completed_plan_revision",
            config=WorkbenchRuntimeConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=False,
            ),
        )
        session._role_adapter._provider_mode = "fake"

        class StubPlaceSearch:
            def search(self, *, query: str) -> PlaceSearchEvidence:
                return PlaceSearchEvidence(
                    query=query,
                    provider="stub_external_read",
                    results=(
                        {
                            "source_title": "修订后的候选",
                            "source_url": "https://example.test/place/revised",
                            "snippet_or_summary": "来自当前修订任务。",
                        },
                    ),
                )

        session._place_search_adapter = StubPlaceSearch()
        original = await session.process_message(
            "请规划 8 人客户午餐，选云南菜；地点在北京市海淀区中关村附近，12 点半，人均 200 元以内，没有忌口。",
            action="start",
        )
        original_task_id = original["snapshot"]["task"]["task_id"]
        await _wait_for_lifecycle(session, "PLANNING")
        revised = await session.process_message("把时间修改到晚上吧。")
        revised_reply = revised["snapshot"]["conversation"][-1]["text"]

        assert revised["status"] == "tool_running"
        assert revised["snapshot"]["task"]["task_id"] == original_task_id
        assert revised["snapshot"]["task"]["current_plan_version"] == 2
        assert revised["snapshot"]["task"]["lifecycle"] == "EXECUTING"
        assert any(
            event["event_name"] == "PLAN_VERSION_ADVANCED"
            for event in revised["snapshot"]["timeline"]
        )
        assert "正在" in revised_reply
        ready_snapshot = await _wait_for_lifecycle(session, "PLANNING")
        assert ready_snapshot["task"]["current_plan_version"] == 2
        assert "当前可修改方案" in ready_snapshot["conversation"][-1]["text"]

    asyncio.run(scenario())


def test_missing_slot_reply_names_each_required_user_input() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_specific_slot_prompt",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        result = await session.process_message("请规划云南菜客户接待。", action="start")

        reply = result["snapshot"]["conversation"][-1]["text"]
        proposal_summary = result["snapshot"]["codex_proposals"][-1]["summary"]
        # The Workbench displays the validated Codex realization candidate;
        # SlowTask owns the missing_fields state, not the wording itself.
        assert reply == proposal_summary
        assert "接待时间" in reply
        assert "位置范围" in reply
        assert "参与人数" in reply
        assert "explicit_user_confirmation" not in reply

    asyncio.run(scenario())


def test_workbench_displays_validated_codex_realization_for_clarification_and_progress() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_codex_user_visible_realization",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        missing = await session.process_message("请帮忙规划一个接待午饭，选云南菜。", action="start")
        assert missing["status"] == "waiting_for_slot"
        assert missing["snapshot"]["conversation"][-1]["text"] == (
            "请补充位置范围、参与人数。"
        )

        ready = await session.process_message("地点在北京中关村附近，明天中午 12 点，6人，人均200以内，没有忌口。")
        assert ready["status"] == "tool_running"
        assert ready["snapshot"]["conversation"][-1]["text"] == (
            "已保留全部已确认条件；Python 已确定性编译并校验只读查询，正在筛选地点候选。"
        )

    asyncio.run(scenario())


def test_cancel_without_an_active_task_is_a_truthful_fast_reply_not_a_400_path() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_cancel_without_active_task",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        result = await session.process_message("在最终回答之前取消规划吧。")

        assert result["status"] == "no_active_task_to_cancel"
        assert result["snapshot"]["router"]["router_decision"] == "FAST_ONLY"
        assert result["snapshot"]["task"] is None
        assert "没有正在执行的规划可取消" in result["snapshot"]["conversation"][-1]["text"]
        assert not any(
            event["event_name"] == "CONFIRMATION_REQUIRED"
            for event in result["snapshot"]["timeline"]
        )

    asyncio.run(scenario())


def test_cancel_requires_current_confirmation_then_clears_focus() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_dynamic_cancel",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        await session.process_message("明天上午在公司附近规划两天客户行程，6个人", action="start")
        candidate = await session.process_message("取消这个任务", action="cancel_candidate")
        pending = candidate["snapshot"]["task"]["pending_confirmation"]

        assert candidate["status"] == "confirmation_required"
        assert candidate["snapshot"]["task"]["lifecycle"] == "WAITING_FOR_USER_CONFIRMATION"
        assert pending["scope"] == "TASK_CANCEL"

        confirmed = await session.confirm(pending["confirmation_id"], accepted=True)
        snapshot = confirmed["snapshot"]
        assert confirmed["status"] == "cancelled"
        assert snapshot["task"]["lifecycle"] == "CANCELLED"
        assert snapshot["task"]["terminal_outcome"] == "CANCELLED"
        assert snapshot["task"]["pending_confirmation"] is None
        assert snapshot["task"]["in_flight_tool_calls"] == []
        assert snapshot["router"]["active_task_id"] is None
        assert any(event["event_name"] == "CONFIRMATION_ACCEPTED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "SLOWTASK_CANCELLED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "TOOL_EXECUTION_CANCELLED" for event in snapshot["timeline"])

    asyncio.run(scenario())


def test_api_manager_keeps_sessions_python_owned_and_resettable() -> None:
    async def scenario() -> None:
        api = WorkbenchApi(
            manager=WorkbenchSessionManager(
                default_config=WorkbenchRuntimeConfig(provider_mode="fake"),
            )
        )
        created = await api.dispatch("create_session", {"session_id": "test_api_workbench"})
        assert created["ok"] is True
        started = await api.dispatch(
            "message",
            {
                "session_id": "test_api_workbench",
                "text": "明天上午在公司附近规划两天客户行程",
                "action": "start",
            },
        )
        assert started["ok"] is True
        assert started["snapshot"]["task"]["current_plan_version"] == 1
        assert started["proposal"]["safety"]["codex_is_fact_owner"] is False
        reset = await api.dispatch("reset", {"session_id": "test_api_workbench"})
        assert reset["ok"] is True
        assert reset["snapshot"]["task"] is None
        assert reset["snapshot"]["replay"]["event_count"] == 2

    asyncio.run(scenario())
