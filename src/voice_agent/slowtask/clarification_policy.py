from __future__ import annotations

"""Deterministic clarification policy for schema-backed SlowTask gaps."""

from collections.abc import Mapping
from dataclasses import dataclass

from voice_agent.slowtask.slot_ledger import SlotLedger
from voice_agent.slowtask.task_schema import RequirementAssessment, RequirementStage, TaskSchema


@dataclass(frozen=True)
class ClarificationPlan:
    clarification_id: str
    blocked_stage: str
    ask_fields: tuple[str, ...]
    known_fields: tuple[str, ...]
    reason: str
    attempt: int

    def to_dict(self) -> dict[str, object]:
        return {
            "clarification_id": self.clarification_id,
            "blocked_stage": self.blocked_stage,
            "ask_fields": list(self.ask_fields),
            "known_fields": list(self.known_fields),
            "reason": self.reason,
            "attempt": self.attempt,
        }


def plan_clarification(
    *,
    schema: TaskSchema,
    ledger: SlotLedger,
    assessment: RequirementAssessment,
    clarification_id: str,
) -> ClarificationPlan | None:
    blocked_stage = _first_blocked_stage(assessment)
    if blocked_stage is None:
        return None

    if assessment.conflicting_fields:
        reason = "conflicting"
        candidate_fields = assessment.conflicting_fields
    elif assessment.ambiguous_fields:
        reason = "ambiguous"
        candidate_fields = assessment.ambiguous_fields
    else:
        reason = "missing"
        candidate_fields = assessment.missing_by_stage.get(blocked_stage.value, ())

    specs = schema.slots_by_name
    ordered = sorted(
        (field for field in candidate_fields if field in specs),
        key=lambda field: (specs[field].priority, field),
    )
    selected = _select_related_fields(ordered, schema=schema)
    known = tuple(
        name
        for name, record in ledger.records.items()
        if name in specs and record.state.value == "RESOLVED" and name not in selected
    )
    attempt = 1 + max((ledger.records[field].asked_count for field in selected if field in ledger.records), default=0)
    return ClarificationPlan(
        clarification_id=clarification_id,
        blocked_stage=blocked_stage.value,
        ask_fields=tuple(selected),
        known_fields=tuple(sorted(known, key=lambda field: (specs[field].priority, field))),
        reason=reason,
        attempt=attempt,
    )


def _first_blocked_stage(assessment: RequirementAssessment) -> RequirementStage | None:
    for stage in RequirementStage:
        if not assessment.ready_for.get(stage.value, False):
            return stage
    return None


def _select_related_fields(fields: list[str], *, schema: TaskSchema) -> tuple[str, ...]:
    if not fields:
        return ()
    specs = schema.slots_by_name
    first_group = specs[fields[0]].question_group
    related = [field for field in fields if specs[field].question_group == first_group]
    if first_group in {"core_reception", "meal_constraints"}:
        return tuple(related[:3])
    return tuple(fields[:2])


def asked_count_map_from_events(events: tuple[Mapping[str, object], ...] | list[Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        if event.get("event_name") != "CLARIFICATION_REQUESTED":
            continue
        fields = event.get("missing_or_ambiguous_fields", ())
        if isinstance(fields, (str, bytes)):
            continue
        if not isinstance(fields, (list, tuple)):
            continue
        for field in fields:
            name = str(field)
            counts[name] = counts.get(name, 0) + 1
    return counts


__all__ = ["ClarificationPlan", "asked_count_map_from_events", "plan_clarification"]
