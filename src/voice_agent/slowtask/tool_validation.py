from __future__ import annotations

"""Deterministic tool-gap resolution and argument compilation.

Models may propose a high-level binding id, but Python owns the binding
allowlist, accepted-fact projection, low-level arguments, provenance, and the
validation result that is allowed to reach Tool Executor.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Callable

from voice_agent.slowtask.requirement_model import TaskRequirementModel
from voice_agent.slowtask.slot_ledger import SlotLedger, SlotState
from voice_agent.tools.manifest import ToolExecutionPolicyError
from voice_agent.tools.registry import ToolRegistry


MAX_COMPILED_QUERY_LENGTH = 320
_SECRET_MARKERS = (
    "bearer ", "api_key", "api-key", "authorization=", "password=",
    "token=", "cookie=", "密钥", "令牌", "密码",
)


class ToolValidationStatus(str, Enum):
    PASS = "PASS"
    REPAIRABLE = "REPAIRABLE"
    BLOCKED = "BLOCKED"


class ToolValidationErrorSource(str, Enum):
    TOOL_BINDING = "TOOL_BINDING"
    ARGUMENT_COMPILER = "ARGUMENT_COMPILER"
    MANIFEST = "MANIFEST"
    PROVENANCE = "PROVENANCE"
    PLAN_VERSION = "PLAN_VERSION"
    AUTHORIZATION = "AUTHORIZATION"
    TOOL_EXECUTOR = "TOOL_EXECUTOR"


@dataclass(frozen=True)
class AllowedToolContract:
    binding_id: str
    task_kind: str
    tool_name: str
    resolver_id: str
    resolves_requirement_ids: tuple[str, ...]
    input_requirement_ids: tuple[str, ...]
    required_argument_names: tuple[str, ...]
    optional_argument_names: tuple[str, ...] = ()
    side_effect_class: str = "READ_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "tool_name": self.tool_name,
            "side_effect_class": self.side_effect_class,
            "required_arguments": list(self.required_argument_names),
            "optional_arguments": list(self.optional_argument_names),
            "resolves_requirement_ids": list(self.resolves_requirement_ids),
            "resolver_id": self.resolver_id,
            "input_requirement_ids": list(self.input_requirement_ids),
        }


@dataclass(frozen=True)
class AcceptedFactInput:
    requirement_id: str
    value: Any
    source_evidence_refs: tuple[str, ...]
    plan_version: int

    def to_safe_dict(self) -> dict[str, Any]:
        """Return metadata safe for a shareable trace (never the user value)."""

        return {
            "requirement_id": self.requirement_id,
            "source_evidence_refs": list(self.source_evidence_refs),
            "plan_version": self.plan_version,
            "value_present": self.value not in (None, "", []),
        }


@dataclass(frozen=True)
class CompiledToolCall:
    contract: AllowedToolContract
    arguments: Mapping[str, Any]
    argument_source_evidence_refs: Mapping[str, tuple[str, ...]]
    argument_fingerprint: str


@dataclass(frozen=True)
class ToolValidationReport:
    report_id: str
    task_id: str
    plan_version: int
    tool_name: str
    binding_id: str
    resolves_requirement_ids: tuple[str, ...]
    status: ToolValidationStatus
    required_arguments: tuple[str, ...]
    provided_arguments: tuple[str, ...]
    unknown_arguments: tuple[str, ...]
    missing_arguments: tuple[str, ...]
    invalid_argument_types: tuple[str, ...]
    missing_provenance: tuple[str, ...]
    binding_errors: tuple[str, ...]
    reason_codes: tuple[str, ...]
    recoverable: bool
    caused_by_proposal_ref: str | None
    error_source: ToolValidationErrorSource | None = None
    manifest_name: str | None = None
    manifest_version: str | None = None
    argument_fingerprint: str | None = None
    repair_attempted: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Safe report projection: argument values are intentionally absent."""

        return {
            "report_id": self.report_id,
            "task_id": self.task_id,
            "plan_version": self.plan_version,
            "tool_name": self.tool_name,
            "binding_id": self.binding_id,
            "resolves_requirement_ids": list(self.resolves_requirement_ids),
            "status": self.status.value,
            "required_arguments": list(self.required_arguments),
            "provided_arguments": list(self.provided_arguments),
            "unknown_arguments": list(self.unknown_arguments),
            "missing_arguments": list(self.missing_arguments),
            "invalid_argument_types": list(self.invalid_argument_types),
            "missing_provenance": list(self.missing_provenance),
            "binding_errors": list(self.binding_errors),
            "reason_codes": list(self.reason_codes),
            "recoverable": self.recoverable,
            "caused_by_proposal_ref": self.caused_by_proposal_ref,
            "error_source": self.error_source.value if self.error_source is not None else None,
            "manifest_name": self.manifest_name,
            "manifest_version": self.manifest_version,
            "argument_fingerprint": self.argument_fingerprint,
            "repair_attempted": self.repair_attempted,
        }


