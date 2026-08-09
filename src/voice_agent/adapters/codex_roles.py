from __future__ import annotations

"""Role-specific, proposal-only Codex contracts for SlowTask planning."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
import asyncio
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from voice_agent.runtime.adapter_callback_boundary import AdapterCallbackAppendBoundary
from voice_agent.slowtask.requirement_model import (
    RequirementModelValidationError,
    RequirementSourceRoute,
    TaskRequirementModel,
    task_requirement_model_from_dict,
    validate_task_requirement_model,
    validate_requirement_value,
)
from voice_agent.slowtask.slot_ledger import SlotState
from voice_agent.slowtask.task_profiles import (
    TaskProfileRegistry,
    builtin_task_profile_registry,
    extract_profile_updates,
)
from voice_agent.tools.manifest import ToolExecutionPolicyError
from voice_agent.tools.registry import ToolRegistry


ROLE_CONTRACT_VERSION = "voice_agent.codex_role.v1"
ROLE_ADAPTER_ID = "codex_role_adapter"
ROLE_SCHEMA_PREFIX = "voice_agent.slowtask.role"
MAX_PUBLIC_SUMMARY = 320
MAX_QUESTIONS = 3


class CodexRole(str, Enum):
    TASK_MODELER = "TASK_MODELER"
    REQUIREMENT_ANALYST = "REQUIREMENT_ANALYST"
    CLARIFIER = "CLARIFIER"
    PLANNER = "PLANNER"
    REVIEWER = "REVIEWER"


ROLE_TIMEOUT_SECONDS: Mapping[CodexRole, int] = {
    CodexRole.TASK_MODELER: 60,
    CodexRole.REQUIREMENT_ANALYST: 45,
    CodexRole.CLARIFIER: 30,
    CodexRole.PLANNER: 60,
    CodexRole.REVIEWER: 45,
}


class CodexRoleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class RoleInvocationResult:
    role: CodexRole
    proposal: Mapping[str, Any]
    proposal_ref: str
    structured_output_event: Mapping[str, Any]
    validation_failed_event: Mapping[str, Any] | None
    validation_status: str
    degraded_reason: str | None
    trace_item: Mapping[str, Any]


ProviderInvoker = Callable[[CodexRole, Mapping[str, Any], Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


class CodexRoleAdapter:
    """Invokes one bounded role at a time; it never owns SlowTask state."""

    def __init__(
        self,
        *,
        boundary: AdapterCallbackAppendBoundary,
        tool_registry: ToolRegistry,
        provider_mode: str = "fake",
        provider_invoker: ProviderInvoker | None = None,
        fake_profiles: TaskProfileRegistry | None = None,
    ) -> None:
        self._boundary = boundary
        self._tool_registry = tool_registry
        self._provider_mode = provider_mode
        self._provider_invoker = provider_invoker
        self._profiles = fake_profiles or builtin_task_profile_registry()

    def fake_task_kind_for_intent(self, intent: str) -> str | None:
        profile = self._profiles.select_fake_fixture(intent)
        return profile.profile_id if profile is not None else None

    async def invoke(
        self,
        *,
        role: CodexRole,
        task_context_pack: Mapping[str, Any],
        role_context: Mapping[str, Any],
        task_binding: Mapping[str, Any],
        source_evidence_refs: Sequence[str],
        event_id_prefix: str,
        caused_by_event_id: str,
        created_monotonic_ms: int,
        created_wall_clock_ms: int,
    ) -> RoleInvocationResult:
        context_projection = minimal_role_context(role, task_context_pack, role_context)
        context_hash = _context_hash(context_projection)
        request_id = f"{event_id_prefix}_{role.value.lower()}_request"
        output_mode = "fake"
        degraded_reason: str | None = None
        if self._provider_invoker is not None:
            try:
                raw = await asyncio.wait_for(
                    self._provider_invoker(role, context_projection, role_output_schema(role)),
                    timeout=ROLE_TIMEOUT_SECONDS[role],
                )
                output_mode = "real"
            except (TimeoutError, OSError, ValueError, RuntimeError) as exc:
                raw = self._degraded_output(
                    role, context_projection, task_binding, source_evidence_refs, context_hash
                )
                output_mode = "degraded"
                degraded_reason = _safe_provider_failure_reason(exc)
        elif self._provider_mode == "fake":
            raw = self._fake_output(role, context_projection, task_binding, source_evidence_refs, context_hash)
        else:
            raw = self._degraded_output(role, context_projection, task_binding, source_evidence_refs, context_hash)
            output_mode = "degraded"
            degraded_reason = "role_provider_unavailable"

        proposal = _normalize_envelope(
            raw,
            role=role,
            task_binding=task_binding,
            source_evidence_refs=source_evidence_refs,
            input_context_hash=context_hash,
        )
        validation_failed: Mapping[str, Any] | None = None
        try:
            validate_role_proposal(
                proposal,
                role=role,
                role_context=role_context,
                tool_registry=self._tool_registry,
                expected_task_binding=task_binding,
                expected_context_hash=context_hash,
                expected_source_evidence_refs=source_evidence_refs,
            )
            validation_status = "validated" if degraded_reason is None else "degraded"
        except CodexRoleValidationError as exc:
            validation_failed = self._boundary.append_adapter_event(
                event_name="ADAPTER_OUTPUT_VALIDATION_FAILED",
                event_id=f"{event_id_prefix}_{role.value.lower()}_validation_failed",
                source_module="slow_llm_role_adapter",
                caused_by_event_id=caused_by_event_id,
                created_monotonic_ms=created_monotonic_ms,
                created_wall_clock_ms=created_wall_clock_ms,
                trace_redaction_level="metadata_only",
                adapter_id=ROLE_ADAPTER_ID,
                adapter_type="slow_llm",
                adapter_request_id=request_id,
                task_id=str(task_binding["task_id"]),
                plan_version=int(task_binding["plan_version"]),
                task_event_seq=int(task_binding["task_event_seq"]),
                schema_name=_schema_name(role),
                failure_reasons=[str(exc)[:160]],
                output_mode="degraded",
                role=role.value,
            )
            proposal = self._fallback_after_validation_failure(
                role, context_projection, task_binding, source_evidence_refs, context_hash, str(exc)
            )
            validate_role_proposal(
                proposal,
                role=role,
                role_context=role_context,
                tool_registry=self._tool_registry,
                expected_task_binding=task_binding,
                expected_context_hash=context_hash,
                expected_source_evidence_refs=source_evidence_refs,
            )
            validation_status = "degraded"
            degraded_reason = "role_output_validation_failed"

        proposal_ref = f"proposal://codex-role/{proposal['proposal_id']}"
        structured_event = self._boundary.append_adapter_event(
            event_name="SLOW_LLM_STRUCTURED_OUTPUT_EMITTED",
            event_id=f"{event_id_prefix}_{role.value.lower()}_structured_output",
            source_module="slow_llm_role_adapter",
            caused_by_event_id=caused_by_event_id,
            created_monotonic_ms=created_monotonic_ms + 1,
            created_wall_clock_ms=created_wall_clock_ms + 1,
            trace_redaction_level="metadata_only",
            adapter_id=ROLE_ADAPTER_ID,
            adapter_type="slow_llm",
            adapter_request_id=request_id,
            task_id=str(task_binding["task_id"]),
            plan_version=int(task_binding["plan_version"]),
            task_event_seq=int(task_binding["task_event_seq"]),
            schema_name=_schema_name(role),
            normalization_status="normalized",
            slow_llm_output_ref=f"output://codex-role/{request_id}",
            structured_output_ref=proposal_ref,
            validation_result_ref=f"validation://codex-role/{proposal['proposal_id']}/{validation_status}",
            output_mode=output_mode if validation_status == "validated" else "degraded",
            role=role.value,
            proposal_id=str(proposal["proposal_id"]),
            public_summary=str(proposal["public_summary"]),
            input_context_hash=context_hash,
            validation_status=validation_status,
            degraded_reason=degraded_reason,
            planning_mode=(
                str(proposal["payload"].get("planning_mode"))
                if role == CodexRole.PLANNER
                else None
            ),
            reviewer_verdict=(
                str(proposal["payload"].get("verdict"))
                if role == CodexRole.REVIEWER
                else None
            ),
            proposal_only=True,
        )
        trace_item = {
            "kind": "role_invocation",
            "status": validation_status,
            "phase": "codex_role",
            "role": role.value,
            "proposal_id": str(proposal["proposal_id"]),
            "orchestration_role": role.value,
            "proposal_only": True,
            "context_hash": context_hash,
            "task_id": str(task_binding["task_id"]),
            "plan_version": int(task_binding["plan_version"]),
            "public_summary": str(proposal["public_summary"]),
            "validation_status": validation_status,
            "degraded_reason": degraded_reason,
            "blocked_on_user": role == CodexRole.CLARIFIER,
        }
        return RoleInvocationResult(
            role=role,
            proposal=proposal,
            proposal_ref=proposal_ref,
            structured_output_event=structured_event,
            validation_failed_event=validation_failed,
            validation_status=validation_status,
            degraded_reason=degraded_reason,
            trace_item=trace_item,
        )

    def _fake_output(
        self,
        role: CodexRole,
        context: Mapping[str, Any],
        task_binding: Mapping[str, Any],
        refs: Sequence[str],
        context_hash: str,
    ) -> Mapping[str, Any]:
        if role == CodexRole.TASK_MODELER:
            intent = str(context.get("goal", ""))
            profile = self._profiles.select_fake_fixture(intent) or self._profiles.generic_bootstrap()
            payload = json.loads(json.dumps(profile.model_payload))
            if profile.profile_id == "customer_reception" and not any(
                marker in intent for marker in ("午饭", "午餐", "晚饭", "晚餐", "用餐", "餐厅", "吃饭", "宴请", "菜", "忌口", "包间")
            ):
                payload["task_components"] = [component for component in payload["task_components"] if component != "meal"]
            payload["source_proposal_ref"] = f"proposal://fake/{profile.profile_id}"
            payload["accepted_context_hash"] = context_hash
            payload["candidate_tool_capabilities"] = sorted(
                {
                    binding["tool_name"]
                    for requirement in payload["requirements"]
                    for binding in requirement.get("tool_bindings", ())
                }
            )
            payload["open_modeling_questions"] = []
            payload["confidence"] = "high"
            summary = f"识别为 {profile.profile_id}，生成 {len(payload['requirements'])} 项候选 requirement。"
        elif role == CodexRole.REQUIREMENT_ANALYST:
            model_payload = context.get("task_requirement_model", {})
            profile_id = str(model_payload.get("task_kind", "generic_bootstrap"))
            source_routes = {
                str(item.get("requirement_id")): str(item.get("source_route"))
                for item in model_payload.get("requirements", ())
                if isinstance(item, Mapping)
            }
            updates = extract_profile_updates(
                profile_id=profile_id,
                text=str(context.get("evidence_text", "")),
                evidence_ref=str(refs[-1]) if refs else "evidence://none",
                plan_version=int(task_binding["plan_version"]),
                source="role_requirement_analyst",
            )
            payload = {
                "requirements": [
                    {
                        "requirement_id": update.name,
                        "proposed_status": update.state.value,
                        "proposed_value": update.normalized_value,
                        "resolution_route": source_routes.get(update.name, "USER"),
                        "source_evidence_refs": [update.evidence_ref],
                        "confidence": "high",
                        "ambiguity_reason": "underspecified" if update.state == SlotState.AMBIGUOUS else None,
                        "conflict_candidates": list(update.conflicting_candidates),
                        "blocks_stage": None,
                        "materially_changes_task": False,
                    }
                    for update in updates
                ]
            }
            summary = f"提出 {len(updates)} 项 requirement 状态更新；最终状态由 SlowTask 校验。"
        elif role == CodexRole.CLARIFIER:
            selected = tuple(str(value) for value in context.get("selected_requirement_ids", ()))
            labels = context.get("selected_labels", {})
            payload = {
                "clarification_id": f"clarification_{task_binding['task_id']}_{task_binding['task_event_seq']}",
                "question_text": _fallback_question(selected, labels),
                "covered_requirement_ids": list(selected),
                "expected_answer_shape": "请按问题给出简短、明确的条件。",
                "optional_examples": [],
            }
            summary = payload["question_text"]
        elif role == CodexRole.PLANNER:
            tool_gaps = tuple(str(value) for value in context.get("tool_gap_ids", ()))
            model = context.get("task_requirement_model", {})
            specs = {item["requirement_id"]: item for item in model.get("requirements", ())}
            proposed_calls: list[dict[str, Any]] = []
            for requirement_id in tool_gaps[:1]:
                bindings = specs.get(requirement_id, {}).get("tool_bindings", ())
                if bindings:
                    binding = bindings[0]
                    accepted_facts = context.get("accepted_facts", {})
                    query_parts = (
                        [str(value) for value in accepted_facts.values() if value not in (None, "", [])]
                        if isinstance(accepted_facts, Mapping)
                        else []
                    )
                    query = " ".join(query_parts) or str(
                        context.get("task_summary", model.get("task_summary", "task research"))
                    )
                    proposed_calls.append(
                        {
                            "tool_name": binding["tool_name"],
                            "arguments": {binding["argument_name"]: query[:320]},
                            "argument_provenance": {binding["argument_name"]: str(refs[-1]) if refs else "evidence://accepted-model"},
                            "resolves_requirement_ids": [requirement_id],
                        }
                    )
            mode = "INFORMATION_GATHERING" if proposed_calls else "FINAL_PLAN_CANDIDATE"
            criteria = list(model.get("success_criteria", ()))
            payload = {
                "planning_mode": mode,
                "plan_summary": f"为 {model.get('task_summary', '当前任务')} 生成受约束的计划候选。",
                "ordered_steps": [f"步骤 {index + 1}：{criterion}" for index, criterion in enumerate(criteria)] or ["确认目标和约束", "形成可验证的执行步骤"],
                "requirement_coverage": list(context.get("resolved_requirement_ids", ())),
                "proposed_tool_calls": proposed_calls,
                "unresolved_optional_items": [],
                "assumptions": [],
                "risks": [],
                "requires_confirmation": False,
                "source_evidence_refs": list(refs),
            }
            summary = payload["plan_summary"]
        else:
            plan = context.get("plan_proposal", {})
            payload = {
                "verdict": "PASS",
                "violations": [],
                "missing_requirement_coverage": [],
                "unsupported_claims": [],
                "stale_evidence_usage": [],
                "tool_binding_errors": [],
                "premature_commitment": False,
                "risk_notes": [],
            }
            if not plan:
                payload["verdict"] = "BLOCK"
                payload["violations"] = ["missing_plan_proposal"]
            summary = "Reviewer 已通过计划候选。" if payload["verdict"] == "PASS" else "Reviewer 阻止缺失的计划候选。"
        return _envelope(role, task_binding, refs, context_hash, payload, summary)

    def _degraded_output(self, role: CodexRole, context: Mapping[str, Any], task_binding: Mapping[str, Any], refs: Sequence[str], context_hash: str) -> Mapping[str, Any]:
        if role == CodexRole.TASK_MODELER:
            semantic_input = " ".join(
                str(context.get(key, ""))
                for key in ("goal", "latest_user_input", "accepted_evidence_summary")
            )
            profile = self._profiles.select_fake_fixture(semantic_input) or self._profiles.generic_bootstrap()
            payload = json.loads(json.dumps(profile.model_payload))
            payload["source_proposal_ref"] = f"proposal://degraded/{profile.profile_id}"
            payload["accepted_context_hash"] = context_hash
            payload["candidate_tool_capabilities"] = sorted(
                {
                    binding["tool_name"]
                    for requirement in payload["requirements"]
                    for binding in requirement.get("tool_bindings", ())
                }
            )
            payload["open_modeling_questions"] = (
                ["desired_deliverable", "key_constraint"]
                if profile.profile_id == "generic_bootstrap"
                else []
            )
            payload["confidence"] = "low"
            result = _envelope(
                role,
                task_binding,
                refs,
                context_hash,
                payload,
                (
                    "模型服务不可用，使用受限的通用澄清模型。"
                    if profile.profile_id == "generic_bootstrap"
                    else f"模型服务不可用，使用隔离的 {profile.profile_id} 确定性降级模型。"
                ),
            )
            result["confidence"] = "low"
            return result
        result = dict(self._fake_output(role, context, task_binding, refs, context_hash))
        result["confidence"] = "low"
        return result

    def _fallback_after_validation_failure(self, role: CodexRole, role_context: Mapping[str, Any], task_binding: Mapping[str, Any], refs: Sequence[str], context_hash: str, reason: str) -> Mapping[str, Any]:
        if role == CodexRole.CLARIFIER:
            selected = tuple(str(value) for value in role_context.get("selected_requirement_ids", ()))
            labels = role_context.get("selected_labels", {})
            payload = {
                "clarification_id": f"clarification_fallback_{task_binding['task_id']}",
                "question_text": _fallback_question(selected, labels),
                "covered_requirement_ids": list(selected),
                "expected_answer_shape": "请提供对应条件。",
                "optional_examples": [],
            }
            return _envelope(role, task_binding, refs, context_hash, payload, payload["question_text"])
        if role == CodexRole.REVIEWER:
            payload = {
                "verdict": "BLOCK",
                "violations": ["review_output_validation_failed"],
                "missing_requirement_coverage": [],
                "unsupported_claims": [],
                "stale_evidence_usage": [],
                "tool_binding_errors": [],
                "premature_commitment": False,
                "risk_notes": [reason[:120]],
            }
            return _envelope(role, task_binding, refs, context_hash, payload, "Reviewer 输出无效，已按 BLOCK 处理。")
        if role == CodexRole.PLANNER:
            payload = {
                "planning_mode": "DRAFT_PLAN",
                "plan_summary": "Planner 输出无效，未形成可提交方案。",
                "ordered_steps": [],
                "requirement_coverage": [],
                "proposed_tool_calls": [],
                "unresolved_optional_items": [],
                "assumptions": [],
                "risks": ["planner_output_validation_failed"],
                "requires_confirmation": False,
                "source_evidence_refs": list(refs),
            }
            return _envelope(role, task_binding, refs, context_hash, payload, payload["plan_summary"])
        if role == CodexRole.REQUIREMENT_ANALYST:
            return self._fake_output(role, role_context, task_binding, refs, context_hash)
        return self._fake_output(role, role_context, task_binding, refs, context_hash)


def role_output_schema(role: CodexRole) -> Mapping[str, Any]:
    assertion_names = (
        "no_state_mutation", "no_plan_version_advance", "no_tool_execution",
        "no_tool_authorization", "no_semantic_commitment", "no_ui_mutation",
        "no_chain_of_thought",
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["contract_version", "role", "proposal_id", "task_binding", "input_context_hash", "source_evidence_refs", "confidence", "public_summary", "payload", "boundary_assertions"],
        "properties": {
            "contract_version": {"const": ROLE_CONTRACT_VERSION},
            "role": {"const": role.value},
            "proposal_id": {"type": "string", "maxLength": 160},
            "task_binding": {
                "type": "object",
                "required": ["task_id", "plan_version", "task_event_seq"],
                "properties": {
                    "task_id": {"type": "string", "maxLength": 160},
                    "plan_version": {"type": "integer", "minimum": 1},
                    "task_event_seq": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
            "input_context_hash": {"type": "string", "maxLength": 160},
            "source_evidence_refs": {
                "type": "array", "maxItems": 32,
                "items": {"type": "string", "maxLength": 240},
            },
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "public_summary": {"type": "string", "maxLength": MAX_PUBLIC_SUMMARY},
            "payload": _role_payload_schema(role),
            "boundary_assertions": {
                "type": "object",
                "required": list(assertion_names),
                "properties": {name: {"const": True} for name in assertion_names},
                "additionalProperties": False,
            },
        },
    }


def _role_payload_schema(role: CodexRole) -> Mapping[str, Any]:
    text = {"type": "string", "maxLength": 240}
    token = {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,63}$"}
    string_list = {"type": "array", "maxItems": 24, "items": text}
    short_string_list = {
        "type": "array",
        "maxItems": 24,
        "items": {"type": "string", "maxLength": 120},
    }
    if role == CodexRole.TASK_MODELER:
        requirement = {
            "type": "object",
            "required": [
                "requirement_id", "label", "description", "value_type", "source_route",
                "required_at", "importance", "activation", "validation_rule", "question_group",
                "priority", "tool_bindings", "sensitive", "allow_explicit_empty",
            ],
            "properties": {
                "requirement_id": token,
                "label": {"type": "string", "maxLength": 80},
                "description": text,
                "value_type": {"enum": ["string", "integer", "number", "boolean", "date", "time_window", "location", "enum", "string_list"]},
                "source_route": {"enum": ["USER", "TOOL", "DERIVED", "SYSTEM", "OPTIONAL"]},
                "required_at": {"type": ["string", "null"], "enum": ["search", "plan", "commitment", None]},
                "importance": {"enum": ["critical", "high", "normal", "low"]},
                "activation": {"type": "object"},
                "validation_rule": {"type": "object"},
                "question_group": token,
                "priority": {"type": "integer", "minimum": 0, "maximum": 10000},
                "tool_bindings": {
                    "type": "array", "maxItems": 8,
                    "items": {
                        "type": "object", "required": ["tool_name", "argument_name"],
                        "properties": {"tool_name": text, "argument_name": text},
                        "additionalProperties": False,
                    },
                },
                "sensitive": {"const": False},
                "allow_explicit_empty": {"type": "boolean"},
            },
            "additionalProperties": False,
        }
        properties = {
            "model_id": token,
            "model_version": {"type": "integer", "minimum": 1},
            "task_kind": token,
            "task_summary": text,
            "task_components": {"type": "array", "maxItems": 12, "items": token},
            "requirements": {"type": "array", "minItems": 1, "maxItems": 24, "items": requirement},
            "success_criteria": short_string_list,
            "applicable_stages": {"type": "array", "items": {"enum": ["search", "plan", "commitment"]}},
            "candidate_tool_capabilities": string_list,
            "open_modeling_questions": string_list,
            "confidence": {"enum": ["low", "medium", "high"]},
            "source_proposal_ref": text,
            "accepted_context_hash": text,
        }
        required = [
            "model_id", "model_version", "task_kind", "task_summary", "task_components",
            "requirements", "success_criteria",
            "applicable_stages", "candidate_tool_capabilities", "open_modeling_questions", "confidence",
        ]
    elif role == CodexRole.REQUIREMENT_ANALYST:
        item_properties = {
            "requirement_id": token,
            "proposed_status": {"enum": ["UNKNOWN", "CANDIDATE", "RESOLVED", "AMBIGUOUS", "CONFLICTING", "NOT_APPLICABLE", "DEFAULTED"]},
            "proposed_value": {},
            "resolution_route": {"enum": ["USER", "TOOL", "DERIVED", "SYSTEM", "OPTIONAL"]},
            "source_evidence_refs": string_list,
            "confidence": {"enum": ["low", "medium", "high"]},
            "ambiguity_reason": {"type": ["string", "null"], "maxLength": 240},
            "conflict_candidates": string_list,
            "blocks_stage": {"type": ["string", "null"], "enum": ["search", "plan", "commitment", None]},
            "materially_changes_task": {"type": "boolean"},
        }
        properties = {
            "requirements": {
                "type": "array", "maxItems": 24,
                "items": {
                    "type": "object", "required": list(item_properties),
                    "properties": item_properties, "additionalProperties": False,
                },
            }
        }
        required = ["requirements"]
    elif role == CodexRole.CLARIFIER:
        properties = {
            "clarification_id": text,
            "question_text": {"type": "string", "maxLength": 320},
            "covered_requirement_ids": {"type": "array", "maxItems": MAX_QUESTIONS, "items": token},
            "expected_answer_shape": text,
            "optional_examples": string_list,
        }
        required = list(properties)
    elif role == CodexRole.PLANNER:
        call_properties = {
            "tool_name": text,
            "arguments": {"type": "object"},
            "argument_provenance": {"type": "object"},
            "resolves_requirement_ids": {"type": "array", "maxItems": 24, "items": token},
        }
        properties = {
            "planning_mode": {"enum": ["INFORMATION_GATHERING", "DRAFT_PLAN", "FINAL_PLAN_CANDIDATE"]},
            "plan_summary": {"type": "string", "maxLength": 320},
            "ordered_steps": string_list,
            "requirement_coverage": {"type": "array", "maxItems": 24, "items": token},
            "proposed_tool_calls": {
                "type": "array", "maxItems": 8,
                "items": {
                    "type": "object", "required": list(call_properties),
                    "properties": call_properties, "additionalProperties": False,
                },
            },
            "unresolved_optional_items": string_list,
            "assumptions": string_list,
            "risks": string_list,
            "requires_confirmation": {"type": "boolean"},
            "source_evidence_refs": string_list,
        }
        required = list(properties)
    else:
        properties = {
            "verdict": {"enum": ["PASS", "REVISE", "BLOCK"]},
            "violations": string_list,
            "missing_requirement_coverage": {"type": "array", "maxItems": 24, "items": token},
            "unsupported_claims": string_list,
            "stale_evidence_usage": string_list,
            "tool_binding_errors": string_list,
            "premature_commitment": {"type": "boolean"},
            "risk_notes": string_list,
        }
        required = list(properties)
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


def make_codex_cli_role_invoker(config: Any) -> ProviderInvoker:
    """Create a role provider without exposing CLI output outside the adapter boundary."""

    async def invoke(
        role: CodexRole,
        context: Mapping[str, Any],
        schema: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        from voice_agent.adapters.codex_slow_llm import _run_codex_cli_async

        prompt = "\n".join(
            (
                role_prompt(role),
                "Return only JSON matching the transport schema. Put the role payload as JSON text in payload_json.",
                "The decoded payload_json must satisfy this role payload contract: "
                + json.dumps(
                    schema["properties"]["payload"],
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "Never execute tools or mutate state.",
                json.dumps(
                    context, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                ),
            )
        )
        stdout, _ = await _run_codex_cli_async(
            config,
            prompt,
            output_schema_json=json.dumps(
                _role_transport_schema(role),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        candidate = _parse_role_jsonl(stdout)
        if candidate is None:
            raise CodexRoleValidationError("provider_returned_no_role_output")
        return _decode_transport_candidate(candidate, role=role)

    return invoke


def _role_transport_schema(role: CodexRole) -> Mapping[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["role", "public_summary", "payload_json"],
        "properties": {
            "role": {"type": "string", "const": role.value},
            "public_summary": {"type": "string", "maxLength": MAX_PUBLIC_SUMMARY},
            "payload_json": {
                "type": "string",
                "description": f"Decoded JSON object for the {role.value} payload contract.",
            },
        },
    }


def _decode_transport_candidate(
    candidate: Mapping[str, Any],
    *,
    role: CodexRole,
) -> Mapping[str, Any]:
    payload_json = candidate.get("payload_json")
    if payload_json is None:
        return candidate
    if candidate.get("role") != role.value or not isinstance(payload_json, str):
        raise CodexRoleValidationError("role_transport_contract_mismatch")
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise CodexRoleValidationError("provider_payload_json_invalid") from exc
    if not isinstance(payload, Mapping):
        raise CodexRoleValidationError("provider_payload_json_not_object")
    return {
        "role": role.value,
        "public_summary": candidate.get("public_summary"),
        "payload": payload,
    }


def _parse_role_jsonl(stdout: str) -> Mapping[str, Any] | None:
    candidate: Mapping[str, Any] | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        values: list[Any] = [
            event.get("output"), event.get("result"), event.get("structured_output")
        ]
        item = event.get("item")
        if isinstance(item, Mapping):
            values.extend((item.get("output"), item.get("text")))
        for value in values:
            if isinstance(value, Mapping):
                candidate = value
            elif isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, Mapping):
                    candidate = parsed
    return candidate


def role_prompt(role: CodexRole) -> str:
    return {
        CodexRole.TASK_MODELER: "Model the user's task as bounded declarative requirements. Return a proposal only.",
        CodexRole.REQUIREMENT_ANALYST: "Propose requirement statuses from cited evidence. Do not mutate task state.",
        CodexRole.CLARIFIER: "Phrase only the backend-selected requirements as a short voice question.",
        CodexRole.PLANNER: "Propose a plan and allow-listed tool calls. Never execute or authorize tools.",
        CodexRole.REVIEWER: "Review the plan for coverage, stale evidence, unsupported claims, and tool errors.",
    }[role]


def minimal_role_context(role: CodexRole, task_context: Mapping[str, Any], role_context: Mapping[str, Any]) -> dict[str, Any]:
    common = {"task_binding": task_context.get("task_binding", {}), "goal": task_context.get("current_goal", role_context.get("goal", ""))}
    allowed = {
        CodexRole.TASK_MODELER: (
            "goal", "latest_user_input", "accepted_evidence_summary", "known_facts",
            "available_tool_manifest_summaries", "system_constraints",
        ),
        CodexRole.REQUIREMENT_ANALYST: ("task_requirement_model", "requirement_summary", "evidence_text", "source_evidence_refs", "stale_evidence_refs"),
        CodexRole.CLARIFIER: ("selected_requirement_ids", "selected_labels", "selected_descriptions", "reason", "asked_counts", "recent_user_input", "max_questions", "max_fields"),
        CodexRole.PLANNER: ("task_requirement_model", "task_summary", "resolved_requirement_ids", "accepted_facts", "tool_gap_ids", "current_plan_evidence", "stale_evidence_refs", "available_tool_manifest_summaries", "success_criteria", "planner_mode"),
        CodexRole.REVIEWER: ("task_requirement_model", "accepted_facts", "plan_proposal", "source_evidence_refs", "stale_evidence_refs", "available_tool_manifest_summaries"),
    }[role]
    for key in allowed:
        if key in role_context:
            common[key] = role_context[key]
        elif key in task_context:
            common[key] = task_context[key]
    return common


def validate_role_proposal(
    proposal: Mapping[str, Any],
    *,
    role: CodexRole,
    role_context: Mapping[str, Any],
    tool_registry: ToolRegistry,
    expected_task_binding: Mapping[str, Any] | None = None,
    expected_context_hash: str | None = None,
    expected_source_evidence_refs: Sequence[str] | None = None,
) -> None:
    if proposal.get("contract_version") != ROLE_CONTRACT_VERSION or proposal.get("role") != role.value:
        raise CodexRoleValidationError("role_contract_mismatch")
    if expected_task_binding is not None and dict(proposal.get("task_binding", {})) != dict(expected_task_binding):
        raise CodexRoleValidationError("task_binding_mismatch")
    if expected_context_hash is not None and proposal.get("input_context_hash") != expected_context_hash:
        raise CodexRoleValidationError("input_context_hash_mismatch")
    if expected_source_evidence_refs is not None and tuple(proposal.get("source_evidence_refs", ())) != tuple(expected_source_evidence_refs):
        raise CodexRoleValidationError("source_evidence_refs_mismatch")
    assertions = proposal.get("boundary_assertions")
    required_assertions = {
        "no_state_mutation", "no_plan_version_advance", "no_tool_execution", "no_tool_authorization",
        "no_semantic_commitment", "no_ui_mutation", "no_chain_of_thought",
    }
    if not isinstance(assertions, Mapping) or any(assertions.get(name) is not True for name in required_assertions):
        raise CodexRoleValidationError("boundary_assertions_failed")
    summary = str(proposal.get("public_summary", ""))
    if not summary or len(summary) > MAX_PUBLIC_SUMMARY:
        raise CodexRoleValidationError("invalid_public_summary")
    payload = proposal.get("payload")
    if not isinstance(payload, Mapping):
        raise CodexRoleValidationError("payload_must_be_object")
    if role == CodexRole.TASK_MODELER:
        try:
            validate_task_requirement_model(
                payload,
                tool_registry=tool_registry,
                source_proposal_ref="proposal://validation/task-modeler-candidate",
                accepted_context_hash=expected_context_hash or "sha256:validation-candidate",
            )
        except RequirementModelValidationError as exc:
            raise CodexRoleValidationError("task_requirement_model_invalid") from exc
    elif role == CodexRole.REQUIREMENT_ANALYST:
        try:
            model = task_requirement_model_from_dict(
                role_context.get("task_requirement_model", {}),
                tool_registry=tool_registry,
            )
        except RequirementModelValidationError as exc:
            raise CodexRoleValidationError("analyst_model_context_invalid") from exc
        expected_refs = set(str(ref) for ref in expected_source_evidence_refs or ())
        for raw in payload.get("requirements", ()):
            if not isinstance(raw, Mapping):
                raise CodexRoleValidationError("analyst_requirement_not_object")
            requirement_id = str(raw.get("requirement_id", ""))
            spec = model.requirements_by_id.get(requirement_id)
            if spec is None:
                raise CodexRoleValidationError("analyst_unknown_requirement")
            if raw.get("resolution_route") != spec.source_route.value:
                raise CodexRoleValidationError("analyst_source_route_mismatch")
            refs = tuple(str(ref) for ref in raw.get("source_evidence_refs", ()))
            if not refs or (expected_refs and not set(refs).issubset(expected_refs)):
                raise CodexRoleValidationError("analyst_source_evidence_refs_mismatch")
            status_value = str(raw.get("proposed_status", ""))
            if status_value not in {
                "UNKNOWN", "CANDIDATE", "RESOLVED", "AMBIGUOUS", "CONFLICTING",
                "NOT_APPLICABLE", "DEFAULTED",
            }:
                raise CodexRoleValidationError("analyst_status_invalid")
            if status_value in {"CANDIDATE", "RESOLVED"}:
                try:
                    validate_requirement_value(spec, raw.get("proposed_value"))
                except RequirementModelValidationError as exc:
                    raise CodexRoleValidationError("analyst_value_type_invalid") from exc
    elif role == CodexRole.CLARIFIER:
        selected = tuple(str(value) for value in role_context.get("selected_requirement_ids", ()))
        covered = tuple(str(value) for value in payload.get("covered_requirement_ids", ()))
        if covered != selected:
            raise CodexRoleValidationError("clarifier_selected_requirement_mismatch")
        if not 1 <= len(covered) <= int(role_context.get("max_fields", MAX_QUESTIONS)):
            raise CodexRoleValidationError("clarifier_field_budget_exceeded")
    elif role == CodexRole.PLANNER:
        if payload.get("planning_mode") not in {"INFORMATION_GATHERING", "DRAFT_PLAN", "FINAL_PLAN_CANDIDATE"}:
            raise CodexRoleValidationError("invalid_planning_mode")
        stale = set(str(ref) for ref in role_context.get("stale_evidence_refs", ()))
        source_refs = set(str(ref) for ref in payload.get("source_evidence_refs", ()))
        if stale & source_refs:
            raise CodexRoleValidationError("planner_used_stale_evidence")
        resolved = set(str(value) for value in role_context.get("resolved_requirement_ids", ()))
        coverage = set(str(value) for value in payload.get("requirement_coverage", ()))
        if not coverage.issubset(resolved):
            raise CodexRoleValidationError("planner_assumed_unknown_requirement")
        for call in payload.get("proposed_tool_calls", ()):
            if not isinstance(call, Mapping):
                raise CodexRoleValidationError("planner_tool_call_must_be_object")
            try:
                manifest = tool_registry.get(str(call.get("tool_name", "")))
            except ToolExecutionPolicyError as exc:
                raise CodexRoleValidationError("planner_unknown_tool") from exc
            arguments = call.get("arguments", {})
            if not isinstance(arguments, Mapping) or any(name not in {*manifest.required_arguments, *manifest.optional_arguments} for name in arguments):
                raise CodexRoleValidationError("planner_unknown_tool_argument")
            if any(name not in arguments for name in manifest.required_arguments):
                raise CodexRoleValidationError("planner_missing_required_tool_argument")
        forbidden = ("已预订", "已付款", "已发送", "booking completed", "payment completed")
        public_plan_parts = [
            str(payload.get("plan_summary", "")),
            *(str(step) for step in payload.get("ordered_steps", ())),
        ]
        if any(_claims_external_side_effect(part, forbidden) for part in public_plan_parts):
            raise CodexRoleValidationError("planner_claimed_external_side_effect")
    elif role == CodexRole.REVIEWER and payload.get("verdict") not in {"PASS", "REVISE", "BLOCK"}:
        raise CodexRoleValidationError("invalid_reviewer_verdict")


def _normalize_envelope(raw: Mapping[str, Any], *, role: CodexRole, task_binding: Mapping[str, Any], source_evidence_refs: Sequence[str], input_context_hash: str) -> dict[str, Any]:
    normalized = dict(raw)
    normalized.setdefault("contract_version", ROLE_CONTRACT_VERSION)
    normalized.setdefault("role", role.value)
    normalized.setdefault("proposal_id", f"{role.value.lower()}_{task_binding['task_id']}_{task_binding['plan_version']}_{task_binding['task_event_seq']}")
    normalized.setdefault("task_binding", dict(task_binding))
    normalized.setdefault("input_context_hash", input_context_hash)
    normalized.setdefault("source_evidence_refs", list(source_evidence_refs))
    normalized.setdefault("confidence", "medium")
    normalized.setdefault("boundary_assertions", _boundary_assertions())
    return normalized


def _envelope(role: CodexRole, task_binding: Mapping[str, Any], refs: Sequence[str], context_hash: str, payload: Mapping[str, Any], summary: str) -> dict[str, Any]:
    return {
        "contract_version": ROLE_CONTRACT_VERSION,
        "role": role.value,
        "proposal_id": f"{role.value.lower()}_{task_binding['task_id']}_{task_binding['plan_version']}_{task_binding['task_event_seq']}",
        "task_binding": dict(task_binding),
        "input_context_hash": context_hash,
        "source_evidence_refs": list(refs),
        "confidence": "high",
        "public_summary": summary[:MAX_PUBLIC_SUMMARY],
        "payload": dict(payload),
        "boundary_assertions": _boundary_assertions(),
    }


def _boundary_assertions() -> dict[str, bool]:
    return {
        "no_state_mutation": True,
        "no_plan_version_advance": True,
        "no_tool_execution": True,
        "no_tool_authorization": True,
        "no_semantic_commitment": True,
        "no_ui_mutation": True,
        "no_chain_of_thought": True,
    }


def _fallback_question(selected: Sequence[str], labels: Mapping[str, Any]) -> str:
    readable = [str(labels.get(requirement_id, requirement_id)) for requirement_id in selected]
    if len(readable) == 1:
        return f"继续规划前，请补充{readable[0]}。"
    return "继续规划前，请补充" + "、".join(readable) + "。"


def _claims_external_side_effect(text: str, markers: Sequence[str]) -> bool:
    lowered = text.lower()
    if not any(marker in lowered for marker in markers):
        return False
    return not any(negation in lowered for negation in ("不得", "不能", "不要", "尚未", "未完成", "not "))


def _context_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _safe_provider_failure_reason(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "provider_timeout"
    message = str(exc).lower()
    if "model" in message and ("unsupported" in message or "not supported" in message):
        return "provider_model_unsupported"
    if "codex_cli_unavailable" in message or "no such file" in message or "not found" in message:
        return "codex_cli_unavailable"
    if "provider_returned_no_role_output" in message:
        return "missing_structured_output"
    if "provider_output_schema_invalid" in message:
        return "provider_output_schema_invalid"
    if "provider_payload_json_invalid" in message or "provider_payload_json_not_object" in message:
        return "provider_jsonl_invalid"
    if "jsonl" in message:
        return "provider_jsonl_invalid"
    if "schema" in message:
        return "structured_output_validation_failed"
    return "role_provider_failed"


def _schema_name(role: CodexRole) -> str:
    return f"{ROLE_SCHEMA_PREFIX}.{role.value.lower()}.v1"


__all__ = [
    "CodexRole", "CodexRoleAdapter", "CodexRoleValidationError", "ROLE_CONTRACT_VERSION",
    "ROLE_TIMEOUT_SECONDS", "RoleInvocationResult", "make_codex_cli_role_invoker",
    "minimal_role_context", "role_output_schema", "role_prompt", "validate_role_proposal",
]
