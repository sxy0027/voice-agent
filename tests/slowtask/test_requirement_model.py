from __future__ import annotations

from copy import deepcopy

import pytest

from voice_agent.slowtask.requirement_model import (
    RequirementModelValidationError,
    RequirementStage,
    assess_requirements,
    validate_requirement_value,
    validate_task_requirement_model,
)
from voice_agent.slowtask.slot_ledger import SlotState, SlotUpdate, build_slot_ledger
from voice_agent.slowtask.task_profiles import builtin_task_profile_registry
from voice_agent.tools.demo_manifests import mvp2_demo_tool_manifests
from voice_agent.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    return ToolRegistry(mvp2_demo_tool_manifests())


def _model_payload(profile_id: str = "research_report") -> dict[str, object]:
    return deepcopy(builtin_task_profile_registry().get(profile_id).model_payload)


def _validate(payload: dict[str, object]):
    return validate_task_requirement_model(
        payload,
        tool_registry=_registry(),
        source_proposal_ref="proposal://synthetic/task-modeler",
        accepted_context_hash="sha256:synthetic-context",
    )


def test_dynamic_model_accepts_parallel_task_profile_and_registered_tool_binding() -> None:
    model = _validate(_model_payload())

    assert model.task_kind == "research_report"
    assert "location_anchor" not in model.requirements_by_id
    assert model.requirements_by_id["source_coverage"].source_route.value == "TOOL"
    assert model.requirements_by_id["source_coverage"].tool_bindings[0].tool_name == "webSearch"


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda value: value["requirements"][0].update(requirement_id="Bad-ID"), "safe token"),
        (lambda value: value["requirements"][0].update(value_type="python"), "invalid type"),
        (lambda value: value["requirements"][0].update(description="请提供 API token"), "sensitive"),
        (
            lambda value: value["requirements"][0]["validation_rule"].update(
                code="lambda value: True"
            ),
            "unsupported fields",
        ),
        (
            lambda value: value["requirements"][-1]["tool_bindings"][0].update(tool_name="unknown.tool"),
            "unknown tool",
        ),
        (
            lambda value: value["requirements"][-1]["tool_bindings"][0].update(argument_name="expanded_argument"),
            "unknown argument",
        ),
    ],
)
def test_dynamic_model_rejects_unsafe_or_manifest_expanding_requirements(mutation, match: str) -> None:
    payload = _model_payload()
    mutation(payload)

    with pytest.raises(RequirementModelValidationError, match=match):
        _validate(payload)


def test_dynamic_model_rejects_unbounded_activation_nesting() -> None:
    payload = _model_payload("code_refactor")
    activation: dict[str, object] = {"operator": "always"}
    for _ in range(7):
        activation = {"operator": "all_of", "clauses": [activation]}
    payload["requirements"][0]["activation"] = activation

    with pytest.raises(RequirementModelValidationError, match="nesting|bounded"):
        _validate(payload)


def test_readiness_routes_user_tool_and_optional_gaps_without_crossing_owners() -> None:
    model = _validate(_model_payload())
    ledger = build_slot_ledger(
        (
            SlotUpdate(
                name="research_scope",
                normalized_value="proactive agent",
                raw_evidence="synthetic",
                state=SlotState.RESOLVED,
                evidence_ref="evidence://synthetic/scope",
                source="user_text",
                plan_version=1,
            ),
            SlotUpdate(
                name="output_format",
                normalized_value="structured report",
                raw_evidence="synthetic",
                state=SlotState.RESOLVED,
                evidence_ref="evidence://synthetic/format",
                source="user_text",
                plan_version=1,
            ),
        )
    )

    assessment = assess_requirements(model=model, ledger=ledger)

    assert assessment.user_gaps == ("target_audience",)
    assert assessment.tool_gaps == ("source_coverage",)
    assert "deadline" not in assessment.blocking_requirement_ids(RequirementStage.COMMITMENT)
    assert assessment.ready_for["plan"] is False


@pytest.mark.parametrize(
    ("requirement_id", "value", "match"),
    (
        ("party_size", "eight", "integer"),
        ("party_size", 0, "positive"),
        ("budget", {"maximum": 200}, "number"),
        ("dietary_constraints", "不吃辣", "string list"),
    ),
)
def test_requirement_values_follow_model_type_and_validation_rules(
    requirement_id: str,
    value: object,
    match: str,
) -> None:
    model = _validate(_model_payload("customer_reception"))

    with pytest.raises(RequirementModelValidationError, match=match):
        validate_requirement_value(model.requirements_by_id[requirement_id], value)


def test_requirement_values_accept_typed_customer_lunch_constraints() -> None:
    model = _validate(_model_payload("customer_reception"))

    validate_requirement_value(model.requirements_by_id["party_size"], 8)
    validate_requirement_value(model.requirements_by_id["budget"], 200)
    validate_requirement_value(model.requirements_by_id["dietary_constraints"], ["不吃辣"])
    validate_requirement_value(model.requirements_by_id["cuisine_preference"], "云南菜")
