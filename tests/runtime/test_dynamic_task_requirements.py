from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from voice_agent.adapters.codex_roles import CodexRole
from voice_agent.runtime.slow_system_workbench_context import TaskContextPackBuilder
from voice_agent.runtime.slow_system_workbench_api import WorkbenchApi
from voice_agent.runtime.slow_system_workbench_sessions import (
    WorkbenchRuntimeConfig,
    WorkbenchSession,
    _reviewer_block_user_summary,
)
from voice_agent.runtime.slow_system_workbench_sessions import WorkbenchSessionManager
from voice_agent.adapters.workbench_place_search import PlaceSearchEvidence
from voice_agent.slowtask.task_profiles import builtin_task_profile_registry
from voice_agent.state.slowtask_state import SlowTaskState


def _session(name: str) -> WorkbenchSession:
    return WorkbenchSession(session_id=name, config=WorkbenchRuntimeConfig(provider_mode="fake"))


def _roles(snapshot: dict[str, object]) -> list[str]:
    return [item["role"] for item in snapshot["provider_trace"] if item.get("role")]


def _requirements(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        item["requirement_id"]: item
        for item in snapshot["task"]["requirement_summary"]
    }


def test_degraded_provider_two_turn_customer_lunch_keeps_current_evidence_and_reply() -> None:
    async def provider_failure(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic_provider_failure")

    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="dynamic_degraded_customer_lunch",
            config=WorkbenchRuntimeConfig(provider_mode="codex_cli_local"),
        )
        session._role_adapter._provider_invoker = provider_failure

        class StubPlaceSearch:
            def search(self, *, query: str) -> PlaceSearchEvidence:
                return PlaceSearchEvidence(
                    query=query,
                    provider="synthetic_read_only",
                    results=(),
                    degraded_reason="synthetic_no_results",
                )

        session._place_search_adapter = StubPlaceSearch()

        first = await session.process_message(
            "请帮忙规划一个接待午饭，选云南菜。",
            action="start",
        )
        first_snapshot = first["snapshot"]
        first_task = first_snapshot["task"]
        first_trace = [item for item in first_snapshot["provider_trace"] if item.get("role")]

        second = await session.process_message(
            "8个人，地点在北京市海淀区中关村领展购物广场附近，人均200以内，有两位不吃辣，最好12点半。"
        )
        second_snapshot = second["snapshot"]
        second_task = second_snapshot["task"]
        second_trace = [item for item in second_snapshot["provider_trace"] if item.get("role")]
        second_turn_trace = second_trace[len(first_trace):]
        requirements = _requirements(second_snapshot)
        assistant_turn = second_snapshot["conversation"][-1]

        assert first_task["task_kind"] == "customer_reception"
        assert first_task["task_model_status"] == "DEGRADED"
        assert first_task["task_model_confidence"] == "low"
        assert "cuisine_preference" in _requirements(first_snapshot)
        assert _requirements(first_snapshot)["cuisine_preference"]["status"] == "RESOLVED"

        assert second_task["task_id"] == first_task["task_id"]
        assert second_task["current_plan_version"] == 1
        assert second_task["task_requirement_model_version"] >= first_task["task_requirement_model_version"]
        assert second_task["task_kind"] == "customer_reception"
        assert {
            "party_size", "location_anchor", "budget", "dietary_constraints",
            "time_window", "cuisine_preference",
        } <= {key for key, value in requirements.items() if value["status"] == "RESOLVED"}
        assert not {
            "party_size", "location_anchor", "budget", "dietary_constraints",
            "time_window", "cuisine_preference", "desired_deliverable", "key_constraint",
        } & set(second_task["selected_clarification_requirement_ids"])

        assert [item["role"] for item in first_trace] == [
            "TASK_MODELER", "REQUIREMENT_ANALYST", "CLARIFIER"
        ]
        assert [item["role"] for item in second_turn_trace] == [
            "TASK_MODELER", "REQUIREMENT_ANALYST", "PLANNER", "REVIEWER"
        ]
        assert all(item["context_hash"] != first_trace[-1]["context_hash"] for item in second_turn_trace)
        assert all(item["proposal_id"] != first_trace[-1]["proposal_id"] for item in second_turn_trace)
        assert all(item["validation_status"] == "degraded" for item in second_turn_trace)
        assert all(item["degraded_reason"] == "role_provider_failed" for item in second_turn_trace)

        planner_trace = second_turn_trace[-2]
        assert assistant_turn["source_role"] == "PLANNER"
        assert assistant_turn["proposal_id"] == planner_trace["proposal_id"]
        assert assistant_turn["context_hash"] == planner_trace["context_hash"]
        assert "最终产出" not in assistant_turn["text"]
        assert "最重要约束" not in assistant_turn["text"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "user_request",
    (
        "请帮忙规划一个接待午饭，选云南菜。",
        "帮我安排一次客户午餐，想吃云南菜。",
        "要接待几位客人，中午一起吃饭。",
        "准备一顿商务午餐。",
        "规划一个午餐接待流程。",
    ),
)
def test_customer_lunch_semantic_variants_do_not_use_generic_bootstrap(user_request: str) -> None:
    async def scenario() -> None:
        session = _session("dynamic_lunch_variant_" + str(abs(hash(user_request))))
        result = await session.process_message(user_request, action="start")

        assert result["snapshot"]["task"]["task_kind"] == "customer_reception"
        assert result["snapshot"]["task"]["task_model_status"] == "ACCEPTED_SPECIFIC"
        assert "desired_deliverable" not in _requirements(result["snapshot"])
        assert "key_constraint" not in _requirements(result["snapshot"])

    asyncio.run(scenario())


