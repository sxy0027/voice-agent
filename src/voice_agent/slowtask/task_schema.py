from __future__ import annotations

"""Schema-driven requirement evaluation for SlowTask-owned task facts."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from voice_agent.slowtask.slot_ledger import SlotLedger, SlotState


class RequirementStage(str, Enum):
    SEARCH = "search"
    PLAN = "plan"
    COMMITMENT = "commitment"


class InferencePolicy(str, Enum):
    EXPLICIT_ONLY = "explicit_only"
    APPROXIMATE_ALLOWED = "approximate_allowed"
    PREFERENCE_OPEN_ALLOWED = "preference_open_allowed"
    EXPLICIT_EMPTY_ALLOWED = "explicit_empty_allowed"


@dataclass(frozen=True)
class SlotSpec:
    name: str
    label: str
    required_at: RequirementStage | None
    required_for_tools: tuple[str, ...] = ()
    inference_policy: InferencePolicy = InferencePolicy.EXPLICIT_ONLY
    explicit_empty_allowed: bool = False
    question_group: str = "general"
    priority: int = 100
    conditional_on: str | None = None


@dataclass(frozen=True)
class TaskSchema:
    name: str
    slots: tuple[SlotSpec, ...]

    def slot(self, name: str) -> SlotSpec:
        for spec in self.slots:
            if spec.name == name:
                return spec
        raise KeyError(name)

    @property
    def slots_by_name(self) -> Mapping[str, SlotSpec]:
        return {spec.name: spec for spec in self.slots}


@dataclass(frozen=True)
class RequirementAssessment:
    ready_for: Mapping[str, bool]
    missing_by_stage: Mapping[str, tuple[str, ...]]
    ambiguous_fields: tuple[str, ...]
    conflicting_fields: tuple[str, ...]
    assumptions: tuple[str, ...]

    def blocking_fields(self, stage: RequirementStage) -> tuple[str, ...]:
        fields: list[str] = []
        for field in self.missing_by_stage.get(stage.value, ()):
            if field not in fields:
                fields.append(field)
        for field in (*self.ambiguous_fields, *self.conflicting_fields):
            if field not in fields:
                fields.append(field)
        return tuple(fields)


CUSTOMER_RECEPTION_SCHEMA_NAME = "customer_reception"


def customer_reception_schema(
    *,
    tool_required_arguments: Mapping[str, Sequence[str]] | None = None,
) -> TaskSchema:
    """Return the first MVP schema.

    ``required_for_tools`` is derived from known manifest arguments where the
    schema field maps to an actual tool argument. Stage readiness is product
    policy and does not expand the tool manifest itself.
    """

    tool_args = {tool: tuple(args) for tool, args in (tool_required_arguments or {}).items()}

    def tool_refs(field_to_arg: Mapping[str, str]) -> tuple[str, ...]:
        refs: list[str] = []
        for tool_name, arg_name in field_to_arg.items():
            if arg_name in tool_args.get(tool_name, ()):
                refs.append(f"{tool_name}:{arg_name}")
        return tuple(refs)

    return TaskSchema(
        name=CUSTOMER_RECEPTION_SCHEMA_NAME,
        slots=(
            SlotSpec(
                "time_window",
                "用餐/接待时间窗口",
                RequirementStage.SEARCH,
                inference_policy=InferencePolicy.EXPLICIT_ONLY,
                question_group="core_reception",
                priority=10,
            ),
            SlotSpec(
                "location_anchor",
                "位置锚点",
                RequirementStage.SEARCH,
                required_for_tools=tool_refs(
                    {
                        "demo.itinerary.search": "company_location",
                        "webSearch": "query",
                    }
                ),
                inference_policy=InferencePolicy.APPROXIMATE_ALLOWED,
                question_group="core_reception",
                priority=20,
            ),
            SlotSpec(
                "party_size",
                "接待人数",
                RequirementStage.SEARCH,
                inference_policy=InferencePolicy.EXPLICIT_ONLY,
                question_group="core_reception",
                priority=30,
            ),
            SlotSpec(
                "agenda_duration",
                "接待/行程时长",
                None,
                required_for_tools=tool_refs({"demo.itinerary.search": "days"}),
                inference_policy=InferencePolicy.EXPLICIT_ONLY,
                question_group="schedule",
                priority=40,
            ),
            SlotSpec(
                "budget",
                "预算",
                RequirementStage.PLAN,
                inference_policy=InferencePolicy.EXPLICIT_ONLY,
                question_group="meal_constraints",
                priority=50,
                conditional_on="meal_planning",
            ),
            SlotSpec(
                "dietary_constraints",
                "忌口/饮食限制",
                RequirementStage.PLAN,
                inference_policy=InferencePolicy.EXPLICIT_EMPTY_ALLOWED,
                explicit_empty_allowed=True,
                question_group="meal_constraints",
                priority=60,
                conditional_on="meal_planning",
            ),
            SlotSpec(
                "cuisine_preference",
                "菜系偏好",
                None,
                inference_policy=InferencePolicy.PREFERENCE_OPEN_ALLOWED,
                question_group="meal_preferences",
                priority=70,
            ),
            SlotSpec(
                "meal_type",
                "用餐类型",
                None,
                inference_policy=InferencePolicy.PREFERENCE_OPEN_ALLOWED,
                question_group="meal_preferences",
                priority=80,
            ),
            SlotSpec("private_room", "包间/安静空间", None, question_group="service", priority=90),
            SlotSpec("parking_needed", "停车需求", None, question_group="service", priority=100),
            SlotSpec("invoice_needed", "发票需求", None, question_group="service", priority=110),
            SlotSpec("transport_needed", "交通接送需求", None, question_group="service", priority=120),
            SlotSpec("guest_profile", "客户画像", None, question_group="context", priority=130),
        ),
    )


def evaluate_requirements(
    *,
    schema: TaskSchema,
    ledger: SlotLedger,
    task_features: Mapping[str, Any] | None = None,
) -> RequirementAssessment:
    features = dict(task_features or {})
    active_specs = tuple(spec for spec in schema.slots if _condition_applies(spec, features))
    missing_by_stage: dict[str, list[str]] = {stage.value: [] for stage in RequirementStage}
    ambiguous: list[str] = []
    conflicting: list[str] = []
    assumptions: list[str] = []

    for spec in active_specs:
        record = ledger.records.get(spec.name)
        if record is not None:
            if record.state == SlotState.AMBIGUOUS:
                ambiguous.append(spec.name)
            elif record.state == SlotState.CONFLICTING:
                conflicting.append(spec.name)
            elif record.state == SlotState.DEFAULTED:
                assumptions.append(spec.name)

        if spec.required_at is None:
            continue
        if record is None or record.state == SlotState.UNKNOWN:
            missing_by_stage[spec.required_at.value].append(spec.name)
        elif record.state in {SlotState.AMBIGUOUS, SlotState.CONFLICTING}:
            missing_by_stage[spec.required_at.value].append(spec.name)
        elif record.state == SlotState.DEFAULTED:
            missing_by_stage[spec.required_at.value].append(spec.name)

    cumulative_missing: dict[str, tuple[str, ...]] = {}
    seen_missing: list[str] = []
    for stage in RequirementStage:
        for field in missing_by_stage[stage.value]:
            if field not in seen_missing:
                seen_missing.append(field)
        cumulative_missing[stage.value] = tuple(seen_missing)

    ready_for: dict[str, bool] = {}
    for stage in RequirementStage:
        ready_for[stage.value] = (
            not cumulative_missing[stage.value]
            and not ambiguous
            and not conflicting
        )

    return RequirementAssessment(
        ready_for=ready_for,
        missing_by_stage=cumulative_missing,
        ambiguous_fields=tuple(dict.fromkeys(ambiguous)),
        conflicting_fields=tuple(dict.fromkeys(conflicting)),
        assumptions=tuple(dict.fromkeys(assumptions)),
    )


def _condition_applies(spec: SlotSpec, features: Mapping[str, Any]) -> bool:
    if spec.conditional_on is None:
        return True
    return bool(features.get(spec.conditional_on))


__all__ = [
    "CUSTOMER_RECEPTION_SCHEMA_NAME",
    "InferencePolicy",
    "RequirementAssessment",
    "RequirementStage",
    "SlotSpec",
    "TaskSchema",
    "customer_reception_schema",
    "evaluate_requirements",
]
