from __future__ import annotations

"""Python-owned ordering, budgets, caching, and acceptance for Codex roles."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
import time
from typing import Any, Callable

from voice_agent.adapters.codex_roles import (
    ROLE_CONTRACT_VERSION,
    CodexRole,
    CodexRoleAdapter,
    RoleInvocationResult,
)
from voice_agent.slowtask.requirement_model import (
    RequirementSourceRoute,
    RequirementStage,
    TaskRequirementModel,
    TaskRequirementModelStatus,
    assess_requirements,
    validate_task_requirement_model,
)
from voice_agent.slowtask.slot_ledger import SlotLedger, SlotState, SlotUpdate
from voice_agent.tools.registry import ToolRegistry


class RoleOrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoleRunBudget:
    max_invocations: int
    total_timeout_seconds: int = 60
    max_planner_repairs: int = 1
    max_repair_invocations: int = 2


@dataclass
class RoleRun:
    budget: RoleRunBudget
    started_at: float = field(default_factory=time.monotonic)
    invocations: list[RoleInvocationResult] = field(default_factory=list)
    planner_repairs: int = 0
    repair_invocations: list[RoleInvocationResult] = field(default_factory=list)

    def require_capacity(self, role: CodexRole) -> None:
        if len(self.invocations) >= self.budget.max_invocations:
            raise RoleOrchestrationError(f"role invocation budget exhausted before {role.value}")
        if time.monotonic() - self.started_at > self.budget.total_timeout_seconds:
            raise RoleOrchestrationError("total role orchestration timeout exceeded")

    def require_repair_capacity(self, role: CodexRole) -> None:
        if len(self.repair_invocations) >= self.budget.max_repair_invocations:
            raise RoleOrchestrationError(f"repair invocation budget exhausted before {role.value}")
        if time.monotonic() - self.started_at > self.budget.total_timeout_seconds:
            raise RoleOrchestrationError("total role orchestration timeout exceeded")


@dataclass(frozen=True)
class PlanReviewResult:
    planner: RoleInvocationResult
    reviewer: RoleInvocationResult
    accepted: bool


class SlowLLMRoleOrchestrator:
    NEW_TASK_BUDGET = RoleRunBudget(max_invocations=4)
    PATCH_BUDGET = RoleRunBudget(max_invocations=3)

    def __init__(self, *, adapter: CodexRoleAdapter, tool_registry: ToolRegistry) -> None:
        self._adapter = adapter
        self._tool_registry = tool_registry
        self._cache: dict[tuple[str, int, str, str, str], RoleInvocationResult] = {}

    def new_task_run(self) -> RoleRun:
        return RoleRun(self.NEW_TASK_BUDGET)

    def patch_run(self) -> RoleRun:
        return RoleRun(self.PATCH_BUDGET)

    async def model_task(self, *, run: RoleRun, invocation: Mapping[str, Any]) -> tuple[TaskRequirementModel, RoleInvocationResult]:
        result = await self._invoke(run=run, role=CodexRole.TASK_MODELER, invocation=invocation, cacheable=True)
        model = validate_task_requirement_model(
            result.proposal["payload"],
            tool_registry=self._tool_registry,
            source_proposal_ref=result.proposal_ref,
            accepted_context_hash=str(result.proposal["input_context_hash"]),
        )
        if model.task_kind == "generic_bootstrap":
            model = replace(
                model,
                model_status=TaskRequirementModelStatus.PROVISIONAL_BOOTSTRAP,
                model_confidence="low",
                bootstrap_reason=result.degraded_reason or "task_kind_not_yet_specific",
                needs_remodeling=True,
            )
        elif result.validation_status == "degraded":
            model = replace(
                model,
                model_status=TaskRequirementModelStatus.DEGRADED,
                model_confidence="low",
                bootstrap_reason=result.degraded_reason or "task_modeler_degraded",
                needs_remodeling=True,
            )
        else:
            model = replace(
                model,
                model_status=TaskRequirementModelStatus.ACCEPTED_SPECIFIC,
                model_confidence=str(result.proposal.get("confidence", model.model_confidence)),
                bootstrap_reason=None,
                needs_remodeling=False,
            )
        return model, result

    async def analyze_requirements(
        self,
        *,
        run: RoleRun,
        model: TaskRequirementModel,
        ledger: SlotLedger,
        stale_evidence_refs: Sequence[str],
        invocation: Mapping[str, Any],
    ) -> tuple[tuple[SlotUpdate, ...], RoleInvocationResult]:
        result = await self._invoke(run=run, role=CodexRole.REQUIREMENT_ANALYST, invocation=invocation)
        specs = model.requirements_by_id
        stale = set(stale_evidence_refs)
        accepted: list[SlotUpdate] = []
        for raw in result.proposal["payload"].get("requirements", ()):
            requirement_id = str(raw.get("requirement_id", ""))
            spec = specs.get(requirement_id)
            if spec is None or spec.source_route not in {
                RequirementSourceRoute.USER,
                RequirementSourceRoute.DERIVED,
                RequirementSourceRoute.SYSTEM,
                RequirementSourceRoute.OPTIONAL,
            }:
                continue
            refs = tuple(str(ref) for ref in raw.get("source_evidence_refs", ()))
            if not refs or stale.intersection(refs):
                continue
            status_value = str(raw.get("proposed_status", "UNKNOWN"))
            if status_value not in {
                SlotState.RESOLVED.value,
                SlotState.AMBIGUOUS.value,
                SlotState.CONFLICTING.value,
                SlotState.CANDIDATE.value,
            }:
                continue
            status = SlotState(status_value)
            accepted.append(
                SlotUpdate(
                    name=requirement_id,
                    normalized_value=raw.get("proposed_value"),
                    raw_evidence=str(invocation.get("role_context", {}).get("evidence_text", ""))[:320],
                    state=status,
                    evidence_ref=refs[0],
                    source="accepted_requirement_analyst_proposal",
                    plan_version=int(invocation["task_binding"]["plan_version"]),
                    explicit_or_inferred=(
                        "explicit"
                        if spec.source_route in {RequirementSourceRoute.USER, RequirementSourceRoute.OPTIONAL}
                        else "inferred"
                    ),
                )
            )
        return tuple(accepted), result

    async def clarify(
        self,
        *,
        run: RoleRun,
        model: TaskRequirementModel,
        selected_requirement_ids: Sequence[str],
        invocation: Mapping[str, Any],
    ) -> RoleInvocationResult:
        specs = model.requirements_by_id
        selected = tuple(selected_requirement_ids)
        if not selected or any(specs[item].source_route != RequirementSourceRoute.USER for item in selected):
            raise RoleOrchestrationError("Clarifier may only receive selected USER requirements")
        return await self._invoke(run=run, role=CodexRole.CLARIFIER, invocation=invocation)

    async def plan_only(
        self,
        *,
        run: RoleRun,
        invocation: Mapping[str, Any],
    ) -> RoleInvocationResult:
        return await self._invoke(run=run, role=CodexRole.PLANNER, invocation=invocation)

    async def review_existing_plan(
        self,
        *,
        run: RoleRun,
        invocation: Mapping[str, Any],
        planner: RoleInvocationResult,
    ) -> PlanReviewResult:
        reviewer_invocation = dict(invocation)
        reviewer_context = dict(invocation.get("role_context", {}))
        reviewer_context["plan_proposal"] = dict(planner.proposal["payload"])
        reviewer_invocation["role_context"] = reviewer_context
        reviewer = await self._invoke(
            run=run,
            role=CodexRole.REVIEWER,
            invocation=reviewer_invocation,
        )
        verdict = str(reviewer.proposal["payload"].get("verdict", "BLOCK"))
        return PlanReviewResult(
            planner=planner,
            reviewer=reviewer,
            accepted=verdict == "PASS",
        )

    async def plan_and_review(self, *, run: RoleRun, invocation: Mapping[str, Any]) -> PlanReviewResult:
        planner = await self.plan_only(run=run, invocation=invocation)
        reviewer_invocation = dict(invocation)
        reviewer_context = dict(invocation.get("role_context", {}))
        reviewer_context["plan_proposal"] = dict(planner.proposal["payload"])
        reviewer_invocation["role_context"] = reviewer_context
        reviewer = await self._invoke(run=run, role=CodexRole.REVIEWER, invocation=reviewer_invocation)
        verdict = str(reviewer.proposal["payload"].get("verdict", "BLOCK"))
        if verdict == "REVISE" and run.planner_repairs < run.budget.max_planner_repairs:
            run.planner_repairs += 1
            repair_context = dict(reviewer_context)
            repair_context["review_feedback"] = dict(reviewer.proposal["payload"])
            repair_invocation = dict(invocation)
            repair_invocation["role_context"] = repair_context
            planner = await self._invoke(
                run=run,
                role=CodexRole.PLANNER,
                invocation=repair_invocation,
                repair=True,
            )
            reviewer_context["plan_proposal"] = dict(planner.proposal["payload"])
            reviewer_invocation["role_context"] = reviewer_context
            reviewer = await self._invoke(
                run=run,
                role=CodexRole.REVIEWER,
                invocation=reviewer_invocation,
                repair=True,
            )
            verdict = str(reviewer.proposal["payload"].get("verdict", "BLOCK"))
        return PlanReviewResult(planner=planner, reviewer=reviewer, accepted=verdict == "PASS")

    async def _invoke(
        self,
        *,
        run: RoleRun,
        role: CodexRole,
        invocation: Mapping[str, Any],
        cacheable: bool = False,
        repair: bool = False,
    ) -> RoleInvocationResult:
        if repair:
            run.require_repair_capacity(role)
        else:
            run.require_capacity(role)
        binding = invocation["task_binding"]
        context_hash = str(invocation.get("context_hash", "uncached"))
        key = (
            str(binding["task_id"]),
            int(binding["plan_version"]),
            context_hash,
            role.value,
            ROLE_CONTRACT_VERSION,
        )
        if cacheable and key in self._cache:
            return self._cache[key]
        result = await self._adapter.invoke(
            role=role,
            task_context_pack=invocation["task_context_pack"],
            role_context=invocation.get("role_context", {}),
            task_binding=binding,
            source_evidence_refs=invocation.get("source_evidence_refs", ()),
            event_id_prefix=str(invocation["event_id_prefix"]),
            caused_by_event_id=str(invocation["caused_by_event_id"]),
            created_monotonic_ms=int(invocation["created_monotonic_ms"]),
            created_wall_clock_ms=int(invocation["created_wall_clock_ms"]),
        )
        if repair:
            run.repair_invocations.append(result)
        else:
            run.invocations.append(result)
        if cacheable:
            self._cache[key] = result
        return result


def select_user_blockers(*, model: TaskRequirementModel, ledger: SlotLedger, max_fields: int = 3) -> tuple[str, ...]:
    assessment = assess_requirements(model=model, ledger=ledger)
    for stage in RequirementStage:
        stage_missing = set(assessment.missing_by_stage[stage.value])
        candidates = [
            spec
            for spec in model.requirements
            if spec.requirement_id in stage_missing and spec.source_route == RequirementSourceRoute.USER
        ]
        if candidates:
            candidates.sort(key=lambda item: (item.priority, item.requirement_id))
            first_group = candidates[0].question_group
            related = [item.requirement_id for item in candidates if item.question_group == first_group]
            return tuple(related[:max_fields])
    return ()


__all__ = [
    "PlanReviewResult", "RoleOrchestrationError", "RoleRun", "RoleRunBudget",
    "SlowLLMRoleOrchestrator", "select_user_blockers",
]
