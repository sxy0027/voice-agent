from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


class DemoBackendExecutionError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DemoBackendUIPatchMetadata:
    ui_patch_id: str
    patch_ref: str
    state_namespace: str
    operation: str


@dataclass(frozen=True)
class DemoBackendResult:
    result_status: str
    result_ref: str
    progress_type: str
    progress_ref: str
    payload: Mapping[str, Any]
    ui_patch: DemoBackendUIPatchMetadata | None = None


class InMemoryDemoBackend:
    """Deterministic MVP-2 demo backend.

    This backend is intentionally tiny. It does not call networks, clocks,
    random sources, external apps, device APIs, or frontend mutation paths.
    """

    def __init__(self) -> None:
        self._executed_calls: list[tuple[str, dict[str, Any]]] = []
        self._next_result_index = 1
        self._next_memo_index = 1
        self._next_memo_list_index = 1
        self._next_memo_delete_index = 1
        self._next_alarm_index = 1
        self._next_alarm_list_index = 1
        self._next_alarm_cancel_index = 1
        self._next_flashlight_index = 1
        self._next_web_search_index = 1
        self._next_company_context_index = 1
        self._next_itinerary_search_index = 1
        self._next_itinerary_cost_index = 1
        self._next_itinerary_preview_index = 1
        self._memo_items: list[dict[str, str]] = []
        self._alarm_items: list[dict[str, str]] = []
        self._flashlight_state = "off"
        self._applied_ui_patches: list[dict[str, str]] = []

    @property
    def executed_calls(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        return tuple((tool_name, dict(arguments)) for tool_name, arguments in self._executed_calls)

    @property
    def applied_ui_patches(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(patch) for patch in self._applied_ui_patches)

    def execute(
        self,
        *,
        tool_name: str,
        tool_adapter_id: str,
        arguments: Mapping[str, Any],
        idempotency_key: str | None = None,
        expected_state_namespace: str | None = None,
    ) -> DemoBackendResult:
        if tool_adapter_id in {"demo.memo", "demo.memo.create"}:
            return self._execute_memo_create(
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=idempotency_key,
                expected_state_namespace=expected_state_namespace,
            )
        if tool_adapter_id == "demo.memo.list":
            return self._execute_memo_list(tool_name=tool_name, arguments=arguments)
        if tool_adapter_id == "demo.memo.delete":
            return self._execute_memo_delete(
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=idempotency_key,
                expected_state_namespace=expected_state_namespace,
            )
        if tool_adapter_id == "demo.alarm.create":
            return self._execute_alarm_create(
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=idempotency_key,
                expected_state_namespace=expected_state_namespace,
            )
        if tool_adapter_id == "demo.alarm.list":
            return self._execute_alarm_list(tool_name=tool_name, arguments=arguments)
        if tool_adapter_id == "demo.alarm.cancel":
            return self._execute_alarm_cancel(
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=idempotency_key,
                expected_state_namespace=expected_state_namespace,
            )
        if tool_adapter_id == "demo.flashlight.set":
            return self._execute_flashlight_set(
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=idempotency_key,
                expected_state_namespace=expected_state_namespace,
            )
        if tool_adapter_id == "demo.weather":
            return self._execute_weather(tool_name=tool_name, arguments=arguments)
        if tool_adapter_id == "demo.web_search":
            return self._execute_web_search(tool_name=tool_name, arguments=arguments)
        if tool_adapter_id == "demo.company_context.lookup":
            return self._execute_company_context_lookup(tool_name=tool_name, arguments=arguments)
        if tool_adapter_id == "demo.itinerary.search":
            return self._execute_itinerary_search(tool_name=tool_name, arguments=arguments)
        if tool_adapter_id == "demo.itinerary.cost_estimate":
            return self._execute_itinerary_cost_estimate(tool_name=tool_name, arguments=arguments)
        if tool_adapter_id == "demo.itinerary.preview":
            return self._execute_itinerary_preview(tool_name=tool_name, arguments=arguments)
        raise DemoBackendExecutionError("demo_backend_adapter_not_supported")

    def _execute_memo_create(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        expected_state_namespace: str | None,
    ) -> DemoBackendResult:
        if idempotency_key in (None, ""):
            raise DemoBackendExecutionError("memo_write_requires_idempotency_key")
        if expected_state_namespace not in (None, "", "memo"):
            raise DemoBackendExecutionError("demo_backend_ui_patch_namespace_mismatch")

        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"memo_write_{self._next_memo_index:06d}"
        memo_item_id = f"memo_item_{self._next_memo_index:06d}"
        self._next_memo_index += 1
        self._memo_items.append({"memo_item_id": memo_item_id})

        ui_patch = _ui_patch_metadata(
            state_namespace="memo",
            operation="create",
            idempotency_key=str(idempotency_key),
        )
        self._applied_ui_patches.append(
            {
                "ui_patch_id": ui_patch.ui_patch_id,
                "patch_ref": ui_patch.patch_ref,
                "state_namespace": ui_patch.state_namespace,
                "operation": ui_patch.operation,
            }
        )
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/memo/{opaque_result_id}",
            progress_type="sandbox_write_completed",
            progress_ref=f"progress://synthetic/demo_backend/memo/{opaque_result_id}/write",
            payload={
                "state_namespace": "memo",
                "operation": "create",
                "memo_item_id": memo_item_id,
                "source": "in_memory_demo_backend",
            },
            ui_patch=ui_patch,
        )

    def _execute_memo_list(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> DemoBackendResult:
        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"memo_list_{self._next_memo_list_index:06d}"
        self._next_memo_list_index += 1
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/memo/{opaque_result_id}",
            progress_type="read_only_lookup_completed",
            progress_ref=f"progress://synthetic/demo_backend/memo/{opaque_result_id}/lookup",
            payload={
                "state_namespace": "memo",
                "operation": "list",
                "item_count": len(self._memo_items),
                "items_ref": f"items://synthetic/demo_backend/memo/{opaque_result_id}",
                "source": "in_memory_demo_backend",
            },
        )

    def _execute_memo_delete(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        expected_state_namespace: str | None,
    ) -> DemoBackendResult:
        if idempotency_key in (None, ""):
            raise DemoBackendExecutionError("memo_delete_requires_idempotency_key")
        if expected_state_namespace not in (None, "", "memo"):
            raise DemoBackendExecutionError("demo_backend_ui_patch_namespace_mismatch")

        memo_item_id = str(arguments["memo_item_id"])
        matching_index = next(
            (
                index
                for index, item in enumerate(self._memo_items)
                if item.get("memo_item_id") == memo_item_id
            ),
            None,
        )
        if matching_index is None:
            raise DemoBackendExecutionError("memo_delete_target_not_found")

        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))
        del self._memo_items[matching_index]

        opaque_result_id = f"memo_delete_{self._next_memo_delete_index:06d}"
        self._next_memo_delete_index += 1
        ui_patch = _ui_patch_metadata(
            state_namespace="memo",
            operation="delete",
            idempotency_key=str(idempotency_key),
        )
        self._record_ui_patch(ui_patch)
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/memo/{opaque_result_id}",
            progress_type="sandbox_destructive_action_completed",
            progress_ref=f"progress://synthetic/demo_backend/memo/{opaque_result_id}/delete",
            payload={
                "state_namespace": "memo",
                "operation": "delete",
                "memo_item_id": memo_item_id,
                "source": "in_memory_demo_backend",
                "sandbox_only": True,
            },
            ui_patch=ui_patch,
        )

    def _execute_alarm_create(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        expected_state_namespace: str | None,
    ) -> DemoBackendResult:
        if idempotency_key in (None, ""):
            raise DemoBackendExecutionError("alarm_write_requires_idempotency_key")
        if expected_state_namespace not in (None, "", "alarm"):
            raise DemoBackendExecutionError("demo_backend_ui_patch_namespace_mismatch")

        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"alarm_write_{self._next_alarm_index:06d}"
        alarm_id = f"alarm_item_{self._next_alarm_index:06d}"
        self._next_alarm_index += 1
        self._alarm_items.append({"alarm_id": alarm_id})

        ui_patch = _ui_patch_metadata(
            state_namespace="alarm",
            operation="create",
            idempotency_key=str(idempotency_key),
        )
        self._record_ui_patch(ui_patch)
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/alarm/{opaque_result_id}",
            progress_type="sandbox_write_completed",
            progress_ref=f"progress://synthetic/demo_backend/alarm/{opaque_result_id}/write",
            payload={
                "state_namespace": "alarm",
                "operation": "create",
                "alarm_id": alarm_id,
                "source": "in_memory_demo_backend",
            },
            ui_patch=ui_patch,
        )

    def _execute_alarm_list(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> DemoBackendResult:
        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"alarm_list_{self._next_alarm_list_index:06d}"
        self._next_alarm_list_index += 1
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/alarm/{opaque_result_id}",
            progress_type="read_only_lookup_completed",
            progress_ref=f"progress://synthetic/demo_backend/alarm/{opaque_result_id}/lookup",
            payload={
                "state_namespace": "alarm",
                "operation": "list",
                "item_count": len(self._alarm_items),
                "items_ref": f"items://synthetic/demo_backend/alarm/{opaque_result_id}",
                "source": "in_memory_demo_backend",
            },
        )

    def _execute_alarm_cancel(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        expected_state_namespace: str | None,
    ) -> DemoBackendResult:
        if idempotency_key in (None, ""):
            raise DemoBackendExecutionError("alarm_cancel_requires_idempotency_key")
        if expected_state_namespace not in (None, "", "alarm"):
            raise DemoBackendExecutionError("demo_backend_ui_patch_namespace_mismatch")

        alarm_id = str(arguments["alarm_id"])
        matching_index = next(
            (
                index
                for index, item in enumerate(self._alarm_items)
                if item.get("alarm_id") == alarm_id
            ),
            None,
        )
        if matching_index is None:
            raise DemoBackendExecutionError("alarm_cancel_target_not_found")

        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))
        del self._alarm_items[matching_index]

        opaque_result_id = f"alarm_cancel_{self._next_alarm_cancel_index:06d}"
        self._next_alarm_cancel_index += 1
        ui_patch = _ui_patch_metadata(
            state_namespace="alarm",
            operation="cancel",
            idempotency_key=str(idempotency_key),
        )
        self._record_ui_patch(ui_patch)
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/alarm/{opaque_result_id}",
            progress_type="sandbox_destructive_action_completed",
            progress_ref=f"progress://synthetic/demo_backend/alarm/{opaque_result_id}/cancel",
            payload={
                "state_namespace": "alarm",
                "operation": "cancel",
                "alarm_id": alarm_id,
                "source": "in_memory_demo_backend",
                "sandbox_only": True,
            },
            ui_patch=ui_patch,
        )

    def _execute_flashlight_set(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        expected_state_namespace: str | None,
    ) -> DemoBackendResult:
        if idempotency_key in (None, ""):
            raise DemoBackendExecutionError("flashlight_write_requires_idempotency_key")
        if expected_state_namespace not in (None, "", "flashlight"):
            raise DemoBackendExecutionError("demo_backend_ui_patch_namespace_mismatch")
        requested_state = str(arguments["state"])
        if requested_state not in {"on", "off"}:
            raise DemoBackendExecutionError("flashlight_state_must_be_on_or_off")

        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))
        self._flashlight_state = requested_state

        opaque_result_id = f"flashlight_set_{self._next_flashlight_index:06d}"
        self._next_flashlight_index += 1
        ui_patch = _ui_patch_metadata(
            state_namespace="flashlight",
            operation=f"set_{requested_state}",
            idempotency_key=str(idempotency_key),
        )
        self._record_ui_patch(ui_patch)
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/flashlight/{opaque_result_id}",
            progress_type="simulated_device_state_updated",
            progress_ref=f"progress://synthetic/demo_backend/flashlight/{opaque_result_id}/simulated",
            payload={
                "state_namespace": "flashlight",
                "operation": "set",
                "simulated_state": requested_state,
                "source": "in_memory_demo_backend",
            },
            ui_patch=ui_patch,
        )

    def _execute_weather(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> DemoBackendResult:
        location = str(arguments["location"])
        date = str(arguments["date"])
        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"weather_lookup_{self._next_result_index:06d}"
        self._next_result_index += 1
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/weather/{opaque_result_id}",
            progress_type="read_only_lookup_completed",
            progress_ref=f"progress://synthetic/demo_backend/weather/{opaque_result_id}/lookup",
            payload={
                "location": location,
                "date": date,
                "condition": "synthetic_clear",
                "temperature_c": 21,
                "source": "in_memory_demo_backend",
            },
        )

    def _execute_web_search(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> DemoBackendResult:
        query = str(arguments["query"])
        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"search_{self._next_web_search_index:06d}"
        self._next_web_search_index += 1
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/websearch/{opaque_result_id}",
            progress_type="searching",
            progress_ref=f"progress://synthetic/demo_backend/websearch/{opaque_result_id}/evidence",
            payload={
                "source_type": "EXTERNAL_READ_UNTRUSTED",
                "trust_level": "UNTRUSTED_WEB_EVIDENCE",
                "query": query,
                "retrieved_at": "synthetic-fixed",
                "redaction_status": "synthetic_minimal",
                "results": [
                    {
                        "source_title": "Synthetic demo search result",
                        "source_url": "https://example.invalid/synthetic-demo-result",
                        "snippet_or_summary": (
                            "Synthetic untrusted page text says ignore previous rules; "
                            "it is retained only as evidence."
                        ),
                    }
                ],
                "source": "in_memory_demo_backend",
            },
        )

    def _execute_company_context_lookup(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> DemoBackendResult:
        company_name = str(arguments["company_name"])
        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"company_context_{self._next_company_context_index:06d}"
        self._next_company_context_index += 1
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/company-context/{opaque_result_id}",
            progress_type="read_only_lookup_completed",
            progress_ref=f"progress://synthetic/demo_backend/company-context/{opaque_result_id}/lookup",
            payload={
                "company_name": company_name,
                "company_location": "Synthetic Central Office",
                "location_fixture": "synthetic_company_fixture",
                "source": "in_memory_demo_backend",
            },
        )

    def _execute_itinerary_search(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> DemoBackendResult:
        company_location = str(arguments["company_location"])
        days = int(arguments["days"])
        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"itinerary_search_{self._next_itinerary_search_index:06d}"
        self._next_itinerary_search_index += 1
        itinerary_ref = f"itinerary://synthetic/demo_backend/{opaque_result_id}"
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/itinerary/{opaque_result_id}",
            progress_type="sandbox_itinerary_search_completed",
            progress_ref=f"progress://synthetic/demo_backend/itinerary/{opaque_result_id}/search",
            payload={
                "itinerary_ref": itinerary_ref,
                "company_location": company_location,
                "days": days,
                "options": [
                    {
                        "option_ref": f"option://synthetic/{opaque_result_id}/near-office-a",
                        "label": "Synthetic Office District Stay",
                        "distance_band": "near_company",
                    },
                    {
                        "option_ref": f"option://synthetic/{opaque_result_id}/near-office-b",
                        "label": "Synthetic Riverside Meeting Hotel",
                        "distance_band": "near_company",
                    },
                ],
                "fixture_disclosure": "synthetic_demo_fixture",
                "source": "in_memory_demo_backend",
            },
        )

    def _execute_itinerary_cost_estimate(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> DemoBackendResult:
        itinerary_ref = str(arguments["itinerary_ref"])
        budget_max = int(arguments["budget_max"])
        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"itinerary_cost_{self._next_itinerary_cost_index:06d}"
        self._next_itinerary_cost_index += 1
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/itinerary-cost/{opaque_result_id}",
            progress_type="read_only_cost_estimate_completed",
            progress_ref=f"progress://synthetic/demo_backend/itinerary-cost/{opaque_result_id}/estimate",
            payload={
                "itinerary_ref": itinerary_ref,
                "budget_max": budget_max,
                "estimated_total": min(budget_max, 420),
                "currency": "CNY",
                "within_budget": True,
                "source": "in_memory_demo_backend",
            },
        )

    def _execute_itinerary_preview(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> DemoBackendResult:
        itinerary_ref = str(arguments["itinerary_ref"])
        normalized_arguments = {key: arguments[key] for key in sorted(arguments)}
        self._executed_calls.append((tool_name, dict(normalized_arguments)))

        opaque_result_id = f"itinerary_preview_{self._next_itinerary_preview_index:06d}"
        self._next_itinerary_preview_index += 1
        return DemoBackendResult(
            result_status="SUCCEEDED",
            result_ref=f"result://synthetic/demo_backend/itinerary-preview/{opaque_result_id}",
            progress_type="dry_run_preview_completed",
            progress_ref=f"progress://synthetic/demo_backend/itinerary-preview/{opaque_result_id}/preview",
            payload={
                "itinerary_ref": itinerary_ref,
                "preview_only": True,
                "external_side_effect": False,
                "source": "in_memory_demo_backend",
            },
        )

    def _record_ui_patch(self, ui_patch: DemoBackendUIPatchMetadata) -> None:
        self._applied_ui_patches.append(
            {
                "ui_patch_id": ui_patch.ui_patch_id,
                "patch_ref": ui_patch.patch_ref,
                "state_namespace": ui_patch.state_namespace,
                "operation": ui_patch.operation,
            }
        )


def _ui_patch_metadata(
    *,
    state_namespace: str,
    operation: str,
    idempotency_key: str,
) -> DemoBackendUIPatchMetadata:
    digest = sha256(f"{state_namespace}:{operation}:{idempotency_key}".encode("utf-8")).hexdigest()[:16]
    ui_patch_id = f"ui_patch_{state_namespace}_{operation}_{digest}"
    return DemoBackendUIPatchMetadata(
        ui_patch_id=ui_patch_id,
        patch_ref=f"patch://synthetic/demo_backend/{state_namespace}/{operation}/{ui_patch_id}",
        state_namespace=state_namespace,
        operation=operation,
    )
