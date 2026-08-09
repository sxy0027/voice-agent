from __future__ import annotations

"""Validated, non-executable task requirement models owned by SlowTask."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from voice_agent.slowtask.slot_ledger import SlotLedger, SlotState
from voice_agent.tools.manifest import ToolExecutionPolicyError
from voice_agent.tools.registry import ToolRegistry


SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SECRET_MARKERS = frozenset(
    {"password", "passwd", "secret", "token", "api key", "apikey", "credential", "cookie", "密码", "密钥", "令牌"}
)
MAX_REQUIREMENTS = 24
MAX_COMPONENTS = 12
MAX_SUCCESS_CRITERIA = 12
MAX_TEXT = 240
MAX_ENUM_VALUES = 24
MAX_NESTING_DEPTH = 5


class RequirementModelValidationError(ValueError):
    pass


class RequirementStage(str, Enum):
    SEARCH = "search"
    PLAN = "plan"
    COMMITMENT = "commitment"


class RequirementSourceRoute(str, Enum):
    USER = "USER"
    TOOL = "TOOL"
    DERIVED = "DERIVED"
    SYSTEM = "SYSTEM"
    OPTIONAL = "OPTIONAL"


class RequirementValueType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    TIME_WINDOW = "time_window"
    LOCATION = "location"
    ENUM = "enum"
    STRING_LIST = "string_list"


class RequirementStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    CANDIDATE = "CANDIDATE"
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DEFAULTED = "DEFAULTED"


class TaskRequirementModelStatus(str, Enum):
    PROVISIONAL_BOOTSTRAP = "PROVISIONAL_BOOTSTRAP"
    ACCEPTED_SPECIFIC = "ACCEPTED_SPECIFIC"
    DEGRADED = "DEGRADED"


ACTIVATION_OPERATORS = frozenset({"always", "component_present", "requirement_equals", "all_of", "any_of"})
VALIDATION_OPERATORS = frozenset(
    {
        "non_empty",
        "positive_integer",
        "bounded_number",
        "one_of",
        "approximate_location_allowed",
        "explicit_empty_allowed",
    }
)


@dataclass(frozen=True)
class ToolBinding:
    tool_name: str
    argument_name: str

    def to_dict(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "argument_name": self.argument_name}


@dataclass(frozen=True)
class RequirementSpec:
    requirement_id: str
    label: str
    description: str
    value_type: RequirementValueType
    source_route: RequirementSourceRoute
    required_at: RequirementStage | None
    importance: str
    activation: Mapping[str, Any]
    validation_rule: Mapping[str, Any]
    question_group: str
    priority: int
    tool_bindings: tuple[ToolBinding, ...] = ()
    sensitive: bool = False
    allow_explicit_empty: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "label": self.label,
            "description": self.description,
            "value_type": self.value_type.value,
            "source_route": self.source_route.value,
            "required_at": self.required_at.value if self.required_at is not None else None,
            "importance": self.importance,
            "activation": dict(self.activation),
            "validation_rule": dict(self.validation_rule),
            "question_group": self.question_group,
            "priority": self.priority,
            "tool_bindings": [binding.to_dict() for binding in self.tool_bindings],
            "sensitive": self.sensitive,
            "allow_explicit_empty": self.allow_explicit_empty,
        }


@dataclass(frozen=True)
class TaskRequirementModel:
    model_id: str
    model_version: int
    task_kind: str
    task_summary: str
    task_components: tuple[str, ...]
    requirements: tuple[RequirementSpec, ...]
    success_criteria: tuple[str, ...]
    applicable_stages: tuple[RequirementStage, ...]
    source_proposal_ref: str
    accepted_context_hash: str
    model_status: TaskRequirementModelStatus
    model_confidence: str
    bootstrap_reason: str | None
    needs_remodeling: bool

    @property
    def requirements_by_id(self) -> Mapping[str, RequirementSpec]:
        return {spec.requirement_id: spec for spec in self.requirements}

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "task_kind": self.task_kind,
            "task_summary": self.task_summary,
            "task_components": list(self.task_components),
            "requirements": [spec.to_dict() for spec in self.requirements],
            "success_criteria": list(self.success_criteria),
            "applicable_stages": [stage.value for stage in self.applicable_stages],
            "source_proposal_ref": self.source_proposal_ref,
            "accepted_context_hash": self.accepted_context_hash,
            "model_status": self.model_status.value,
            "model_confidence": self.model_confidence,
            "bootstrap_reason": self.bootstrap_reason,
            "needs_remodeling": self.needs_remodeling,
        }


@dataclass(frozen=True)
class RequirementAssessment:
    ready_for: Mapping[str, bool]
    missing_by_stage: Mapping[str, tuple[str, ...]]
    user_gaps: tuple[str, ...]
    tool_gaps: tuple[str, ...]
    derived_or_system_gaps: tuple[str, ...]
    ambiguous: tuple[str, ...]
    conflicting: tuple[str, ...]
    assumptions: tuple[str, ...]

    def blocking_requirement_ids(self, stage: RequirementStage) -> tuple[str, ...]:
        return self.missing_by_stage.get(stage.value, ())


def validate_task_requirement_model(
    payload: Mapping[str, Any],
    *,
    tool_registry: ToolRegistry,
    source_proposal_ref: str | None = None,
    accepted_context_hash: str | None = None,
) -> TaskRequirementModel:
    _bounded_mapping(payload, depth=0)
    _reject_unknown_keys(
        payload,
        {
            "model_id", "model_version", "task_kind", "task_summary", "task_components",
            "requirements", "success_criteria", "applicable_stages", "source_proposal_ref",
            "accepted_context_hash", "candidate_tool_capabilities", "open_modeling_questions",
            "confidence", "model_status", "model_confidence", "bootstrap_reason",
            "needs_remodeling",
        },
        "task requirement model",
    )
    model_id = _safe_token(payload.get("model_id"), "model_id")
    task_kind = _safe_token(payload.get("task_kind"), "task_kind")
    model_version = _bounded_int(payload.get("model_version"), "model_version", minimum=1, maximum=10_000)
    task_summary = _bounded_text(payload.get("task_summary"), "task_summary")
    components = _safe_token_list(payload.get("task_components", ()), "task_components", MAX_COMPONENTS)
    success_criteria = _bounded_text_list(
        payload.get("success_criteria", ()), "success_criteria", MAX_SUCCESS_CRITERIA
    )
    stages = tuple(
        RequirementStage(str(value))
        for value in _sequence(payload.get("applicable_stages", ()), "applicable_stages")
    )
    if not stages:
        raise RequirementModelValidationError("applicable_stages must not be empty")

    raw_requirements = _sequence(payload.get("requirements", ()), "requirements")
    if not 1 <= len(raw_requirements) <= MAX_REQUIREMENTS:
        raise RequirementModelValidationError(f"requirements must contain 1..{MAX_REQUIREMENTS} items")
    requirements: list[RequirementSpec] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_requirements):
        if not isinstance(raw, Mapping):
            raise RequirementModelValidationError(f"requirements[{index}] must be an object")
        spec = _validate_requirement(raw, tool_registry=tool_registry, known_requirement_ids=seen_ids)
        if spec.requirement_id in seen_ids:
            raise RequirementModelValidationError(f"duplicate requirement_id: {spec.requirement_id}")
        seen_ids.add(spec.requirement_id)
        requirements.append(spec)

    candidate_capabilities = _bounded_tool_name_list(
        payload.get("candidate_tool_capabilities", ()),
        "candidate_tool_capabilities",
        12,
    )
    for tool_name in candidate_capabilities:
        try:
            tool_registry.get(tool_name)
        except ToolExecutionPolicyError as exc:
            raise RequirementModelValidationError(
                f"unknown candidate tool capability: {tool_name}"
            ) from exc
    _bounded_text_list(
        payload.get("open_modeling_questions", ()),
        "open_modeling_questions",
        MAX_REQUIREMENTS,
    )
    if payload.get("confidence", "medium") not in {"low", "medium", "high"}:
        raise RequirementModelValidationError("confidence must be low, medium, or high")
    model_confidence = str(payload.get("model_confidence", payload.get("confidence", "medium")))
    if model_confidence not in {"low", "medium", "high"}:
        raise RequirementModelValidationError("model_confidence must be low, medium, or high")
    default_status = (
        TaskRequirementModelStatus.PROVISIONAL_BOOTSTRAP
        if task_kind == "generic_bootstrap"
        else TaskRequirementModelStatus.ACCEPTED_SPECIFIC
    )
    try:
        model_status = TaskRequirementModelStatus(str(payload.get("model_status", default_status.value)))
    except ValueError as exc:
        raise RequirementModelValidationError("invalid model_status") from exc
    bootstrap_reason_value = payload.get("bootstrap_reason")
    bootstrap_reason = (
        None
        if bootstrap_reason_value in (None, "")
        else _bounded_text(bootstrap_reason_value, "bootstrap_reason")
    )
    needs_remodeling = payload.get(
        "needs_remodeling",
        model_status != TaskRequirementModelStatus.ACCEPTED_SPECIFIC,
    )
    if not isinstance(needs_remodeling, bool):
        raise RequirementModelValidationError("needs_remodeling must be a boolean")
    if model_status == TaskRequirementModelStatus.PROVISIONAL_BOOTSTRAP and task_kind != "generic_bootstrap":
        raise RequirementModelValidationError("PROVISIONAL_BOOTSTRAP requires generic_bootstrap task_kind")

    requirement_ids = {spec.requirement_id for spec in requirements}
    for spec in requirements:
        _validate_activation_references(spec.activation, requirement_ids=requirement_ids)

    proposal_ref = source_proposal_ref or _bounded_ref(payload.get("source_proposal_ref"), "source_proposal_ref")
    context_hash = accepted_context_hash or _bounded_ref(payload.get("accepted_context_hash"), "accepted_context_hash")
    return TaskRequirementModel(
        model_id=model_id,
        model_version=model_version,
        task_kind=task_kind,
        task_summary=task_summary,
        task_components=components,
        requirements=tuple(requirements),
        success_criteria=success_criteria,
        applicable_stages=tuple(dict.fromkeys(stages)),
        source_proposal_ref=proposal_ref,
        accepted_context_hash=context_hash,
        model_status=model_status,
        model_confidence=model_confidence,
        bootstrap_reason=bootstrap_reason,
        needs_remodeling=needs_remodeling,
    )


def task_requirement_model_from_dict(payload: Mapping[str, Any], *, tool_registry: ToolRegistry) -> TaskRequirementModel:
    return validate_task_requirement_model(payload, tool_registry=tool_registry)


def validate_requirement_value(spec: RequirementSpec, value: Any) -> None:
    value_type = spec.value_type
    if value_type in {
        RequirementValueType.STRING,
        RequirementValueType.DATE,
        RequirementValueType.TIME_WINDOW,
        RequirementValueType.LOCATION,
        RequirementValueType.ENUM,
    }:
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
            raise RequirementModelValidationError(
                f"{spec.requirement_id} requires a bounded non-empty string"
            )
    elif value_type == RequirementValueType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise RequirementModelValidationError(f"{spec.requirement_id} requires an integer")
    elif value_type == RequirementValueType.NUMBER:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RequirementModelValidationError(f"{spec.requirement_id} requires a number")
    elif value_type == RequirementValueType.BOOLEAN:
        if not isinstance(value, bool):
            raise RequirementModelValidationError(f"{spec.requirement_id} requires a boolean")
    elif value_type == RequirementValueType.STRING_LIST:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise RequirementModelValidationError(f"{spec.requirement_id} requires a string list")
        if len(value) > MAX_ENUM_VALUES or any(
            not isinstance(item, str) or not item.strip() or len(item) > 120
            for item in value
        ):
            raise RequirementModelValidationError(f"{spec.requirement_id} contains an invalid string list")
        if not value and not spec.allow_explicit_empty:
            raise RequirementModelValidationError(f"{spec.requirement_id} does not allow an empty list")

    operator = str(spec.validation_rule.get("operator", "non_empty"))
    if operator == "positive_integer" and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
        raise RequirementModelValidationError(f"{spec.requirement_id} must be positive")
    if operator == "bounded_number":
        minimum = float(spec.validation_rule.get("minimum", 0))
        maximum = float(spec.validation_rule.get("maximum", 1_000_000))
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not minimum <= float(value) <= maximum:
            raise RequirementModelValidationError(f"{spec.requirement_id} is outside its numeric bounds")
    if operator == "one_of" and value not in spec.validation_rule.get("values", ()):
        raise RequirementModelValidationError(f"{spec.requirement_id} is not an allowed enum value")


def assess_requirements(*, model: TaskRequirementModel, ledger: SlotLedger) -> RequirementAssessment:
    missing_by_stage: dict[str, list[str]] = {stage.value: [] for stage in RequirementStage}
    user_gaps: list[str] = []
    tool_gaps: list[str] = []
    derived_system: list[str] = []
    ambiguous: list[str] = []
    conflicting: list[str] = []
    assumptions: list[str] = []
    values = ledger.value_map(resolved_only=False)

    for spec in sorted(model.requirements, key=lambda item: (item.priority, item.requirement_id)):
        if not activation_applies(spec.activation, components=model.task_components, values=values):
            continue
        record = ledger.records.get(spec.requirement_id)
        state = SlotState.UNKNOWN if record is None else record.state
        if state == SlotState.AMBIGUOUS:
            ambiguous.append(spec.requirement_id)
        elif state == SlotState.CONFLICTING:
            conflicting.append(spec.requirement_id)
        elif state == SlotState.DEFAULTED:
            assumptions.append(spec.requirement_id)

        if spec.source_route == RequirementSourceRoute.OPTIONAL or spec.required_at is None:
            continue
        if state == SlotState.RESOLVED:
            continue
        missing_by_stage[spec.required_at.value].append(spec.requirement_id)
        if spec.source_route == RequirementSourceRoute.USER:
            user_gaps.append(spec.requirement_id)
        elif spec.source_route == RequirementSourceRoute.TOOL:
            tool_gaps.append(spec.requirement_id)
        else:
            derived_system.append(spec.requirement_id)

    cumulative: dict[str, tuple[str, ...]] = {}
    seen: list[str] = []
    for stage in RequirementStage:
        for requirement_id in missing_by_stage[stage.value]:
            if requirement_id not in seen:
                seen.append(requirement_id)
        cumulative[stage.value] = tuple(seen)
    return RequirementAssessment(
        ready_for={stage.value: not cumulative[stage.value] for stage in RequirementStage},
        missing_by_stage=cumulative,
        user_gaps=tuple(dict.fromkeys(user_gaps)),
        tool_gaps=tuple(dict.fromkeys(tool_gaps)),
        derived_or_system_gaps=tuple(dict.fromkeys(derived_system)),
        ambiguous=tuple(dict.fromkeys(ambiguous)),
        conflicting=tuple(dict.fromkeys(conflicting)),
        assumptions=tuple(dict.fromkeys(assumptions)),
    )


def activation_applies(
    activation: Mapping[str, Any], *, components: Sequence[str], values: Mapping[str, Any]
) -> bool:
    operator = str(activation.get("operator", "always"))
    if operator == "always":
        return True
    if operator == "component_present":
        return str(activation.get("component", "")) in components
    if operator == "requirement_equals":
        return values.get(str(activation.get("requirement_id", ""))) == activation.get("value")
    clauses = activation.get("clauses", ())
    if operator == "all_of":
        return all(activation_applies(clause, components=components, values=values) for clause in clauses)
    if operator == "any_of":
        return any(activation_applies(clause, components=components, values=values) for clause in clauses)
    return False


def _validate_requirement(
    raw: Mapping[str, Any], *, tool_registry: ToolRegistry, known_requirement_ids: set[str]
) -> RequirementSpec:
    _reject_unknown_keys(
        raw,
        {
            "requirement_id", "label", "description", "value_type", "source_route",
            "required_at", "importance", "activation", "validation_rule", "question_group",
            "priority", "tool_bindings", "sensitive", "allow_explicit_empty",
        },
        "requirement",
    )
    requirement_id = _safe_token(raw.get("requirement_id"), "requirement_id")
    label = _bounded_text(raw.get("label"), f"{requirement_id}.label", maximum=80)
    description = _bounded_text(raw.get("description"), f"{requirement_id}.description")
    secret_text = f"{requirement_id} {label} {description}".lower()
    if bool(raw.get("sensitive", False)) or any(marker in secret_text for marker in SECRET_MARKERS):
        raise RequirementModelValidationError(f"sensitive requirement is forbidden: {requirement_id}")
    try:
        value_type = RequirementValueType(str(raw.get("value_type")))
        source_route = RequirementSourceRoute(str(raw.get("source_route")))
    except ValueError as exc:
        raise RequirementModelValidationError(f"invalid type or source route for {requirement_id}") from exc
    required_raw = raw.get("required_at")
    required_at = None if required_raw in (None, "") else RequirementStage(str(required_raw))
    if source_route == RequirementSourceRoute.OPTIONAL:
        required_at = None
    importance = str(raw.get("importance", "normal"))
    if importance not in {"critical", "high", "normal", "low"}:
        raise RequirementModelValidationError(f"invalid importance for {requirement_id}")
    activation = _validate_activation(raw.get("activation", {"operator": "always"}), depth=0)
    validation_rule = _validate_validation_rule(raw.get("validation_rule", {"operator": "non_empty"}))
    question_group = _safe_token(raw.get("question_group", "general"), "question_group")
    priority = _bounded_int(raw.get("priority", 100), "priority", minimum=0, maximum=10_000)
    bindings: list[ToolBinding] = []
    for binding_raw in _sequence(raw.get("tool_bindings", ()), "tool_bindings"):
        if not isinstance(binding_raw, Mapping):
            raise RequirementModelValidationError("tool binding must be an object")
        _reject_unknown_keys(binding_raw, {"tool_name", "argument_name"}, "tool binding")
        tool_name = str(binding_raw.get("tool_name", ""))
        argument_name = str(binding_raw.get("argument_name", ""))
        try:
            manifest = tool_registry.get(tool_name)
        except ToolExecutionPolicyError as exc:
            raise RequirementModelValidationError(f"unknown tool binding: {tool_name}") from exc
        if argument_name not in {*manifest.required_arguments, *manifest.optional_arguments}:
            raise RequirementModelValidationError(f"unknown argument {tool_name}:{argument_name}")
        bindings.append(ToolBinding(tool_name=tool_name, argument_name=argument_name))
    allow_empty = bool(raw.get("allow_explicit_empty", False))
    if allow_empty and value_type != RequirementValueType.STRING_LIST:
        raise RequirementModelValidationError("allow_explicit_empty requires string_list")
    if source_route == RequirementSourceRoute.TOOL and not bindings:
        raise RequirementModelValidationError(f"TOOL requirement requires a tool binding: {requirement_id}")
    if source_route != RequirementSourceRoute.TOOL and bindings:
        raise RequirementModelValidationError(
            f"only TOOL requirements may declare tool bindings: {requirement_id}"
        )
    return RequirementSpec(
        requirement_id=requirement_id,
        label=label,
        description=description,
        value_type=value_type,
        source_route=source_route,
        required_at=required_at,
        importance=importance,
        activation=activation,
        validation_rule=validation_rule,
        question_group=question_group,
        priority=priority,
        tool_bindings=tuple(bindings),
        sensitive=False,
        allow_explicit_empty=allow_empty,
    )


def _validate_activation(value: Any, *, depth: int) -> Mapping[str, Any]:
    if depth > MAX_NESTING_DEPTH or not isinstance(value, Mapping):
        raise RequirementModelValidationError("activation must be a bounded object")
    operator = str(value.get("operator", ""))
    if operator not in ACTIVATION_OPERATORS:
        raise RequirementModelValidationError(f"unsupported activation operator: {operator}")
    normalized: dict[str, Any] = {"operator": operator}
    if operator == "component_present":
        _reject_unknown_keys(value, {"operator", "component"}, "activation")
        normalized["component"] = _safe_token(value.get("component"), "activation.component")
    elif operator == "requirement_equals":
        _reject_unknown_keys(value, {"operator", "requirement_id", "value"}, "activation")
        normalized["requirement_id"] = _safe_token(value.get("requirement_id"), "activation.requirement_id")
        normalized["value"] = _bounded_scalar(value.get("value"), "activation.value")
    elif operator in {"all_of", "any_of"}:
        _reject_unknown_keys(value, {"operator", "clauses"}, "activation")
        clauses = _sequence(value.get("clauses", ()), "activation.clauses")
        if not 1 <= len(clauses) <= 8:
            raise RequirementModelValidationError("activation clauses must contain 1..8 items")
        normalized["clauses"] = [_validate_activation(clause, depth=depth + 1) for clause in clauses]
    else:
        _reject_unknown_keys(value, {"operator"}, "activation")
    return normalized


def _validate_activation_references(activation: Mapping[str, Any], *, requirement_ids: set[str]) -> None:
    if activation.get("operator") == "requirement_equals" and activation.get("requirement_id") not in requirement_ids:
        raise RequirementModelValidationError("activation references an unknown requirement")
    for clause in activation.get("clauses", ()):
        _validate_activation_references(clause, requirement_ids=requirement_ids)


def _validate_validation_rule(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RequirementModelValidationError("validation_rule must be an object")
    operator = str(value.get("operator", ""))
    if operator not in VALIDATION_OPERATORS:
        raise RequirementModelValidationError(f"unsupported validation operator: {operator}")
    normalized: dict[str, Any] = {"operator": operator}
    if operator == "bounded_number":
        _reject_unknown_keys(value, {"operator", "minimum", "maximum"}, "validation_rule")
        normalized["minimum"] = float(value.get("minimum", 0))
        normalized["maximum"] = float(value.get("maximum", 1_000_000))
        if normalized["minimum"] > normalized["maximum"]:
            raise RequirementModelValidationError("bounded_number minimum exceeds maximum")
    elif operator == "one_of":
        _reject_unknown_keys(value, {"operator", "values"}, "validation_rule")
        normalized["values"] = list(_bounded_text_list(value.get("values", ()), "one_of.values", MAX_ENUM_VALUES))
        if not normalized["values"]:
            raise RequirementModelValidationError("one_of values must not be empty")
    else:
        _reject_unknown_keys(value, {"operator"}, "validation_rule")
    return normalized


def _reject_unknown_keys(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise RequirementModelValidationError(
            f"{field} contains unsupported fields: {', '.join(sorted(unknown))}"
        )


def _bounded_mapping(value: Any, *, depth: int) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise RequirementModelValidationError("model payload nesting is too deep")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                raise RequirementModelValidationError("model payload contains an invalid key")
            _bounded_mapping(item, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) > 64:
            raise RequirementModelValidationError("model payload collection is too large")
        for item in value:
            _bounded_mapping(item, depth=depth + 1)
    elif isinstance(value, str) and len(value) > 2_000:
        raise RequirementModelValidationError("model payload string is too long")


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RequirementModelValidationError(f"{field} must be an array")
    return value


def _safe_token(value: Any, field: str) -> str:
    text = str(value or "")
    if not SAFE_TOKEN.fullmatch(text):
        raise RequirementModelValidationError(f"{field} must be a safe token")
    return text


def _safe_token_list(value: Any, field: str, limit: int) -> tuple[str, ...]:
    items = _sequence(value, field)
    if len(items) > limit:
        raise RequirementModelValidationError(f"{field} exceeds {limit} items")
    return tuple(dict.fromkeys(_safe_token(item, field) for item in items))


def _bounded_tool_name_list(value: Any, field: str, limit: int) -> tuple[str, ...]:
    items = _sequence(value, field)
    if len(items) > limit:
        raise RequirementModelValidationError(f"{field} exceeds {limit} items")
    result: list[str] = []
    for item in items:
        text = str(item)
        if not text or len(text) > 80 or any(character.isspace() for character in text):
            raise RequirementModelValidationError(f"{field} contains an invalid tool name")
        if text not in result:
            result.append(text)
    return tuple(result)


def _bounded_text(value: Any, field: str, maximum: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > maximum:
        raise RequirementModelValidationError(f"{field} must contain 1..{maximum} characters")
    return text


def _bounded_text_list(value: Any, field: str, limit: int) -> tuple[str, ...]:
    items = _sequence(value, field)
    if len(items) > limit:
        raise RequirementModelValidationError(f"{field} exceeds {limit} items")
    return tuple(_bounded_text(item, field, maximum=120) for item in items)


def _bounded_ref(value: Any, field: str) -> str:
    text = str(value or "")
    if not text or len(text) > 240 or any(character.isspace() for character in text):
        raise RequirementModelValidationError(f"{field} must be a bounded ref")
    return text


def _bounded_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise RequirementModelValidationError(f"{field} must be between {minimum} and {maximum}")
    return value


def _bounded_scalar(value: Any, field: str) -> str | int | float | bool:
    if not isinstance(value, (str, int, float, bool)) or isinstance(value, str) and len(value) > 120:
        raise RequirementModelValidationError(f"{field} must be a bounded scalar")
    return value


__all__ = [
    "RequirementAssessment",
    "RequirementModelValidationError",
    "RequirementSourceRoute",
    "RequirementSpec",
    "RequirementStage",
    "RequirementStatus",
    "RequirementValueType",
    "TaskRequirementModel",
    "TaskRequirementModelStatus",
    "ToolBinding",
    "activation_applies",
    "assess_requirements",
    "task_requirement_model_from_dict",
    "validate_task_requirement_model",
    "validate_requirement_value",
]
