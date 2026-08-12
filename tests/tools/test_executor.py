from __future__ import annotations

from voice_agent.demo_backend.in_memory import InMemoryDemoBackend
from voice_agent.runtime.session import start_mvp0_session
from voice_agent.tools.demo_manifests import web_search_manifest
from voice_agent.tools.executor import DemoToolExecutor, ToolExecutionRequest
from voice_agent.tools.registry import ToolRegistry


def test_executor_real_missing_query_is_canonical_and_never_attributed_to_reviewer() -> None:
    startup = start_mvp0_session(
        session_id="session_executor_missing",
        conversation_id="conversation_executor_missing",
        runtime_config_ref="config://synthetic/executor-missing",
        created_monotonic_ms=1,
        created_wall_clock_ms=1,
    )
    journal = startup.journal
    journal.append(
        event_name="SLOWTASK_CREATED",
        event_id="event_created",
        source_module="slowtask_runtime",
        caused_by_event_id=str(journal.events()[-1]["event_id"]),
        created_monotonic_ms=3,
        created_wall_clock_ms=3,
        trace_redaction_level="metadata_only",
        task_id="task_executor_missing",
        plan_version=1,
        task_event_seq=1,
        initial_goal_ref="goal://synthetic/executor-missing",
    )
    arguments = journal.append(
        event_name="ARGUMENTS_RESOLVED",
        event_id="event_arguments_resolved",
        source_module="slowtask_runtime",
        caused_by_event_id="event_created",
        created_monotonic_ms=4,
        created_wall_clock_ms=4,
        trace_redaction_level="metadata_only",
        task_id="task_executor_missing",
        plan_version=1,
        task_event_seq=2,
        resolved_arguments_ref="args://synthetic/executor-missing/current",
        provenance_ref="provenance://synthetic/executor-missing/current",
    )
    executor = DemoToolExecutor(
        journal=journal,
        registry=ToolRegistry([web_search_manifest()]),
        backend=InMemoryDemoBackend(),
    )

    started = executor.begin(
        ToolExecutionRequest(
            tool_call_id="tool_call_missing_query",
            tool_name="webSearch",
            task_id="task_executor_missing",
            plan_version=1,
            current_plan_version=1,
            start_task_event_seq=3,
            caused_by_event_id=str(arguments["event_id"]),
            event_id_prefix="event_tool_missing",
            created_monotonic_ms=10,
            created_wall_clock_ms=10,
            idempotency_key="idem://synthetic/executor-missing",
            arguments={},
            argument_provenance={},
            resolved_arguments_ref="args://synthetic/executor-missing",
            provenance_ref="provenance://synthetic/executor-missing",
        )
    )

    assert started.handle is None
    assert started.blocking_fields == ("query",)
    assert [event["event_name"] for event in started.produced_events] == [
        "TOOL_MANIFEST_LOADED",
        "TOOL_ARGUMENTS_PARTIAL",
        "TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS",
    ]
    assert not any(event["event_name"] == "TOOL_EXECUTION_STARTED" for event in journal.events())
    assert "reviewer" not in repr(started.produced_events).lower()
