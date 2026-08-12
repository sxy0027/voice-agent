from __future__ import annotations

from copy import deepcopy

from voice_agent.slowtask.requirement_model import validate_task_requirement_model
from voice_agent.slowtask.slot_ledger import SlotState, SlotUpdate, build_slot_ledger
from voice_agent.slowtask.task_profiles import builtin_task_profile_registry
from voice_agent.slowtask.tool_validation import (
    ToolArgumentCompiler,
    ToolGapResolver,
    ToolValidationStatus,
    accepted_fact_inputs_from_ledger,
)
from voice_agent.tools.demo_manifests import mvp2_demo_tool_manifests
from voice_agent.tools.registry import ToolRegistry


def _customer_model_and_registry():
    registry = ToolRegistry(mvp2_demo_tool_manifests())
    payload = deepcopy(builtin_task_profile_registry().get("customer_reception").model_payload)
    payload["source_proposal_ref"] = "proposal://synthetic/tool-validation"
    payload["accepted_context_hash"] = "sha256:synthetic-tool-validation"
    return validate_task_requirement_model(payload, tool_registry=registry), registry


def _ledger():
    first = "evidence://synthetic/turn-1"
    second = "evidence://synthetic/turn-2"
    values = (
        ("time_window", "12:30", second),
        ("location_anchor", "北京市海淀区中关村领展购物广场附近", second),
        ("party_size", 8, second),
        ("budget", 200, second),
        ("dietary_constraints", ["两位不吃辣"], second),
        ("cuisine_preference", "云南菜", first),
    )
    return build_slot_ledger(
        tuple(
            SlotUpdate(
                name=name,
                normalized_value=value,
                raw_evidence="synthetic redacted fact",
                state=SlotState.RESOLVED,
                evidence_ref=evidence_ref,
                source="synthetic_user",
                plan_version=1,
            )
            for name, value, evidence_ref in values
        )
    )


def test_customer_reception_query_is_deterministic_bounded_and_multi_turn_grounded() -> None:
    model, registry = _customer_model_and_registry()
    contract = ToolGapResolver().allowed_contracts(
        model=model,
        active_tool_gap_ids=("venue_options",),
        tool_registry=registry,
    )[0]
    facts = accepted_fact_inputs_from_ledger(
        ledger=_ledger(),
        requirement_ids=contract.input_requirement_ids,
    )

    compiled, report = ToolArgumentCompiler().compile(
        task_id="task_synthetic",
        plan_version=1,
        contract=contract,
        accepted_fact_inputs=facts,
        tool_registry=registry,
    )

    assert compiled is not None
    assert report.status is ToolValidationStatus.PASS
    assert tuple(compiled.arguments) == ("query",)
    query = compiled.arguments["query"]
    assert query == "北京市海淀区中关村领展购物广场附近 云南菜 8人 人均200以内 两位不吃辣 12:30 餐厅"
    assert len(query) <= 320
    assert compiled.argument_source_evidence_refs["query"] == (
        "evidence://synthetic/turn-2",
        "evidence://synthetic/turn-1",
    )
    assert report.provided_arguments == ("query",)
    assert "北京市" not in repr(report.to_dict())


def test_wrong_raw_planner_tool_and_argument_are_ignored_and_repaired_once() -> None:
    model, registry = _customer_model_and_registry()
    contract = ToolGapResolver().allowed_contracts(
        model=model,
        active_tool_gap_ids=("venue_options",),
        tool_registry=registry,
    )[0]
    facts = accepted_fact_inputs_from_ledger(
        ledger=_ledger(),
        requirement_ids=contract.input_requirement_ids,
    )

    compiled, report = ToolArgumentCompiler().compile(
        task_id="task_synthetic",
        plan_version=1,
        contract=contract,
        accepted_fact_inputs=facts,
        tool_registry=registry,
        caused_by_proposal_ref="proposal://synthetic/wrong-low-level-call",
        diagnostic_candidate={
            "tool_name": "demo.itinerary.search",
            "arguments": {"location": "diagnostic value must be ignored"},
        },
    )

    assert compiled is not None
    assert compiled.contract.tool_name == "webSearch"
    assert tuple(compiled.arguments) == ("query",)
    assert report.status is ToolValidationStatus.PASS
    assert report.repair_attempted is True
    assert "DETERMINISTIC_REPAIR_APPLIED" in report.reason_codes
    assert "RAW_PROPOSAL_TOOL_IGNORED" in report.reason_codes
    assert report.caused_by_proposal_ref == "proposal://synthetic/wrong-low-level-call"


def test_compiler_blocks_without_accepted_facts_and_reports_specific_source() -> None:
    model, registry = _customer_model_and_registry()
    contract = ToolGapResolver().allowed_contracts(
        model=model,
        active_tool_gap_ids=("venue_options",),
        tool_registry=registry,
    )[0]

    compiled, report = ToolArgumentCompiler().compile(
        task_id="task_synthetic",
        plan_version=1,
        contract=contract,
        accepted_fact_inputs=(),
        tool_registry=registry,
    )

    assert compiled is None
    assert report.status is ToolValidationStatus.BLOCKED
    assert report.error_source is not None
    assert report.reason_codes == ("NO_ACCEPTED_FACTS_FOR_RESOLVER",)
    assert report.missing_arguments == ("query",)
