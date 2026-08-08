from __future__ import annotations

from voice_agent.slowtask.clarification_policy import plan_clarification
from voice_agent.slowtask.slot_ledger import SlotState, SlotUpdate, build_slot_ledger
from voice_agent.slowtask.task_schema import customer_reception_schema, evaluate_requirements


def _update(name: str, value: object, *, state: SlotState = SlotState.RESOLVED) -> SlotUpdate:
    return SlotUpdate(
        name=name,
        normalized_value=value,
        raw_evidence="synthetic user evidence",
        state=state,
        evidence_ref="evidence://synthetic/test",
        source="user_text",
        plan_version=1,
    )


def test_customer_reception_core_fields_form_first_clarification_group() -> None:
    schema = customer_reception_schema()
    ledger = build_slot_ledger(())
    assessment = evaluate_requirements(schema=schema, ledger=ledger, task_features={})
    clarification = plan_clarification(
        schema=schema,
        ledger=ledger,
        assessment=assessment,
        clarification_id="clarification_test_core",
    )

    assert assessment.ready_for == {"search": False, "plan": False, "commitment": False}
    assert assessment.missing_by_stage["search"] == (
        "time_window",
        "location_anchor",
        "party_size",
    )
    assert clarification is not None
    assert clarification.ask_fields == ("time_window", "location_anchor", "party_size")
    assert clarification.reason == "missing"


def test_meal_planning_budget_and_dietary_are_second_group_after_core_fields() -> None:
    schema = customer_reception_schema()
    ledger = build_slot_ledger(
        (
            _update("time_window", "下周二下午"),
            _update("location_anchor", "中关村附近"),
            _update("party_size", "6 人"),
        )
    )
    assessment = evaluate_requirements(
        schema=schema,
        ledger=ledger,
        task_features={"meal_planning": True},
    )
    clarification = plan_clarification(
        schema=schema,
        ledger=ledger,
        assessment=assessment,
        clarification_id="clarification_test_meal",
    )

    assert assessment.ready_for["search"] is True
    assert assessment.ready_for["plan"] is False
    assert clarification is not None
    assert clarification.blocked_stage == "plan"
    assert clarification.ask_fields == ("budget", "dietary_constraints")
    assert clarification.known_fields == ("time_window", "location_anchor", "party_size")


def test_ambiguous_time_reasks_only_time_window() -> None:
    schema = customer_reception_schema()
    ledger = build_slot_ledger(
        (
            _update("time_window", "下周", state=SlotState.AMBIGUOUS),
            _update("location_anchor", "中关村附近"),
            _update("party_size", "6 人"),
        )
    )
    assessment = evaluate_requirements(schema=schema, ledger=ledger, task_features={})
    clarification = plan_clarification(
        schema=schema,
        ledger=ledger,
        assessment=assessment,
        clarification_id="clarification_test_ambiguous",
    )

    assert assessment.ambiguous_fields == ("time_window",)
    assert clarification is not None
    assert clarification.reason == "ambiguous"
    assert clarification.ask_fields == ("time_window",)
