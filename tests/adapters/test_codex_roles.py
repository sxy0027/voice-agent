from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
import json
from typing import Any

import pytest

from voice_agent.adapters import codex_slow_llm
from voice_agent.adapters.codex_roles import (
    CodexRole,
    CodexRoleAdapter,
    make_codex_cli_role_invoker,
    role_output_schema,
)
from voice_agent.adapters.codex_slow_llm import CodexSlowLLMAdapterConfig
from voice_agent.runtime.slow_system_workbench_sessions import WorkbenchRuntimeConfig, WorkbenchSession
from voice_agent.slowtask.task_profiles import builtin_task_profile_registry


def _invocation(session: WorkbenchSession, *, prefix: str, role_context: Mapping[str, Any]):
    return {
        "task_context_pack": {
            "task_binding": {"task_id": "task_roles", "plan_version": 1, "task_event_seq": 1},
            "current_goal": "synthetic role contract test",
        },
        "role_context": role_context,
        "task_binding": {"task_id": "task_roles", "plan_version": 1, "task_event_seq": 1},
        "source_evidence_refs": ("evidence://synthetic/role-input",),
        "event_id_prefix": prefix,
        "caused_by_event_id": session.journal.events()[-1]["event_id"],
        "created_monotonic_ms": 100,
        "created_wall_clock_ms": 1_700_000_000_100,
    }


def test_each_role_has_a_distinct_prompt_schema_and_fake_contract() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_role_contracts",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        adapter = session._role_adapter
        generic_model = deepcopy(builtin_task_profile_registry().get("generic_bootstrap").model_payload)
        generic_model.update(
            {
                "source_proposal_ref": "proposal://synthetic/task-modeler",
                "accepted_context_hash": "context_hash_synthetic",
                "candidate_tool_capabilities": [],
                "open_modeling_questions": ["desired_deliverable", "critical_constraints"],
                "confidence": "high",
            }
        )
        contexts = {
            CodexRole.TASK_MODELER: {"goal": "帮我规划一篇关于 proactive agent 的调研报告"},
            CodexRole.REQUIREMENT_ANALYST: {
                "task_requirement_model": generic_model,
                "evidence_text": "我希望得到一份报告",
            },
            CodexRole.CLARIFIER: {
                "selected_requirement_ids": ["desired_deliverable"],
                "selected_labels": {"desired_deliverable": "最终产出"},
                "max_fields": 3,
            },
            CodexRole.PLANNER: {
                "task_requirement_model": {"task_summary": "测试", "requirements": [], "success_criteria": []},
                "resolved_requirement_ids": [],
                "tool_gap_ids": [],
                "stale_evidence_refs": [],
            },
            CodexRole.REVIEWER: {"plan_proposal": {"ordered_steps": ["验证"]}},
        }
        for index, role in enumerate(CodexRole):
            result = await adapter.invoke(role=role, **_invocation(
                session, prefix=f"role_contract_{index}", role_context=contexts[role]
            ))
            assert result.role is role
            assert result.proposal["role"] == role.value
            assert result.validation_status == "validated"
            assert result.proposal["boundary_assertions"]["no_state_mutation"] is True
            assert role_output_schema(role)["properties"]["role"]["const"] == role.value
            assert result.structured_output_event["role"] == role.value

    asyncio.run(scenario())