def test_provisional_bootstrap_is_boundedly_upgraded_without_plan_advance() -> None:
    task_modeler_calls = 0
    profile_payload = builtin_task_profile_registry().get("customer_reception").model_payload

    async def recovering_provider(
        role: CodexRole,
        _context: dict[str, object],
        _schema: dict[str, object],
    ) -> dict[str, object]:
        nonlocal task_modeler_calls
        if role is CodexRole.TASK_MODELER:
            task_modeler_calls += 1
            if task_modeler_calls == 1:
                raise RuntimeError("synthetic_first_modeler_failure")
            payload = deepcopy(profile_payload)
            payload["candidate_tool_capabilities"] = ["webSearch"]
            payload["open_modeling_questions"] = []
            payload["confidence"] = "high"
            return {"public_summary": "specific model candidate", "payload": payload}
        raise RuntimeError("synthetic_non_modeler_failure")

    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="dynamic_bootstrap_upgrade",
            config=WorkbenchRuntimeConfig(provider_mode="codex_cli_local"),
        )
        session._role_adapter._provider_invoker = recovering_provider
        first = await session.process_message("帮我规划一下。", action="start")
        old_ref = first["snapshot"]["task"]["task_requirement_model_ref"]

        assert first["snapshot"]["task"]["task_kind"] == "generic_bootstrap"
        assert first["snapshot"]["task"]["task_model_status"] == "PROVISIONAL_BOOTSTRAP"
        assert first["snapshot"]["task"]["task_model_needs_remodeling"] is True

        second = await session.process_message(
            "是给8位客户安排一顿午饭，地点在中关村，想吃云南菜。"
        )
        task = second["snapshot"]["task"]
        names = [event["event_name"] for event in session.journal.events()]

        assert task_modeler_calls == 2
        assert task["task_kind"] == "customer_reception"
        assert task["task_model_status"] == "ACCEPTED_SPECIFIC"
        assert task["task_model_needs_remodeling"] is False
        assert task["task_requirement_model_ref"] != old_ref
        assert task["task_requirement_model_version"] == 2
        assert task["current_plan_version"] == 1
        assert names.count("TASK_REQUIREMENT_MODEL_INVALIDATED") == 1
        assert names.count("TASK_REQUIREMENT_MODEL_ACCEPTED") == 2
        assert "最终产出" not in second["snapshot"]["conversation"][-1]["text"]
        assert len(session._remodel_context_hashes) == 1

    asyncio.run(scenario())


