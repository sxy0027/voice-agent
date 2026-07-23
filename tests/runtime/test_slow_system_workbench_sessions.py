from __future__ import annotations

import asyncio
import json
from pathlib import Path

from voice_agent.runtime.slow_system_workbench_api import WorkbenchApi
from voice_agent.adapters import codex_slow_llm
from voice_agent.adapters.codex_slow_llm import (
    CodexSlowLLMAdapterConfig,
    _proposal_from_structured_output,
    parse_codex_jsonl_output,
    _run_codex_cli_async,
)
from voice_agent.runtime.slow_system_workbench_sessions import (
    WorkbenchRuntimeConfig,
    WorkbenchSession,
    WorkbenchSessionManager,
)


def test_python_owned_session_starts_dynamic_tool_phase_with_fake_codex() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_dynamic_workbench",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )

        initial = await session.snapshot()
        assert initial["task"] is None
        result = await session.process_message(
            "帮我规划一个两天的客户来访行程，地点尽量靠近公司。",
            action="start",
        )
        snapshot = result["snapshot"]

        assert result["status"] == "tool_running"
        assert snapshot["mode"] == "text_first_python_owned"
        assert snapshot["router"]["router_decision"] == "SPAWN_SLOW_TASK"
        assert snapshot["task"]["lifecycle"] == "EXECUTING"
        assert snapshot["task"]["current_plan_version"] == 1
        assert snapshot["task"]["in_flight_tool_calls"]
        assert any(event["event_name"] == "TOOL_EXECUTION_STARTED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "WAITING_FOR_TOOL" for event in snapshot["timeline"])
        assert snapshot["capability_matrices"][-1]["output_mode"] == "mock"
        assert snapshot["provider_trace"][-1]["output_mode"] == "fallback"
        assert snapshot["safety"]["raw_provider_body_included"] is False

    asyncio.run(scenario())


def test_workbench_environment_defaults_to_local_codex_cli(monkeypatch) -> None:
    monkeypatch.delenv("VOICE_AGENT_WORKBENCH_CODEX_MODE", raising=False)
    monkeypatch.delenv("VOICE_AGENT_ALLOW_LOCAL_CODEX_CLI", raising=False)

    config = WorkbenchRuntimeConfig.from_environment()

    assert config.provider_mode == "codex_cli_local"
    assert config.allow_local_codex_cli is True


def test_local_codex_cli_passes_output_schema_as_short_lived_file(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, prompt: bytes) -> tuple[bytes, bytes]:
            command = captured["command"]
            assert isinstance(command, tuple)
            schema_path = Path(command[command.index("--output-schema") + 1])
            captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
            captured["prompt"] = prompt.decode("utf-8")
            return b'{"type":"turn.completed"}\n', b""

    async def fake_create_subprocess_exec(*command: str, **_kwargs: object) -> FakeProcess:
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(codex_slow_llm.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    stdout, stderr = asyncio.run(
        _run_codex_cli_async(
            CodexSlowLLMAdapterConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=True,
            ),
            "Return one structured proposal.",
        )
    )

    command = captured["command"]
    assert isinstance(command, tuple)
    schema_path = Path(command[command.index("--output-schema") + 1])
    assert captured["schema"]["type"] == "object"
    assert captured["schema"]["additionalProperties"] is False
    assert "schema_version" in captured["schema"]["required"]
    assert captured["prompt"] == "Return one structured proposal."
    assert not schema_path.exists()
    assert stdout == '{"type":"turn.completed"}\n'
    assert stderr == ""


def test_codex_jsonl_parser_extracts_item_completed_text() -> None:
    output = {
        "schema_version": "slow_llm_qwen_evidence_v1",
        "task_binding": {},
    }
    jsonl = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(output),
            },
        }
    )

    parsed = parse_codex_jsonl_output(jsonl)

    assert parsed.structured_output == output
    assert parsed.provider_event_count == 1