def test_local_codex_invoker_passes_role_specific_prompt_and_schema(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_run(config, prompt, *, output_schema_json=None, on_provider_event=None):
        captured["prompt"] = prompt
        captured["schema"] = str(output_schema_json)
        return (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "text": json.dumps(
                            {
                                "role": "CLARIFIER",
                                "public_summary": "只询问后端选定字段",
                                "payload_json": json.dumps(
                                    {
                                        "clarification_id": "clarification_real",
                                        "question_text": "请补充目标读者。",
                                        "covered_requirement_ids": ["target_audience"],
                                        "expected_answer_shape": "简短文本",
                                        "optional_examples": [],
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                            ensure_ascii=False,
                        )
                    },
                },
                ensure_ascii=False,
            ),
            "",
        )

    monkeypatch.setattr(codex_slow_llm, "_run_codex_cli_async", fake_run)
    invoker = make_codex_cli_role_invoker(
        CodexSlowLLMAdapterConfig(provider_mode="codex_cli_local", allow_local_codex_cli=True)
    )
    result = asyncio.run(
        invoker(
            CodexRole.CLARIFIER,
            {"selected_requirement_ids": ["target_audience"]},
            role_output_schema(CodexRole.CLARIFIER),
        )
    )

    assert result["payload"]["covered_requirement_ids"] == ["target_audience"]
    assert "Phrase only the backend-selected requirements" in captured["prompt"]
    assert "Put the role payload as JSON text in payload_json" in captured["prompt"]
    assert json.loads(captured["schema"])["properties"]["role"] == {
        "const": "CLARIFIER",
        "type": "string",
    }
    assert '"additionalProperties":false' in captured["schema"]


@pytest.mark.parametrize("role", list(CodexRole))
def test_real_jsonl_transport_extracts_each_role_with_its_own_schema(monkeypatch, role: CodexRole) -> None:
    captured: dict[str, str] = {}

    async def fake_run(config, prompt, *, output_schema_json=None, on_provider_event=None):
        captured["prompt"] = prompt
        captured["schema"] = str(output_schema_json)
        return (
            "\n".join(
                (
                    json.dumps({"type": "thread.started", "thread_id": "synthetic_thread"}),
                    json.dumps({"type": "turn.started"}),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "synthetic_item",
                                "type": "agent_message",
                                "text": json.dumps(
                                    {
                                        "role": role.value,
                                        "public_summary": f"{role.value} transport",
                                        "payload_json": "{}",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
                )
            ),
            "",
        )

    monkeypatch.setattr(codex_slow_llm, "_run_codex_cli_async", fake_run)
    invoker = make_codex_cli_role_invoker(
        CodexSlowLLMAdapterConfig(provider_mode="codex_cli_local", allow_local_codex_cli=True)
    )
    result = asyncio.run(invoker(role, {"goal": "synthetic"}, role_output_schema(role)))

    assert result["public_summary"] == f"{role.value} transport"
    assert json.loads(captured["schema"])["properties"]["role"] == {
        "const": role.value,
        "type": "string",
    }
    assert "Return only JSON matching the transport schema" in captured["prompt"]


def test_missing_real_structured_output_is_exposed_and_not_validated_as_generic(monkeypatch) -> None:
    async def fake_run(config, prompt, *, output_schema_json=None, on_provider_event=None):
        return (
            "\n".join(
                (
                    json.dumps({"type": "thread.started", "thread_id": "synthetic_thread"}),
                    json.dumps({"type": "turn.started"}),
                    json.dumps({"type": "turn.failed", "error": {"message": "synthetic_failure"}}),
                )
            ),
            "",
        )

    monkeypatch.setattr(codex_slow_llm, "_run_codex_cli_async", fake_run)

    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_missing_real_role_output",
            config=WorkbenchRuntimeConfig(provider_mode="codex_cli_local"),
        )
        result = await session.process_message(
            "请帮忙规划一个接待午饭，选云南菜。",
            action="start",
        )
        task = result["snapshot"]["task"]
        trace = [item for item in result["snapshot"]["provider_trace"] if item.get("role")]

        assert task["task_kind"] == "customer_reception"
        assert task["task_model_status"] == "DEGRADED"
        assert task["task_model_confidence"] == "low"
        assert trace[0]["validation_status"] == "degraded"
        assert trace[0]["degraded_reason"] == "missing_structured_output"
        assert trace[0]["output_mode"] == "degraded"

    asyncio.run(scenario())


def test_clarifier_extra_requirement_fails_validation_and_uses_selected_only_fallback() -> None:
    async def provider(role, context, schema):
        assert role is CodexRole.CLARIFIER
        return {
            "public_summary": "越权问题",
            "payload": {
                "clarification_id": "clarification_bad",
                "question_text": "请同时提供预算和密码",
                "covered_requirement_ids": ["target_audience", "password"],
                "expected_answer_shape": "text",
                "optional_examples": [],
            },
        }

    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_clarifier_boundary",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        adapter = CodexRoleAdapter(
            boundary=session._boundary,
            tool_registry=session._tool_registry,
            provider_invoker=provider,
        )
        result = await adapter.invoke(
            role=CodexRole.CLARIFIER,
            **_invocation(
                session,
                prefix="clarifier_mismatch",
                role_context={
                    "selected_requirement_ids": ["target_audience"],
                    "selected_labels": {"target_audience": "目标读者"},
                    "max_fields": 3,
                },
            ),
        )

        assert result.validation_status == "degraded"
        assert result.degraded_reason == "role_output_validation_failed"
        assert result.validation_failed_event is not None
        assert result.proposal["payload"]["covered_requirement_ids"] == ["target_audience"]
        assert result.proposal["payload"]["question_text"] == "继续规划前，请补充目标读者。"

    asyncio.run(scenario())


def test_analyst_invalid_value_type_is_rejected_before_requirement_acceptance() -> None:
    model_payload = deepcopy(
        builtin_task_profile_registry().get("customer_reception").model_payload
    )
    model_payload.update(
        {
            "source_proposal_ref": "proposal://synthetic/customer-reception",
            "accepted_context_hash": "sha256:synthetic-context",
            "candidate_tool_capabilities": ["webSearch"],
            "open_modeling_questions": [],
            "confidence": "high",
        }
    )

    async def provider(role, context, schema):
        return {
            "public_summary": "invalid party size",
            "payload": {
                "requirements": [
                    {
                        "requirement_id": "party_size",
                        "proposed_status": "RESOLVED",
                        "proposed_value": "eight",
                        "resolution_route": "USER",
                        "source_evidence_refs": ["evidence://synthetic/role-input"],
                        "confidence": "high",
                        "ambiguity_reason": None,
                        "conflict_candidates": [],
                        "blocks_stage": None,
                        "materially_changes_task": False,
                    }
                ]
            },
        }

    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_analyst_value_type",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        adapter = CodexRoleAdapter(
            boundary=session._boundary,
            tool_registry=session._tool_registry,
            provider_invoker=provider,
        )
        result = await adapter.invoke(
            role=CodexRole.REQUIREMENT_ANALYST,
            **_invocation(
                session,
                prefix="analyst_value_type",
                role_context={
                    "task_requirement_model": model_payload,
                    "requirement_summary": [],
                    "evidence_text": "8个人",
                    "source_evidence_refs": ["evidence://synthetic/role-input"],
                    "stale_evidence_refs": [],
                },
            ),
        )

        assert result.validation_status == "degraded"
        assert result.degraded_reason == "role_output_validation_failed"
        assert result.validation_failed_event is not None
        assert result.proposal["payload"]["requirements"][0]["proposed_value"] == 8

    asyncio.run(scenario())