def test_model_validation_failure_remodeling_keeps_original_goal_context() -> None:
    invalid_payload = deepcopy(
        builtin_task_profile_registry().get("customer_reception").model_payload
    )
    invalid_payload["success_criteria"] = ["x" * 121]
    invalid_payload["candidate_tool_capabilities"] = ["webSearch"]
    invalid_payload["open_modeling_questions"] = []
    invalid_payload["confidence"] = "high"

    async def invalid_model_provider(
        role: CodexRole,
        _context: dict[str, object],
        _schema: dict[str, object],
    ) -> dict[str, object]:
        if role is CodexRole.TASK_MODELER:
            return {"public_summary": "invalid specific model", "payload": invalid_payload}
        raise RuntimeError("synthetic_non_modeler_failure")

    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="dynamic_validation_failure_context",
            config=WorkbenchRuntimeConfig(provider_mode="codex_cli_local"),
        )
        session._role_adapter._provider_invoker = invalid_model_provider
        await session.process_message(
            "请帮忙规划一个接待午饭，选云南菜。",
            action="start",
        )
        second = await session.process_message(
            "8个人，地点在北京市海淀区中关村领展购物广场附近，人均200以内，有两位不吃辣，最好12点半。"
        )
        task = second["snapshot"]["task"]
        modeler_trace = [
            item for item in second["snapshot"]["provider_trace"] if item.get("role") == "TASK_MODELER"
        ]

        assert task["task_kind"] == "customer_reception"
        assert task["task_requirement_model_version"] == 2
        assert task["current_plan_version"] == 1
        assert all(item["degraded_reason"] == "role_output_validation_failed" for item in modeler_trace)
        assert not {"desired_deliverable", "key_constraint"} & set(_requirements(second["snapshot"]))
        assert "最终产出" not in second["snapshot"]["conversation"][-1]["text"]
        assert "最重要约束" not in second["snapshot"]["conversation"][-1]["text"]

    asyncio.run(scenario())


def test_clarifier_uses_current_turn_proposal_context_and_selected_requirements() -> None:
    async def scenario() -> None:
        session = _session("dynamic_clarifier_freshness")
        first = await session.process_message("帮我规划一个接待客户流程。", action="start")
        first_turn = first["snapshot"]["conversation"][-1]
        first_selected = first["snapshot"]["task"]["selected_clarification_requirement_ids"]

        second = await session.process_message("时间定在明天中午。")
        second_turn = second["snapshot"]["conversation"][-1]
        second_selected = second["snapshot"]["task"]["selected_clarification_requirement_ids"]
        clarification_trace = [
            item for item in second["snapshot"]["provider_trace"] if item.get("role") == "CLARIFIER"
        ]

        assert first_selected == ["time_window", "location_anchor", "party_size"]
        assert second_selected == ["location_anchor", "party_size"]
        assert first_turn["proposal_id"] != second_turn["proposal_id"]
        assert first_turn["context_hash"] != second_turn["context_hash"]
        assert second_turn["proposal_id"] == clarification_trace[-1]["proposal_id"]
        assert second_turn["context_hash"] == clarification_trace[-1]["context_hash"]
        assert "接待时间" not in second_turn["text"]
        assert "位置范围" in second_turn["text"]
        assert "参与人数" in second_turn["text"]

    asyncio.run(scenario())