Resolver = Callable[[AllowedToolContract, Mapping[str, AcceptedFactInput]], Mapping[str, Any]]


class ToolArgumentResolverRegistry:
    def __init__(self, resolvers: Mapping[str, Resolver] | None = None) -> None:
        self._resolvers = dict(resolvers or {"place_search_query_v1": _place_search_query_v1})

    def resolve(
        self,
        contract: AllowedToolContract,
        facts: Mapping[str, AcceptedFactInput],
    ) -> Mapping[str, Any]:
        try:
            resolver = self._resolvers[contract.resolver_id]
        except KeyError as exc:
            raise ValueError("resolver_not_allowlisted") from exc
        return resolver(contract, facts)


class ToolGapResolver:
    """Maps active TOOL requirements to Python-registered contracts only."""

    def __init__(self, contracts: Sequence[AllowedToolContract] | None = None) -> None:
        self._contracts = tuple(contracts or builtin_allowed_tool_contracts())

    def allowed_contracts(
        self,
        *,
        model: TaskRequirementModel,
        active_tool_gap_ids: Sequence[str],
        tool_registry: ToolRegistry,
    ) -> tuple[AllowedToolContract, ...]:
        active = set(str(value) for value in active_tool_gap_ids)
        model_specs = model.requirements_by_id
        allowed: list[AllowedToolContract] = []
        for contract in self._contracts:
            if contract.task_kind != model.task_kind:
                continue
            if not set(contract.resolves_requirement_ids).issubset(active):
                continue
            if any(requirement_id not in model_specs for requirement_id in contract.resolves_requirement_ids):
                continue
            try:
                manifest = tool_registry.get(contract.tool_name)
            except ToolExecutionPolicyError:
                continue
            if (
                tuple(manifest.required_arguments) != contract.required_argument_names
                or tuple(manifest.optional_arguments) != contract.optional_argument_names
                or manifest.side_effect_class != contract.side_effect_class
            ):
                continue
            declared = {
                (binding.tool_name, binding.argument_name)
                for requirement_id in contract.resolves_requirement_ids
                for binding in model_specs[requirement_id].tool_bindings
            }
            if any((contract.tool_name, name) not in declared for name in contract.required_argument_names):
                continue
            allowed.append(contract)
        return tuple(sorted(allowed, key=lambda item: item.binding_id))


