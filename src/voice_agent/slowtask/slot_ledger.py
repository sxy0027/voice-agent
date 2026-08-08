from __future__ import annotations

"""Replayable slot ledger projection for SlowTask requirement facts."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SlotState(str, Enum):
    UNKNOWN = "UNKNOWN"
    CANDIDATE = "CANDIDATE"
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    DEFAULTED = "DEFAULTED"


@dataclass(frozen=True)
class SlotEvidence:
    evidence_ref: str
    raw_evidence: str
    source: str
    plan_version: int
    event_id: str | None = None


@dataclass(frozen=True)
class SlotRecord:
    name: str
    normalized_value: Any
    raw_evidence: str
    state: SlotState
    provenance: tuple[SlotEvidence, ...]
    explicit_or_inferred: str
    first_resolved_plan_version: int | None
    last_updated_event_id: str | None
    asked_count: int = 0
    conflicting_candidates: tuple[Any, ...] = ()

    def value_preview(self) -> str:
        if isinstance(self.normalized_value, (list, tuple)):
            if not self.normalized_value:
                return "无"
            return "、".join(str(item) for item in self.normalized_value)[:80]
        return str(self.normalized_value)[:80]


@dataclass(frozen=True)
class SlotUpdate:
    name: str
    normalized_value: Any
    raw_evidence: str
    state: SlotState
    evidence_ref: str
    source: str
    plan_version: int
    explicit_or_inferred: str = "explicit"
    event_id: str | None = None
    asked_count_delta: int = 0
    conflicting_candidates: tuple[Any, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "normalized_value": self.normalized_value,
            "raw_evidence": self.raw_evidence,
            "state": self.state.value,
            "evidence_ref": self.evidence_ref,
            "source": self.source,
            "plan_version": self.plan_version,
            "explicit_or_inferred": self.explicit_or_inferred,
            "event_id": self.event_id,
            "asked_count_delta": self.asked_count_delta,
            "conflicting_candidates": list(self.conflicting_candidates),
        }

    @classmethod
    def from_metadata(cls, value: Mapping[str, Any]) -> "SlotUpdate":
        return cls(
            name=str(value["name"]),
            normalized_value=value.get("normalized_value"),
            raw_evidence=str(value.get("raw_evidence", "")),
            state=SlotState(str(value.get("state", SlotState.UNKNOWN.value))),
            evidence_ref=str(value.get("evidence_ref", "")),
            source=str(value.get("source", "unknown")),
            plan_version=int(value.get("plan_version", 1)),
            explicit_or_inferred=str(value.get("explicit_or_inferred", "explicit")),
            event_id=None if value.get("event_id") in (None, "") else str(value.get("event_id")),
            asked_count_delta=int(value.get("asked_count_delta", 0)),
            conflicting_candidates=tuple(value.get("conflicting_candidates", ()) or ()),
        )


@dataclass(frozen=True)
class SlotLedger:
    records: Mapping[str, SlotRecord] = field(default_factory=dict)

    def value_map(self, *, resolved_only: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, record in self.records.items():
            if resolved_only and record.state != SlotState.RESOLVED:
                continue
            result[name] = record.normalized_value
        return result

    def resolved_fields(self) -> tuple[str, ...]:
        return tuple(name for name, record in self.records.items() if record.state == SlotState.RESOLVED)

    def summary(self, labels: Mapping[str, str] | None = None) -> tuple[dict[str, Any], ...]:
        label_map = dict(labels or {})
        rows: list[dict[str, Any]] = []
        for name in sorted(self.records):
            record = self.records[name]
            provenance = record.provenance[-1] if record.provenance else None
            rows.append(
                {
                    "name": name,
                    "label": label_map.get(name, name),
                    "state": record.state.value,
                    "value_preview": record.value_preview(),
                    "source": provenance.source if provenance else "unknown",
                    "required_for": [],
                    "asked_count": record.asked_count,
                }
            )
        return tuple(rows)


def build_slot_ledger(
    updates: Sequence[SlotUpdate],
    *,
    asked_counts: Mapping[str, int] | None = None,
) -> SlotLedger:
    asked = dict(asked_counts or {})
    records: dict[str, SlotRecord] = {}
    for update in updates:
        if not update.name:
            continue
        previous = records.get(update.name)
        provenance = SlotEvidence(
            evidence_ref=update.evidence_ref,
            raw_evidence=update.raw_evidence,
            source=update.source,
            plan_version=update.plan_version,
            event_id=update.event_id,
        )
        first_resolved = (
            update.plan_version
            if update.state == SlotState.RESOLVED and (previous is None or previous.first_resolved_plan_version is None)
            else previous.first_resolved_plan_version if previous is not None else None
        )
        conflicting = tuple(update.conflicting_candidates)
        if previous is not None and previous.state == SlotState.CONFLICTING:
            conflicting = (*previous.conflicting_candidates, update.normalized_value)
        records[update.name] = SlotRecord(
            name=update.name,
            normalized_value=update.normalized_value,
            raw_evidence=update.raw_evidence,
            state=update.state,
            provenance=((*previous.provenance, provenance) if previous is not None else (provenance,)),
            explicit_or_inferred=update.explicit_or_inferred,
            first_resolved_plan_version=first_resolved,
            last_updated_event_id=update.event_id,
            asked_count=asked.get(update.name, previous.asked_count if previous is not None else 0)
            + update.asked_count_delta,
            conflicting_candidates=conflicting,
        )
    return SlotLedger(records=records)


def updates_from_evidence_catalog(
    evidence_catalog: Mapping[str, Mapping[str, Any]],
    *,
    task_id: str | None = None,
) -> tuple[SlotUpdate, ...]:
    updates: list[SlotUpdate] = []
    for evidence_ref, item in evidence_catalog.items():
        if task_id is not None and str(item.get("task_id", "")) != task_id:
            continue
        raw_updates = item.get("slot_updates", ())
        if not isinstance(raw_updates, Sequence) or isinstance(raw_updates, (str, bytes)):
            continue
        for raw_update in raw_updates:
            if isinstance(raw_update, Mapping):
                payload = dict(raw_update)
                payload.setdefault("evidence_ref", evidence_ref)
                payload.setdefault("source", item.get("source", "unknown"))
                payload.setdefault("plan_version", item.get("plan_version", 1))
                updates.append(SlotUpdate.from_metadata(payload))
    return tuple(updates)


__all__ = [
    "SlotEvidence",
    "SlotLedger",
    "SlotRecord",
    "SlotState",
    "SlotUpdate",
    "build_slot_ledger",
    "updates_from_evidence_catalog",
]