def test_two_turn_customer_lunch_replay_restores_model_values_roles_and_plan() -> None:
    async def scenario() -> None:
        session = _session("dynamic_customer_lunch_replay")
        await session.process_message("请帮忙规划一个接待午饭，选云南菜。", action="start")
        result = await session.process_message(
            "8个人，地点在北京市海淀区中关村领展购物广场附近，人均200以内，有两位不吃辣，最好12点半。"
        )
        original = result["snapshot"]
        invocation_count = len(_roles(original))

        replayed_state = SlowTaskState()
        for event in session.journal.events():
            replayed_state.reduce_event(event)
        replayed_task = replayed_state.tasks[original["task"]["task_id"]]
        _, focus_state, tool_state = session._projections()
        replayed_pack = TaskContextPackBuilder().build(
            slowtask_state=replayed_state,
            task_focus_state=focus_state,
            tool_execution_state=tool_state,
            evidence_catalog=session._evidence_catalog,
            conversation=(),
            latest_user_input=None,
            role_state=session._replayed_role_state(replayed_task),
        )
        replayed_requirements = {
            item["requirement_id"]: item for item in replayed_pack.requirement_summary
        }

        assert replayed_task.task_kind == "customer_reception"
        assert replayed_task.task_requirement_model_version == 1
        assert replayed_task.current_plan_version == 1
        assert replayed_requirements["cuisine_preference"]["value_preview"] == "云南菜"
        assert replayed_requirements["party_size"]["value_preview"] == "8"
        assert replayed_requirements["budget"]["value_preview"] == "200"
        assert replayed_requirements["dietary_constraints"]["status"] == "RESOLVED"
        assert replayed_requirements["location_anchor"]["status"] == "RESOLVED"
        assert replayed_requirements["time_window"]["value_preview"] == "12:30"
        assert replayed_pack.prior_role_proposal_refs == tuple(
            original["task"]["prior_role_proposal_refs"]
        )
        assert replayed_pack.clarification is None
        assert len(_roles(await session.snapshot())) == invocation_count

    asyncio.run(scenario())


def test_user_visible_reply_uses_clarifier_not_debug_public_summaries() -> None:
    async def scenario() -> None:
        session = _session("dynamic_visible_role")
        result = await session.process_message("帮我规划一个接待客户流程。", action="start")
        trace = [item for item in result["snapshot"]["provider_trace"] if item.get("role")]
        assistant_turn = result["snapshot"]["conversation"][-1]

        assert trace[0]["role"] == "TASK_MODELER"
        assert trace[1]["role"] == "REQUIREMENT_ANALYST"
        assert trace[2]["role"] == "CLARIFIER"
        assert assistant_turn["source_role"] == "CLARIFIER"
        assert assistant_turn["proposal_id"] == trace[2]["proposal_id"]
        assert assistant_turn["text"] == trace[2]["public_summary"]
        assert assistant_turn["text"] != trace[0]["public_summary"]
        assert assistant_turn["text"] != trace[1]["public_summary"]

    asyncio.run(scenario())


def test_reviewer_block_uses_bounded_user_summary_not_provider_debug_text() -> None:
    provider_summary = "Plan review completed: BLOCK due to invalid internal details."
    payload = {
        "verdict": "BLOCK",
        "missing_requirement_coverage": ["location_anchor"],
        "stale_evidence_usage": [],
        "tool_binding_errors": [],
        "premature_commitment": False,
    }

    user_summary = _reviewer_block_user_summary(payload)

    assert user_summary == "方案尚未覆盖已确认的必要条件。我已保留你确认的条件，但当前不会提交或执行这个方案。"
    assert provider_summary not in user_summary
    assert "最终产出" not in user_summary
    assert "最重要约束" not in user_summary


def test_http_api_current_turn_response_cannot_reference_old_proposal_or_context() -> None:
    async def scenario() -> None:
        api = WorkbenchApi(
            manager=WorkbenchSessionManager(
                default_config=WorkbenchRuntimeConfig(provider_mode="fake"),
            )
        )
        await api.dispatch(
            "create_session",
            {"session_id": "dynamic_api_current_turn", "provider_mode": "fake"},
        )
        first = await api.dispatch(
            "message",
            {
                "session_id": "dynamic_api_current_turn",
                "text": "帮我规划一个接待客户流程。",
                "action": "start",
            },
        )
        second = await api.dispatch(
            "message",
            {
                "session_id": "dynamic_api_current_turn",
                "text": "时间定在明天中午。",
            },
        )
        first_reply = first["snapshot"]["conversation"][-1]
        second_reply = second["snapshot"]["conversation"][-1]

        assert second["ok"] is True
        assert second_reply["proposal_id"] != first_reply["proposal_id"]
        assert second_reply["context_hash"] != first_reply["context_hash"]
        assert second_reply["proposal_id"] == second["proposal"]["proposal_id"]
        assert second_reply["source_role"] == "CLARIFIER"
        assert second_reply["text"] == second["proposal"]["summary"]
        assert "接待时间" not in second_reply["text"]

    asyncio.run(scenario())