class ToolArgumentCompiler:
    def __init__(self, resolver_registry: ToolArgumentResolverRegistry | None = None) -> None:
        self._resolvers = resolver_registry or ToolArgumentResolverRegistry()

    def compile(
        self,
        *,
        task_id: str,
        plan_version: int,
        contract: AllowedToolContract,
        accepted_fact_inputs: Sequence[AcceptedFactInput],
        tool_registry: ToolRegistry,
        caused_by_proposal_ref: str | None = None,
        diagnostic_candidate: Mapping[str, Any] | None = None,
    ) -> tuple[CompiledToolCall | None, ToolValidationReport]:
        facts = {fact.requirement_id: fact for fact in accepted_fact_inputs}
        binding_errors: list[str] = []
        reason_codes: list[str] = []
        repair_attempted = False
        if diagnostic_candidate:
            candidate_tool = str(diagnostic_candidate.get("tool_name", ""))
            candidate_arguments = diagnostic_candidate.get("arguments", {})
            if candidate_tool != contract.tool_name:
                binding_errors.append("diagnostic_tool_binding_mismatch")
                reason_codes.append("RAW_PROPOSAL_TOOL_IGNORED")
                repair_attempted = True
            if not isinstance(candidate_arguments, Mapping) or set(candidate_arguments) != set(contract.required_argument_names):
                reason_codes.append("RAW_PROPOSAL_ARGUMENTS_REBUILT")
                repair_attempted = True

        try:
            manifest = tool_registry.get(contract.tool_name)
        except ToolExecutionPolicyError:
            return None, self._blocked_report(
                task_id=task_id,
                plan_version=plan_version,
                contract=contract,
                reason_codes=(*reason_codes, "TOOL_MANIFEST_NOT_FOUND"),
                binding_errors=tuple(binding_errors),
                caused_by_proposal_ref=caused_by_proposal_ref,
                error_source=ToolValidationErrorSource.MANIFEST,
                repair_attempted=repair_attempted,
            )

        try:
            arguments = dict(self._resolvers.resolve(contract, facts))
        except ValueError as exc:
            return None, self._blocked_report(
                task_id=task_id,
                plan_version=plan_version,
                contract=contract,
                reason_codes=(*reason_codes, str(exc)),
                binding_errors=tuple(binding_errors),
                caused_by_proposal_ref=caused_by_proposal_ref,
                error_source=ToolValidationErrorSource.ARGUMENT_COMPILER,
                manifest_name=manifest.tool_name,
                manifest_version=manifest.tool_manifest_version,
                repair_attempted=repair_attempted,
            )

        provided = tuple(sorted(arguments))
        allowed_names = {*manifest.required_arguments, *manifest.optional_arguments}
        unknown = tuple(sorted(set(arguments) - allowed_names))
        missing = tuple(name for name in manifest.required_arguments if arguments.get(name) in (None, ""))
        invalid_types = tuple(
            name for name, value in arguments.items()
            if name == "query" and (not isinstance(value, str) or not value.strip())
        )
        provenance: dict[str, tuple[str, ...]] = {}
        all_refs = tuple(dict.fromkeys(
            ref
            for requirement_id in contract.input_requirement_ids
            for ref in facts.get(
                requirement_id,
                AcceptedFactInput(requirement_id, None, (), plan_version),
            ).source_evidence_refs
        ))
        for name in arguments:
            provenance[name] = all_refs
        missing_provenance = tuple(
            name for name in manifest.argument_provenance_requirements if not provenance.get(name)
        )
        fingerprint = _fingerprint(arguments)
        status = (
            ToolValidationStatus.BLOCKED
            if unknown or missing or invalid_types or missing_provenance
            else ToolValidationStatus.PASS
        )
        if status == ToolValidationStatus.PASS:
            reason_codes.append("DETERMINISTIC_ARGUMENT_COMPILATION_PASS")
            if repair_attempted:
                reason_codes.append("DETERMINISTIC_REPAIR_APPLIED")
        else:
            if missing:
                reason_codes.append("MISSING_REQUIRED_ARGUMENT")
            if unknown:
                reason_codes.append("UNKNOWN_ARGUMENT")
            if invalid_types:
                reason_codes.append("INVALID_ARGUMENT_TYPE")
            if missing_provenance:
                reason_codes.append("MISSING_ARGUMENT_PROVENANCE")
        report = ToolValidationReport(
            report_id=_report_id(task_id, plan_version, contract.binding_id, fingerprint, reason_codes),
            task_id=task_id,
            plan_version=plan_version,
            tool_name=contract.tool_name,
            binding_id=contract.binding_id,
            resolves_requirement_ids=contract.resolves_requirement_ids,
            status=status,
            required_arguments=tuple(manifest.required_arguments),
            provided_arguments=provided,
            unknown_arguments=unknown,
            missing_arguments=missing,
            invalid_argument_types=invalid_types,
            missing_provenance=missing_provenance,
            binding_errors=tuple(binding_errors),
            reason_codes=tuple(dict.fromkeys(reason_codes)),
            recoverable=status != ToolValidationStatus.PASS,
            caused_by_proposal_ref=caused_by_proposal_ref,
            error_source=(
                None
                if status == ToolValidationStatus.PASS
                else ToolValidationErrorSource.PROVENANCE
                if missing_provenance
                else ToolValidationErrorSource.ARGUMENT_COMPILER
            ),
            manifest_name=manifest.tool_name,
            manifest_version=manifest.tool_manifest_version,
            argument_fingerprint=fingerprint,
            repair_attempted=repair_attempted,
        )
        if status != ToolValidationStatus.PASS:
            return None, report
        return (
            CompiledToolCall(
                contract=contract,
                arguments=arguments,
                argument_source_evidence_refs=provenance,
                argument_fingerprint=fingerprint,
            ),
            report,
        )

    @staticmethod
    def _blocked_report(
        *,
        task_id: str,
        plan_version: int,
        contract: AllowedToolContract,
        reason_codes: Sequence[str],
        binding_errors: tuple[str, ...],
        caused_by_proposal_ref: str | None,
        error_source: ToolValidationErrorSource,
        manifest_name: str | None = None,
        manifest_version: str | None = None,
        repair_attempted: bool = False,
    ) -> ToolValidationReport:
        return ToolValidationReport(
            report_id=_report_id(task_id, plan_version, contract.binding_id, "blocked", reason_codes),
            task_id=task_id,
            plan_version=plan_version,
            tool_name=contract.tool_name,
            binding_id=contract.binding_id,
            resolves_requirement_ids=contract.resolves_requirement_ids,
            status=ToolValidationStatus.BLOCKED,
            required_arguments=contract.required_argument_names,
            provided_arguments=(),
            unknown_arguments=(),
            missing_arguments=contract.required_argument_names,
            invalid_argument_types=(),
            missing_provenance=contract.required_argument_names,
            binding_errors=binding_errors,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
            recoverable=True,
            caused_by_proposal_ref=caused_by_proposal_ref,
            error_source=error_source,
            manifest_name=manifest_name,
            manifest_version=manifest_version,
            repair_attempted=repair_attempted,
        )


