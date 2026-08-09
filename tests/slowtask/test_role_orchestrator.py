from __future__ import annotations

import asyncio
from copy import deepcopy

from voice_agent.adapters.codex_roles import CodexRole, RoleInvocationResult
from voice_agent.slowtask.role_orchestrator import (
    RoleRun,
    RoleRunBudget,
    SlowLLMRoleOrchestrator,
)
from voice_agent.slowtask.task_profiles import builtin_task_profile_registry
from voice_agent.tools.demo_manifests import mvp2_demo_tool_manifests
from voice_agent.tools.registry import ToolRegistry


class _StubRoleAdapter:
    def __init__(self) -> None:
        self.calls: list[CodexRole] = []
        self.reviewer_calls = 0

    async def invoke(self, *, role: CodexRole, task_binding, **kwargs) -> RoleInvocationResult:
        self.calls.append(role)
        if role == CodexRole.TASK_MODELER:
            payload = deepcopy(builtin_task_profile_registry().get("code_refactor").model_payload)
        elif role == CodexRole.PLANNER:
            payload = {
                "planning_mode": "DRAFT_PLAN",
                "plan_summary": "bounded draft",
                "ordered_steps": ["validate"],
                "requirement_coverage": [],
                "proposed_tool_calls": [],
                "source_evidence_refs": [],
            }
        else:
            self.reviewer_calls += 1
            payload = {
                "verdict": "REVISE" if self.reviewer_calls == 1 else "PASS",
                "violations": ["repair_once"] if self.reviewer_calls == 1 else [],
            }
        proposal_id = f"{role.value.lower()}_{len(self.calls)}"
        proposal = {
            "proposal_id": proposal_id,
            "input_context_hash": "sha256:stub",
            "task_binding": dict(task_binding),
            "public_summary": role.value,
            "payload": payload,
        }
        return RoleInvocationResult(
            role=role,
            proposal=proposal,
            proposal_ref=f"proposal://stub/{proposal_id}",
            structured_output_event={"event_id": f"event_{proposal_id}"},
            validation_failed_event=None,
            validation_status="validated",
            degraded_reason=None,
            trace_item={},
        )


def _invocation() -> dict[str, object]:
    return {
        "task_context_pack": {},
        "role_context": {},
        "task_binding": {"task_id": "task_stub", "plan_version": 1, "task_event_seq": 1},
        "source_evidence_refs": (),
        "event_id_prefix": "stub",
        "caused_by_event_id": "event_cause",
        "created_monotonic_ms": 1,
        "created_wall_clock_ms": 1,
        "context_hash": "sha256:stable",
    }


def test_task_modeler_cache_key_reuses_same_task_plan_context_and_contract() -> None:
    async def scenario() -> None:
        adapter = _StubRoleAdapter()
        orchestrator = SlowLLMRoleOrchestrator(
            adapter=adapter,
            tool_registry=ToolRegistry(mvp2_demo_tool_manifests()),
        )
        run = RoleRun(RoleRunBudget(max_invocations=2))

        first, _ = await orchestrator.model_task(run=run, invocation=_invocation())
        second, _ = await orchestrator.model_task(run=run, invocation=_invocation())

        assert first == second
        assert adapter.calls == [CodexRole.TASK_MODELER]
        assert len(run.invocations) == 1

    asyncio.run(scenario())


def test_planner_repair_is_bounded_to_one_and_roles_are_python_ordered() -> None:
    async def scenario() -> None:
        adapter = _StubRoleAdapter()
        orchestrator = SlowLLMRoleOrchestrator(
            adapter=adapter,
            tool_registry=ToolRegistry(mvp2_demo_tool_manifests()),
        )
        run = RoleRun(RoleRunBudget(max_invocations=6, max_planner_repairs=1))

        result = await orchestrator.plan_and_review(run=run, invocation=_invocation())

        assert result.accepted is True
        assert run.planner_repairs == 1
        assert adapter.calls == [
            CodexRole.PLANNER,
            CodexRole.REVIEWER,
            CodexRole.PLANNER,
            CodexRole.REVIEWER,
        ]
        assert len(run.invocations) == 4

    asyncio.run(scenario())


def test_declared_workbench_budgets_remain_four_for_new_task_and_three_for_patch() -> None:
    adapter = _StubRoleAdapter()
    orchestrator = SlowLLMRoleOrchestrator(
        adapter=adapter,
        tool_registry=ToolRegistry(mvp2_demo_tool_manifests()),
    )

    assert orchestrator.new_task_run().budget.max_invocations == 4
    assert orchestrator.patch_run().budget.max_invocations == 3
    assert orchestrator.new_task_run().budget.max_planner_repairs == 1