def test_customer_reception_uses_modeler_analyst_and_selected_user_clarification() -> None:
    async def scenario() -> None:
        session = _session("dynamic_customer_reception")
        result = await session.process_message("帮我规划一个接待客户的流程。", action="start")
        snapshot = result["snapshot"]

        assert result["status"] == "waiting_for_slot"
        assert snapshot["task"]["task_kind"] == "customer_reception"
        assert snapshot["task"]["selected_clarification_requirement_ids"] == [
            "time_window", "location_anchor", "party_size"
        ]
        assert _roles(snapshot) == ["TASK_MODELER", "REQUIREMENT_ANALYST", "CLARIFIER"]
        assert snapshot["task"]["in_flight_tool_calls"] == []
        assert snapshot["task"]["semantic_commitment"]["status"] == "not_emitted"

    asyncio.run(scenario())


def test_research_report_has_research_requirements_and_never_uses_itinerary() -> None:
    async def scenario() -> None:
        session = _session("dynamic_research_report")
        result = await session.process_message(
            "帮我规划一篇关于 proactive agent 的调研报告。", action="start"
        )
        snapshot = result["snapshot"]
        requirements = _requirements(snapshot)

        assert snapshot["task"]["task_kind"] == "research_report"
        assert {"research_scope", "target_audience", "output_format", "deadline"} <= set(requirements)
        assert "location_anchor" not in requirements
        assert "party_size" not in requirements
        assert snapshot["task"]["selected_clarification_requirement_ids"] == ["target_audience"]
        assert all(
            call["tool_name"] != "demo.itinerary.search"
            for call in snapshot["task"]["tool_calls"]
        )

    asyncio.run(scenario())


def test_code_refactor_reaches_reviewed_no_tool_plan() -> None:
    async def scenario() -> None:
        session = _session("dynamic_code_refactor")
        first = await session.process_message(
            "帮我给 voice-agent 设计一个代码重构计划。", action="start"
        )
        requirements = _requirements(first["snapshot"])
        assert {"refactor_scope", "refactor_objectives", "compatibility_constraints", "acceptance_criteria"} == set(requirements)
        assert not {"party_size", "dietary_constraints", "location_anchor"} & set(requirements)

        result = await session.process_message(
            "目标是降低模块耦合，必须保持现有 API 兼容，验收标准是全部测试通过。"
        )
        snapshot = result["snapshot"]

        assert result["status"] == "plan_ready"
        assert snapshot["task"]["planner_mode"] == "FINAL_PLAN_CANDIDATE"
        assert snapshot["task"]["reviewer_status"] == "PASS"
        assert snapshot["task"]["tool_calls"] == []
        assert _roles(snapshot)[-3:] == ["REQUIREMENT_ANALYST", "PLANNER", "REVIEWER"]

    asyncio.run(scenario())


def test_team_event_keeps_known_party_size_and_uses_distinct_model() -> None:
    async def scenario() -> None:
        session = _session("dynamic_team_event")
        result = await session.process_message("帮我规划一次二十人的团队团建。", action="start")
        snapshot = result["snapshot"]
        requirements = _requirements(snapshot)

        assert snapshot["task"]["task_kind"] == "team_event"
        assert requirements["party_size"]["status"] == "RESOLVED"
        assert "party_size" not in snapshot["task"]["selected_clarification_requirement_ids"]
        assert snapshot["task"]["selected_clarification_requirement_ids"] == ["event_date"]
        assert requirements["venue_options"]["source_route"] == "TOOL"

    asyncio.run(scenario())


def test_tool_gap_is_planned_for_tool_executor_and_never_asked_by_clarifier() -> None:
    async def scenario() -> None:
        session = _session("dynamic_user_tool_routing")
        await session.process_message("帮我规划一个接待客户流程。", action="start")
        result = await session.process_message("下周二下午，在中关村，6个人。")
        snapshot = result["snapshot"]

        assert result["status"] == "tool_running"
        assert snapshot["task"]["selected_clarification_requirement_ids"] == []
        assert _requirements(snapshot)["venue_options"]["source_route"] == "TOOL"
        assert snapshot["task"]["planner_mode"] == "INFORMATION_GATHERING"
        assert snapshot["task"]["in_flight_tool_calls"][0]["tool_name"] == "webSearch"
        assert _roles(snapshot)[-3:] == ["REQUIREMENT_ANALYST", "PLANNER", "REVIEWER"]

    asyncio.run(scenario())