def accepted_fact_inputs_from_ledger(
    *,
    ledger: SlotLedger,
    requirement_ids: Sequence[str],
    stale_evidence_refs: Sequence[str] = (),
) -> tuple[AcceptedFactInput, ...]:
    stale = set(str(ref) for ref in stale_evidence_refs)
    result: list[AcceptedFactInput] = []
    for requirement_id in requirement_ids:
        record = ledger.records.get(requirement_id)
        if record is None or record.state != SlotState.RESOLVED or not record.provenance:
            continue
        latest = record.provenance[-1]
        # SlotEvidence intentionally has no value.  Only the latest evidence
        # owns the currently selected value; retaining older provenance here
        # would reintroduce a superseded fact after a patch.
        refs = () if latest.evidence_ref in stale else (latest.evidence_ref,)
        if not refs:
            continue
        result.append(
            AcceptedFactInput(
                requirement_id=requirement_id,
                value=record.normalized_value,
                source_evidence_refs=refs,
                plan_version=latest.plan_version,
            )
        )
    return tuple(result)


def builtin_allowed_tool_contracts() -> tuple[AllowedToolContract, ...]:
    return (
        AllowedToolContract(
            binding_id="venue_options:webSearch:query",
            task_kind="customer_reception",
            tool_name="webSearch",
            resolver_id="place_search_query_v1",
            resolves_requirement_ids=("venue_options",),
            input_requirement_ids=(
                "location_anchor", "cuisine_preference", "party_size", "budget",
                "dietary_constraints", "time_window",
            ),
            required_argument_names=("query",),
        ),
        AllowedToolContract(
            binding_id="venue_options:webSearch:query",
            task_kind="team_event",
            tool_name="webSearch",
            resolver_id="place_search_query_v1",
            resolves_requirement_ids=("venue_options",),
            input_requirement_ids=(
                "city", "party_size", "budget", "activity_preferences", "event_date",
            ),
            required_argument_names=("query",),
        ),
        AllowedToolContract(
            binding_id="source_coverage:webSearch:query",
            task_kind="research_report",
            tool_name="webSearch",
            resolver_id="place_search_query_v1",
            resolves_requirement_ids=("source_coverage",),
            input_requirement_ids=("research_scope", "target_audience", "output_format"),
            required_argument_names=("query",),
        ),
    )


def _place_search_query_v1(
    contract: AllowedToolContract,
    facts: Mapping[str, AcceptedFactInput],
) -> Mapping[str, Any]:
    parts: list[str] = []
    for requirement_id in contract.input_requirement_ids:
        fact = facts.get(requirement_id)
        if fact is None or fact.value in (None, "", []):
            continue
        value = fact.value
        if requirement_id == "party_size" and isinstance(value, int) and not isinstance(value, bool):
            rendered = f"{value}人"
        elif requirement_id == "budget" and isinstance(value, (int, float)) and not isinstance(value, bool):
            rendered = f"人均{value:g}以内" if isinstance(value, float) else f"人均{value}以内"
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            rendered = " ".join(str(item).strip() for item in value if str(item).strip())
        else:
            rendered = str(value).strip()
        if rendered and rendered not in {"UNKNOWN", "STALE", "unknown", "stale"}:
            parts.append(rendered)
    if not parts:
        raise ValueError("NO_ACCEPTED_FACTS_FOR_RESOLVER")
    suffix = "餐厅" if contract.task_kind == "customer_reception" else "场地" if contract.task_kind == "team_event" else "资料"
    query = " ".join((*parts, suffix))[:MAX_COMPILED_QUERY_LENGTH].strip()
    lowered = query.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError("SECRET_LIKE_VALUE_BLOCKED")
    return {"query": query}


def _fingerprint(arguments: Mapping[str, Any]) -> str:
    encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _report_id(
    task_id: str,
    plan_version: int,
    binding_id: str,
    fingerprint: str,
    reasons: Sequence[str],
) -> str:
    encoded = json.dumps(
        [task_id, plan_version, binding_id, fingerprint, list(reasons)],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "tool_validation_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


__all__ = [
    "AcceptedFactInput",
    "AllowedToolContract",
    "CompiledToolCall",
    "ToolArgumentCompiler",
    "ToolArgumentResolverRegistry",
    "ToolGapResolver",
    "ToolValidationErrorSource",
    "ToolValidationReport",
    "ToolValidationStatus",
    "accepted_fact_inputs_from_ledger",
    "builtin_allowed_tool_contracts",
]