@pytest.mark.parametrize("violation", ["unknown_tool", "stale", "unknown_requirement", "side_effect"])
def test_planner_overreach_degrades_to_non_executable_draft(violation: str) -> None:
    async def provider(role, context, schema):
        payload = {
            "planning_mode": "FINAL_PLAN_CANDIDATE",
            "plan_summary": "候选计划",
            "ordered_steps": ["形成计划"],
            "requirement_coverage": ["known"],
            "proposed_tool_calls": [],
            "unresolved_optional_items": [],
            "assumptions": [],
            "risks": [],
            "requires_confirmation": False,
            "source_evidence_refs": ["evidence://synthetic/current"],
        }
        if violation == "unknown_tool":
            payload["proposed_tool_calls"] = [{"tool_name": "unknown.tool", "arguments": {}}]
        elif violation == "stale":
            payload["source_evidence_refs"] = ["evidence://synthetic/stale"]
        elif violation == "unknown_requirement":
            payload["requirement_coverage"] = ["known", "unknown"]
        else:
            payload["ordered_steps"] = ["已预订外部场地"]
        return {"public_summary": "planner candidate", "payload": payload}

    async def scenario() -> None:
        session = WorkbenchSession(
            session_id=f"test_planner_{violation}",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        adapter = CodexRoleAdapter(
            boundary=session._boundary,
            tool_registry=session._tool_registry,
            provider_invoker=provider,
        )
        result = await adapter.invoke(
            role=CodexRole.PLANNER,
            **_invocation(
                session,
                prefix=f"planner_{violation}",
                role_context={
                    "resolved_requirement_ids": ["known"],
                    "stale_evidence_refs": ["evidence://synthetic/stale"],
                },
            ),
        )

        assert result.validation_status == "degraded"
        assert result.proposal["payload"]["planning_mode"] == "DRAFT_PLAN"
        assert result.proposal["payload"]["proposed_tool_calls"] == []
        assert result.proposal["payload"]["ordered_steps"] == []

    asyncio.run(scenario())


def test_invalid_reviewer_output_fails_closed_as_block() -> None:
    async def provider(role, context, schema):
        return {"public_summary": "invalid", "payload": {"verdict": "PASS_ANYWAY", "violations": []}}

    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_reviewer_fail_closed",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        adapter = CodexRoleAdapter(
            boundary=session._boundary,
            tool_registry=session._tool_registry,
            provider_invoker=provider,
        )
        result = await adapter.invoke(
            role=CodexRole.REVIEWER,
            **_invocation(session, prefix="reviewer_invalid", role_context={"plan_proposal": {"ordered_steps": ["x"]}}),
        )

        assert result.validation_status == "degraded"
        assert result.proposal["payload"]["verdict"] == "BLOCK"

    asyncio.run(scenario())