def test_goal_rewrite_invalidates_old_model_advances_plan_and_models_new_task() -> None:
    async def scenario() -> None:
        session = _session("dynamic_goal_rewrite")
        first = await session.process_message("帮我规划一个接待客户流程。", action="start")
        old_ref = first["snapshot"]["task"]["task_requirement_model_ref"]

        result = await session.process_message("改成给 voice-agent 准备代码重构计划。")
        snapshot = result["snapshot"]
        names = [event["event_name"] for event in session.journal.events()]

        assert snapshot["task"]["current_plan_version"] == 2
        assert snapshot["task"]["task_kind"] == "code_refactor"
        assert snapshot["task"]["task_requirement_model_ref"] != old_ref
        assert "TASK_REQUIREMENT_MODEL_INVALIDATED" in names
        assert names.count("TASK_REQUIREMENT_MODEL_ACCEPTED") == 2
        assert set(_requirements(snapshot)) == {
            "refactor_scope", "refactor_objectives", "compatibility_constraints", "acceptance_criteria"
        }
        assert _roles(snapshot).count("TASK_MODELER") == 2

    asyncio.run(scenario())


def test_replay_restores_model_requirement_states_role_refs_and_reviewer_without_calls() -> None:
    async def scenario() -> None:
        session = _session("dynamic_replay")
        await session.process_message(
            "帮我给 voice-agent 设计一个代码重构计划。", action="start"
        )
        result = await session.process_message(
            "目标是降低模块耦合，必须保持现有 API 兼容，验收标准是全部测试通过。"
        )
        original = result["snapshot"]

        replayed_state = SlowTaskState()
        for event in session.journal.events():
            replayed_state.reduce_event(event)
        replayed_task = replayed_state.tasks[original["task"]["task_id"]]
        _, focus_state, tool_state = session._projections()
        role_state = session._replayed_role_state(replayed_task)
        replayed_pack = TaskContextPackBuilder().build(
            slowtask_state=replayed_state,
            task_focus_state=focus_state,
            tool_execution_state=tool_state,
            evidence_catalog={},
            conversation=(),
            latest_user_input=None,
            role_state=role_state,
        )

        assert replayed_task.task_requirement_model == session._projections()[0].tasks[replayed_task.task_id].task_requirement_model
        assert replayed_task.current_plan_version == original["task"]["current_plan_version"]
        assert set(replayed_task.requirement_states) >= {
            "refactor_scope", "refactor_objectives", "compatibility_constraints", "acceptance_criteria"
        }
        assert replayed_pack.prior_role_proposal_refs == tuple(original["task"]["prior_role_proposal_refs"])
        assert replayed_pack.reviewer_status == "PASS"
        assert {item["requirement_id"]: item["status"] for item in replayed_pack.requirement_summary}[
            "acceptance_criteria"
        ] == "RESOLVED"

    asyncio.run(scenario())


def test_ordinary_patch_obeys_three_call_budget_and_does_not_remodel_task() -> None:
    async def scenario() -> None:
        session = _session("dynamic_call_budget")
        first = await session.process_message(
            "帮我给 voice-agent 设计一个代码重构计划。", action="start"
        )
        first_roles = _roles(first["snapshot"])
        assert first_roles == ["TASK_MODELER", "REQUIREMENT_ANALYST", "CLARIFIER"]

        second = await session.process_message(
            "目标是降低模块耦合，必须保持现有 API 兼容，验收标准是全部测试通过。"
        )
        added_roles = _roles(second["snapshot"])[len(first_roles):]

        assert added_roles == ["REQUIREMENT_ANALYST", "PLANNER", "REVIEWER"]
        assert len(added_roles) <= 3
        assert "TASK_MODELER" not in added_roles
        assert not ({"CLARIFIER", "PLANNER"} <= set(added_roles))

    asyncio.run(scenario())
