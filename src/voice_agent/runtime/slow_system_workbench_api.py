from __future__ import annotations

"""JSON-line control plane used by the Vite Workbench endpoint.

The transport is intentionally tiny: one long-lived Python process owns the
session manager and receives one JSON request per line.  Vite is the HTTP/SSE
edge; this module keeps all session state and all event-journal writes inside
Python.
"""

import asyncio
from collections.abc import Mapping
import json
import sys
from typing import Any

from voice_agent.runtime.slow_system_workbench_sessions import (
    WORKBENCH_DEFAULT_ALLOW_LOCAL_CODEX_CLI,
    WORKBENCH_DEFAULT_PROVIDER_MODE,
    WorkbenchRuntimeConfig,
    WorkbenchSession,
    WorkbenchSessionManager,
)


class WorkbenchApi:
    def __init__(self, *, manager: WorkbenchSessionManager | None = None) -> None:
        self.manager = manager or WorkbenchSessionManager()

    async def dispatch(self, operation: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = {} if payload is None else payload
        if operation == "create_session":
            config = _config_from_payload(payload)
            session = await self.manager.create_session(
                session_id=_optional_string(payload.get("session_id")),
                config=config,
            )
            return {
                "ok": True,
                "session_id": session.session_id,
                "snapshot": await session.snapshot(),
            }

        if operation == "snapshot":
            session = await self.manager.get(_required_string(payload, "session_id"))
            return {"ok": True, "session_id": session.session_id, "snapshot": await session.snapshot()}

        if operation == "message":
            session = await self.manager.get(_required_string(payload, "session_id"))
            message_text = payload.get("text", payload.get("message"))
            if not isinstance(message_text, str) or not message_text.strip():
                raise ValueError("text must be a non-empty string")
            result = await session.process_message(
                message_text,
                action=_optional_string(payload.get("action")),
            )
            capability = dict(session.codex_capability)
            proposal = session.latest_proposal or _foreground_proposal(
                session_id=session.session_id,
                intent=message_text,
                source_evidence_refs=(f"evidence://synthetic/{session.session_id}/user_input",),
                provider_mode=str(capability.get("provider", "codex_cli")),
                output_mode=str(capability.get("output_mode", "degraded")),
            )
            return {
                **result,
                "ok": True,
                "proposal": proposal,
                "capability": _public_capability(capability),
            }

        if operation == "confirmation":
            session = await self.manager.get(_required_string(payload, "session_id"))
            accepted = payload.get("accepted")
            if not isinstance(accepted, bool):
                raise ValueError("accepted must be a boolean")
            result = await session.confirm(
                _required_string(payload, "confirmation_id"),
                accepted=accepted,
            )
            return {**result, "ok": True}

        if operation == "reset":
            session = await self.manager.get(_required_string(payload, "session_id"))
            return {
                "ok": True,
                "session_id": session.session_id,
                "snapshot": await session.reset(),
            }

        if operation == "compat_proposal":
            return await self._compat_proposal(payload)

        raise ValueError(f"unsupported workbench operation: {operation}")

    async def _compat_proposal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        provider_mode = str(payload.get("provider_mode", WORKBENCH_DEFAULT_PROVIDER_MODE))
        if provider_mode == "codex_unavailable":
            provider_mode = "codex_cli_unavailable"
        config = _config_from_payload(
            {
                "provider_mode": provider_mode,
                "allow_local_codex_cli": payload.get(
                    "allow_local_codex_cli", WORKBENCH_DEFAULT_ALLOW_LOCAL_CODEX_CLI
                ),
                "codex_bin": payload.get("codex_bin", "codex"),
                "timeout_seconds": payload.get("timeout_seconds", 60),
                "model_name": payload.get("model_name"),
                "reasoning_effort": payload.get("reasoning_effort"),
            }
        )
        requested_session_id = _optional_string(payload.get("session_id"))
        if requested_session_id is None:
            session = await self.manager.create_session(config=config)
        else:
            try:
                session = await self.manager.get(requested_session_id)
            except KeyError:
                session = await self.manager.create_session(
                    session_id=requested_session_id,
                    config=config,
                )

        action = _compat_action(payload.get("action") or payload.get("scenario_action"))
        intent = _optional_string(payload.get("intent")) or "请处理一个 synthetic Workbench SlowTask。"
        snapshot_before = await session.snapshot()
        if action in {"material_patch", "cancel_candidate"} and not _has_active_task(snapshot_before):
            await session.process_message(
                "帮我规划一个两天的客户来访行程，地点尽量靠近公司。",
                action="start",
            )
        if action == "material_patch" and str(payload.get("action")) == "receive_late_tool_result":
            await session.process_message("补充一个预算约束，旧调用稍后返回。", action="material_patch")
            status_result = await session.snapshot()
        else:
            result = await session.process_message(intent, action=action)
            status_result = result["snapshot"]

        proposal = session.latest_proposal
        if proposal is None:
            proposal = _foreground_proposal(
                session_id=session.session_id,
                intent=intent,
                source_evidence_refs=_string_tuple(
                    payload.get("source_evidence_refs", ("evidence://synthetic/workbench/user",))
                ),
                provider_mode=provider_mode,
                output_mode=str(session.codex_capability.get("output_mode", "degraded")),
            )
        capability = dict(session.codex_capability)
        return {
            "ok": True,
            "backend": "python_slow_system_workbench_codex",
            "status": "dynamic_session_projection",
            "session_id": session.session_id,
            "proposal": proposal,
            "capability": {
                "output_mode": capability.get("output_mode"),
                "provider": capability.get("provider"),
                "health_status": capability.get("health_status"),
                "adapter_id": capability.get("adapter_id"),
                "deployment_mode": capability.get("deployment_mode"),
            },
            "snapshot": status_result,
        }


def _config_from_payload(payload: Mapping[str, Any]) -> WorkbenchRuntimeConfig:
    return WorkbenchRuntimeConfig.from_mapping(payload)


def _compat_action(value: object) -> str:
    aliases = {
        "start_new_task": "start",
        "send_user_patch": "material_patch",
        "receive_late_tool_result": "material_patch",
        "request_cancel_confirmation": "cancel_candidate",
        "foreground_chat": "foreground",
        "start": "start",
        "material_patch": "material_patch",
        "cancel_candidate": "cancel_candidate",
    }
    return aliases.get(str(value), "start")


def _has_active_task(snapshot: Mapping[str, Any]) -> bool:
    task = snapshot.get("task")
    return isinstance(task, Mapping) and str(task.get("lifecycle")) not in {"COMPLETED", "CANCELLED", "FAILED"}


def _public_capability(capability: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "output_mode": capability.get("output_mode"),
        "provider": capability.get("provider"),
        "health_status": capability.get("health_status"),
        "adapter_id": capability.get("adapter_id"),
        "deployment_mode": capability.get("deployment_mode"),
    }


def _required_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("string field must be non-empty when provided")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _foreground_proposal(
    *,
    session_id: str,
    intent: str,
    source_evidence_refs: tuple[str, ...],
    provider_mode: str,
    output_mode: str,
) -> dict[str, Any]:
    """Return a bounded non-provider proposal for FAST_ONLY turns."""

    return {
        "proposal_id": f"proposal_{session_id}_foreground",
        "proposal_type": "evidence_review",
        "status": "validated",
        "summary": "当前 turn 被 Router 归类为 FAST_ONLY；没有 SlowTask fact 需要变更。",
        "suggested_next_steps": [
            "保留当前 foreground chat 结果。",
            "如果用户提出复杂目标，再由 Router 门控进入 SlowTask。",
        ],
        "missing_fields": [],
        "requires_confirmation": False,
        "risk_notes": ["该 proposal 只用于解释 FAST_ONLY，不拥有任务事实。"],
        "source_evidence_refs": list(source_evidence_refs),
        "safety": {
            "codex_is_fact_owner": False,
            "advances_plan_version": False,
            "authorizes_tool": False,
            "contains_secret": False,
            "mutates_task_snapshot": False,
            "emits_canonical_event": False,
            "executes_external_tool": False,
            "contains_raw_provider_body": False,
        },
        "provider_mode": provider_mode,
        "output_mode": output_mode,
        "intent_summary": "bounded foreground intent received",
    }


def _safe_error(value: BaseException) -> str:
    message = str(value).lower()
    if "timeout" in message:
        return "workbench_timeout"
    if "unknown" in message or "unsupported" in message:
        return "workbench_request_invalid"
    if "confirmation" in message:
        return "confirmation_request_invalid"
    return "workbench_request_failed"


async def run_stdio() -> None:
    api = WorkbenchApi()
    response_lock = asyncio.Lock()
    tasks: set[asyncio.Task[None]] = set()

    async def handle_request(request: Mapping[str, Any]) -> None:
        try:
            operation = request.get("operation")
            if not isinstance(operation, str):
                raise ValueError("operation must be a string")
            payload = request.get("payload", {})
            if not isinstance(payload, Mapping):
                raise ValueError("payload must be an object")
            response = await api.dispatch(operation, payload)
            if "request_id" in request:
                response = {"request_id": request["request_id"], **response}
        except BaseException as exc:  # the transport must always answer one line
            response = {
                "request_id": request.get("request_id"),
                "ok": False,
                "error": _safe_error(exc),
            }
        async with response_lock:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + "\n")
            sys.stdout.flush()

    def remember(task: asyncio.Task[None]) -> None:
        tasks.discard(task)

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        try:
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise ValueError("JSON-line request must be an object")
        except BaseException as exc:  # the transport must always answer one line
            response = {"ok": False, "error": _safe_error(exc)}
            async with response_lock:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + "\n")
                sys.stdout.flush()
            continue
        task = asyncio.create_task(handle_request(request))
        tasks.add(task)
        task.add_done_callback(remember)
    if tasks:
        await asyncio.gather(*tasks)


def main() -> None:
    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()


__all__ = ["WorkbenchApi", "main", "run_stdio"]
