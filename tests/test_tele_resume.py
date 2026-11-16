"""Tests for Tele transport-reliability resume logic (item #6).

Pure resume eligibility + message validation are tested directly; the resumable-
session lookup is driven by an in-memory async Mongo stand-in supporting the
find().sort().limit().to_list() chain with $in / $or operators.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.services import tele_session as ts


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Pure resume eligibility
# --------------------------------------------------------------------------- #
def test_is_resumable_status():
    assert ts.is_resumable_status("requested") is True
    assert ts.is_resumable_status("accepted") is True
    assert ts.is_resumable_status("active") is True
    assert ts.is_resumable_status("ended") is False
    assert ts.is_resumable_status("cancelled") is False


def test_can_resume_participant():
    session = {"status": "active", "initiator_id": "u1", "consultant_id": "u2"}
    assert ts.can_resume(session, {"user_id": "u1", "role": "user"}) is True
    assert ts.can_resume(session, {"user_id": "u2", "role": "user"}) is True


def test_can_resume_admin_even_if_not_participant():
    session = {"status": "accepted", "initiator_id": "u1", "consultant_id": "u2"}
    assert ts.can_resume(session, {"user_id": "x", "role": "admin"}) is True


def test_cannot_resume_non_participant():
    session = {"status": "active", "initiator_id": "u1", "consultant_id": "u2"}
    assert ts.can_resume(session, {"user_id": "stranger", "role": "user"}) is False


def test_cannot_resume_ended_session():
    session = {"status": "ended", "initiator_id": "u1", "consultant_id": "u2"}
    assert ts.can_resume(session, {"user_id": "u1", "role": "user"}) is False


def test_cannot_resume_missing_session():
    assert ts.can_resume(None, {"user_id": "u1", "role": "user"}) is False


def test_validate_resume_message_requires_session_id():
    with pytest.raises(ts.TeleError):
        ts.validate_client_message({"type": "resume"})
    out = ts.validate_client_message({"type": "resume", "session_id": "sess-1"})
    assert out == {"type": "resume", "session_id": "sess-1"}


# --------------------------------------------------------------------------- #
# Resumable-session lookup (IO)
# --------------------------------------------------------------------------- #
class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, key: str, direction: int) -> "FakeCursor":
        self._docs = sorted(
            self._docs,
            key=lambda d: d.get(key) or datetime.min.replace(tzinfo=UTC),
            reverse=direction < 0,
        )
        return self

    def limit(self, n: int) -> "FakeCursor":
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [dict(doc) for doc in self._docs[: length or len(self._docs)]]


class FakeSessions:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    @staticmethod
    def _match(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, value in query.items():
            if key == "$or":
                if not any(FakeSessions._match(doc, clause) for clause in value):
                    return False
            elif isinstance(value, dict) and "$in" in value:
                if doc.get(key) not in value["$in"]:
                    return False
            elif doc.get(key) != value:
                return False
        return True

    def find(self, query: dict[str, Any]) -> FakeCursor:
        return FakeCursor([doc for doc in self.docs if self._match(doc, query)])


class FakeDB:
    def __init__(self) -> None:
        self.tele_sessions = FakeSessions()


def test_find_resumable_session_returns_latest_active_for_participant():
    db = FakeDB()
    now = datetime.now(UTC)
    db.tele_sessions.docs = [
        {"session_id": "old", "tenant_id": "default", "status": "ended", "initiator_id": "u1", "consultant_id": "u2", "updated_at": now - timedelta(hours=2)},
        {"session_id": "live", "tenant_id": "default", "status": "active", "initiator_id": "u1", "consultant_id": "u2", "updated_at": now - timedelta(minutes=5)},
        {"session_id": "older_live", "tenant_id": "default", "status": "accepted", "initiator_id": "u3", "consultant_id": "u1", "updated_at": now - timedelta(hours=1)},
    ]
    found = run(ts.find_resumable_session_for_user(db, user_id="u1", tenant_id="default"))
    assert found is not None
    assert found["session_id"] == "live"


def test_find_resumable_session_none_when_all_ended():
    db = FakeDB()
    now = datetime.now(UTC)
    db.tele_sessions.docs = [
        {"session_id": "x", "tenant_id": "default", "status": "ended", "initiator_id": "u1", "consultant_id": "u2", "updated_at": now},
    ]
    assert run(ts.find_resumable_session_for_user(db, user_id="u1", tenant_id="default")) is None
