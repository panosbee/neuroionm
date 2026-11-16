"""Tests for the expert on-call rota, escalation plan and summons ledger
(item #3). No database, no mock data: pure schedule/escalation logic is tested
directly, and the ledger IO is driven by a tiny in-memory async Mongo stand-in.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.app.services import expert_rota as rota


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# In-memory async Mongo stand-in
# --------------------------------------------------------------------------- #
class _DeleteResult:
    def __init__(self, deleted: int) -> None:
        self.deleted_count = deleted


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [dict(doc) for doc in self._docs[: length or len(self._docs)]]


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None

    @staticmethod
    def _match(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, value in query.items():
            if doc.get(key) != value:
                return False
        return True

    def find(self, query: dict[str, Any]) -> FakeCursor:
        return FakeCursor([doc for doc in self.docs if self._match(doc, query)])

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.docs.append(dict(document))

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        for doc in self.docs:
            if self._match(doc, query):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                for key, value in update.get("$push", {}).items():
                    doc.setdefault(key, []).append(value)
                return

    async def delete_one(self, query: dict[str, Any]) -> _DeleteResult:
        for index, doc in enumerate(self.docs):
            if self._match(doc, query):
                del self.docs[index]
                return _DeleteResult(1)
        return _DeleteResult(0)


class FakeDB:
    def __init__(self) -> None:
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.setdefault(name, FakeCollection())


# --------------------------------------------------------------------------- #
# Schedule normalization + on-call evaluation (pure)
# --------------------------------------------------------------------------- #
def test_recurring_schedule_validation():
    schedule = rota.normalize_schedule(
        {"kind": "recurring", "weekdays": [0, 2, 4], "start_minute": 480, "end_minute": 1020, "tz_offset_minutes": 180}
    )
    assert schedule["weekdays"] == [0, 2, 4]
    assert schedule["start_minute"] == 480


def test_recurring_rejects_bad_window():
    with pytest.raises(rota.RotaError):
        rota.normalize_schedule({"kind": "recurring", "weekdays": [0], "start_minute": 600, "end_minute": 600})


def test_recurring_daytime_window_local_time():
    # Mon 08:00-17:00 local (UTC+3). At 2024-01-01 09:00 local == 06:00 UTC -> on call.
    schedule = rota.normalize_schedule(
        {"kind": "recurring", "weekdays": [0], "start_minute": 480, "end_minute": 1020, "tz_offset_minutes": 180}
    )
    on = datetime(2024, 1, 1, 6, 0, tzinfo=UTC)  # Monday 09:00 local
    off = datetime(2024, 1, 1, 15, 0, tzinfo=UTC)  # Monday 18:00 local
    assert rota.schedule_is_active(schedule, on) is True
    assert rota.schedule_is_active(schedule, off) is False


def test_recurring_overnight_window_wraps_midnight():
    # Fri 22:00 -> Sat 06:00 local, UTC offset 0.
    schedule = rota.normalize_schedule(
        {"kind": "recurring", "weekdays": [4], "start_minute": 1320, "end_minute": 1800, "tz_offset_minutes": 0}
    )
    fri_night = datetime(2024, 1, 5, 23, 0, tzinfo=UTC)  # Friday 23:00
    sat_early = datetime(2024, 1, 6, 3, 0, tzinfo=UTC)  # Saturday 03:00
    sat_late = datetime(2024, 1, 6, 9, 0, tzinfo=UTC)  # Saturday 09:00 (out)
    assert rota.schedule_is_active(schedule, fri_night) is True
    assert rota.schedule_is_active(schedule, sat_early) is True
    assert rota.schedule_is_active(schedule, sat_late) is False


def test_always_schedule_always_active():
    schedule = rota.normalize_schedule({"kind": "always"})
    assert rota.schedule_is_active(schedule, datetime.now(UTC)) is True


def test_window_schedule():
    schedule = rota.normalize_schedule(
        {"kind": "window", "start": "2024-01-01T10:00:00Z", "end": "2024-01-01T12:00:00Z"}
    )
    assert rota.schedule_is_active(schedule, datetime(2024, 1, 1, 11, 0, tzinfo=UTC)) is True
    assert rota.schedule_is_active(schedule, datetime(2024, 1, 1, 13, 0, tzinfo=UTC)) is False


def test_modality_cover():
    assert rota.entry_covers_modality({"modalities": []}, "ssep_upper") is True
    assert rota.entry_covers_modality({"modalities": ["ssep_upper"]}, "ssep_upper") is True
    assert rota.entry_covers_modality({"modalities": ["baep"]}, "ssep_upper") is False


# --------------------------------------------------------------------------- #
# Resolution + escalation plan (pure)
# --------------------------------------------------------------------------- #
def _entry(expert_id: str, *, tier: int, modalities: list[str] | None = None) -> dict[str, Any]:
    return {
        "expert_id": expert_id,
        "expert_name": f"Dr {expert_id}",
        "tier": tier,
        "active": True,
        "modalities": modalities or [],
        "schedule": {"kind": "always"},
        "contacts": [{"channel": "sms", "target": f"+30-{expert_id}"}],
    }


def test_resolve_orders_by_tier_and_dedups_lowest_tier():
    entries = [
        _entry("b", tier=2),
        _entry("a", tier=1),
        {**_entry("a", tier=3, modalities=["baep"])},  # same expert, higher tier -> ignored
    ]
    resolved = rota.resolve_on_call_entries(entries, modality="ssep_upper", at=datetime.now(UTC))
    assert [e["expert_id"] for e in resolved] == ["a", "b"]
    assert resolved[0]["tier"] == 1


def test_escalation_plan_marks_online_and_groups_tiers():
    on_call = [_entry("a", tier=1), _entry("b", tier=2)]
    plan = rota.build_escalation_plan(on_call, online_expert_ids={"a"})
    assert plan[0]["level"] == 1
    assert plan[0]["experts"][0]["expert_id"] == "a"
    assert plan[0]["experts"][0]["on_platform"] is True
    assert plan[1]["experts"][0]["on_platform"] is False


def test_attempt_queues_offplatform_only_for_absent_experts():
    plan = rota.build_escalation_plan([_entry("a", tier=1), _entry("b", tier=1)], online_expert_ids={"a"})
    attempt = rota.build_attempt(plan[0], at=datetime.now(UTC))
    # Only the absent expert "b" gets an off-platform dispatch intent.
    assert {d["expert_id"] for d in attempt["dispatch"]} == {"b"}
    assert attempt["dispatch"][0]["status"] == "queued"


# --------------------------------------------------------------------------- #
# Ledger IO
# --------------------------------------------------------------------------- #
def _seed_rota(db: FakeDB) -> None:
    run(
        rota.create_rota_entry(
            db,
            tenant_id="default",
            expert_id="a",
            expert_name="Dr A",
            modalities=["ssep_upper"],
            tier=1,
            schedule={"kind": "always"},
            contacts=[{"channel": "sms", "target": "+30-1"}],
        )
    )
    run(
        rota.create_rota_entry(
            db,
            tenant_id="default",
            expert_id="b",
            expert_name="Dr B",
            modalities=[],
            tier=2,
            schedule={"kind": "always"},
            contacts=[{"channel": "viber", "target": "b-handle"}],
        )
    )


def test_open_escalate_and_answer_summons():
    db = FakeDB()
    _seed_rota(db)
    summons = run(
        rota.open_summons(
            db,
            tenant_id="default",
            session_id="sess-1",
            modality="ssep_upper",
            requested_by={"user_id": "init", "display_name": "Tech"},
            online_expert_ids=set(),
        )
    )
    assert summons["status"] == "pending"
    assert summons["current_level"] == 1
    assert len(summons["attempts"]) == 1
    assert len(summons["plan"]) == 2  # two tiers

    escalated = run(rota.escalate_summons(db, summons_id=summons["summons_id"]))
    assert escalated["current_level"] == 2
    assert len(escalated["attempts"]) == 2

    exhausted = run(rota.escalate_summons(db, summons_id=summons["summons_id"]))
    assert exhausted["status"] == "exhausted"

    answered = run(
        rota.acknowledge_summons(db, summons_id=summons["summons_id"], expert={"user_id": "b", "display_name": "Dr B"})
    )
    assert answered["status"] == "answered"
    assert answered["answered_by_id"] == "b"


def test_resolve_on_call_filters_by_modality():
    db = FakeDB()
    _seed_rota(db)
    # baep is covered only by the tier-2 all-modality expert "b".
    on_call = run(rota.resolve_on_call(db, tenant_id="default", modality="baep"))
    assert [e["expert_id"] for e in on_call] == ["b"]


def test_cancel_summons_blocks_after_answer():
    db = FakeDB()
    _seed_rota(db)
    summons = run(
        rota.open_summons(
            db, tenant_id="default", session_id="s", modality=None, requested_by={"user_id": "i"}, online_expert_ids=set()
        )
    )
    run(rota.acknowledge_summons(db, summons_id=summons["summons_id"], expert={"user_id": "a", "display_name": "Dr A"}))
    with pytest.raises(rota.RotaError):
        run(rota.cancel_summons(db, summons_id=summons["summons_id"]))