def test_local_codex_cli_reports_allowlisted_jsonl_progress_without_raw_text(monkeypatch) -> None:
    progress: list[dict[str, object]] = []

    class FakeStdin:
        def write(self, _value: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    class FakeStdout:
        def __init__(self, lines: list[bytes]) -> None:
            self._lines = iter(lines)

        async def readline(self) -> bytes:
            return next(self._lines, b"")

    class FakeStderr:
        async def read(self, _size: int) -> bytes:
            return b""

    class FakeProcess:
        returncode = 0

        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = FakeStdout(
                [
                    b'{"type":"thread.started"}\n',
                    b'{"type":"item.completed","item":{"text":"hidden provider text"}}\n',
                ]
            )
            self.stderr = FakeStderr()

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*_command: str, **_kwargs: object) -> FakeProcess:
        return FakeProcess()

    async def on_progress(item) -> None:
        progress.append(dict(item))

    monkeypatch.setattr(codex_slow_llm.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    stdout, stderr = asyncio.run(
        _run_codex_cli_async(
            CodexSlowLLMAdapterConfig(
                provider_mode="codex_cli_local",
                allow_local_codex_cli=True,
            ),
            "Return one structured proposal.",
            on_provider_event=on_progress,
        )
    )

    assert stdout.count("\n") == 2
    assert stderr == ""
    assert [item["kind"] for item in progress] == ["thread.started", "item.completed"]
    assert all("text" not in item for item in progress)


def test_session_snapshot_exposes_live_progress_while_provider_is_waiting() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_live_progress",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        original_propose = session._codex_adapter.propose
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_propose(**kwargs):
            await kwargs["on_progress"](
                {
                    "kind": "item.started",
                    "status": "started",
                    "provider_mode": "fake",
                    "output_mode": "mock",
                    "tool_name": "demo.itinerary.search",
                }
            )
            started.set()
            await release.wait()
            return await original_propose(**kwargs)

        session._codex_adapter.propose = slow_propose
        task = asyncio.create_task(session.process_message("规划两天客户行程", action="start"))
        await started.wait()

        live = await session.snapshot()
        assert live["streaming"]["active"] is True
        assert live["live_progress"]
        assert live["conversation"][-1]["speaker"] == "user"
        assert live["snapshot_id"].endswith(":stream:0004")

        release.set()
        final = await task
        assert final["snapshot"]["streaming"]["active"] is False
        assert final["snapshot"]["live_progress"]

    asyncio.run(scenario())


def test_codex_proposal_projection_uses_validated_model_analysis() -> None:
    proposal = _proposal_from_structured_output(
        {
            "task_binding": {"adapter_request_id": "request_real_projection"},
            "task_analysis": {
                "summary": "根据用户的上午时间窗，建议先筛选靠近公司的候选地点。",
                "intent": "morning_itinerary_with_budget_constraint",
            },
            "tool_proposal": {"tool_name": "demo.itinerary.search"},
            "confirmation_risk_hints": ["只使用 synthetic demo sandbox。"],
            "missing_fields": [],
        },
        source_evidence_refs=("evidence://synthetic/user/request",),
        provider_mode="codex_cli_local",
        output_mode="real",
    )

    assert "上午时间窗" in proposal["summary"]
    assert any("上午时间窗" in step for step in proposal["suggested_next_steps"])
    assert any(
        "morning_itinerary_with_budget_constraint" in step
        for step in proposal["suggested_next_steps"]
    )
    assert any("synthetic demo sandbox" in note for note in proposal["risk_notes"])


def test_material_patch_advances_plan_and_late_tool_result_is_stale() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_dynamic_patch",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        await session.process_message("规划两天客户行程", action="start")
        result = await session.process_message(
            "改成明天上午，预算不超过 500 元。",
            action="material_patch",
        )
        snapshot = result["snapshot"]

        assert result["status"] == "plan_advanced_stale_result"
        assert snapshot["task"]["current_plan_version"] == 2
        assert snapshot["task"]["lifecycle"] == "PLANNING"
        assert snapshot["task"]["stale_evidence"]
        assert any(event["event_name"] == "PLAN_VERSION_ADVANCED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "TOOL_RESULT_RECEIVED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "TOOL_RESULT_MARKED_STALE" for event in snapshot["timeline"])
        assert any(event["event_name"] == "STALE_EVIDENCE_RECORDED" for event in snapshot["timeline"])
        assert snapshot["replay"]["replay_reruns_provider"] is False
        assert snapshot["replay"]["replay_reruns_tool"] is False

        again = await session.snapshot()
        assert again["context_hash"] == snapshot["context_hash"]
        assert again["replay"]["state_digest"] == snapshot["replay"]["state_digest"]

    asyncio.run(scenario())


def test_cancel_requires_current_confirmation_then_clears_focus() -> None:
    async def scenario() -> None:
        session = WorkbenchSession(
            session_id="test_dynamic_cancel",
            config=WorkbenchRuntimeConfig(provider_mode="fake"),
        )
        await session.process_message("规划两天客户行程", action="start")
        candidate = await session.process_message("取消这个任务", action="cancel_candidate")
        pending = candidate["snapshot"]["task"]["pending_confirmation"]

        assert candidate["status"] == "confirmation_required"
        assert candidate["snapshot"]["task"]["lifecycle"] == "WAITING_FOR_USER_CONFIRMATION"
        assert pending["scope"] == "TASK_CANCEL"

        confirmed = await session.confirm(pending["confirmation_id"], accepted=True)
        snapshot = confirmed["snapshot"]
        assert confirmed["status"] == "cancelled"
        assert snapshot["task"]["lifecycle"] == "CANCELLED"
        assert snapshot["task"]["terminal_outcome"] == "CANCELLED"
        assert snapshot["task"]["pending_confirmation"] is None
        assert snapshot["task"]["in_flight_tool_calls"] == []
        assert snapshot["router"]["active_task_id"] is None
        assert any(event["event_name"] == "CONFIRMATION_ACCEPTED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "SLOWTASK_CANCELLED" for event in snapshot["timeline"])
        assert any(event["event_name"] == "TOOL_EXECUTION_CANCELLED" for event in snapshot["timeline"])

    asyncio.run(scenario())


def test_api_manager_keeps_sessions_python_owned_and_resettable() -> None:
    async def scenario() -> None:
        api = WorkbenchApi(
            manager=WorkbenchSessionManager(
                default_config=WorkbenchRuntimeConfig(provider_mode="fake"),
            )
        )
        created = await api.dispatch("create_session", {"session_id": "test_api_workbench"})
        assert created["ok"] is True
        started = await api.dispatch(
            "message",
            {
                "session_id": "test_api_workbench",
                "text": "规划两天客户行程",
                "action": "start",
            },
        )
        assert started["ok"] is True
        assert started["snapshot"]["task"]["current_plan_version"] == 1
        assert started["proposal"]["safety"]["codex_is_fact_owner"] is False
        reset = await api.dispatch("reset", {"session_id": "test_api_workbench"})
        assert reset["ok"] is True
        assert reset["snapshot"]["task"] is None
        assert reset["snapshot"]["replay"]["event_count"] == 2

    asyncio.run(scenario())
