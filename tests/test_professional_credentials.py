"""Tests for professional credential profiles + tele session credential stamping
(item #7). No database, no mock data: a tiny in-memory async Mongo stand-in
drives the real service code so each session record proves *who* acted and
*under what authority and jurisdiction*.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.app.services import professional_credentials as creds
from backend.app.services import tele_session as ts


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Minimal in-memory async Mongo stand-in
# --------------------------------------------------------------------------- #
class _UpdateResult:
    def __init__(self, matched: int) -> None:
        self.matched_count = matched


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

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.docs.append(dict(document))

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> _UpdateResult:
        for doc in self.docs:
            if self._match(doc, query):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                return _UpdateResult(1)
        if upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            for key, value in update.get("$setOnInsert", {}).items():
                doc[key] = value
            for key, value in update.get("$set", {}).items():
                doc[key] = value
            self.docs.append(doc)
            return _UpdateResult(1)
        return _UpdateResult(0)


class FakeDB:
    def __init__(self) -> None:
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.setdefault(name, FakeCollection())

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


# --------------------------------------------------------------------------- #
# Pure normalization
# --------------------------------------------------------------------------- #
def test_normalize_trims_and_nulls_blanks():
    out = creds.normalize_credentials(
        {"board_cert_number": "  ABPN-123 ", "license_number": "", "license_region": "GR-Attica"}
    )
    assert out == {
        "board_cert_number": "ABPN-123",
        "license_number": None,
        "license_region": "GR-Attica",
    }


def test_normalize_handles_none():
    assert creds.normalize_credentials(None) == {
        "board_cert_number": None,
        "license_number": None,
        "license_region": None,
    }


def test_credentials_present():
    assert creds.credentials_present({"license_number": "L1"}) is True
    assert creds.credentials_present({"license_number": "  "}) is False
    assert creds.credentials_present(None) is False


# --------------------------------------------------------------------------- #
# Profile persistence
# --------------------------------------------------------------------------- #
def test_get_user_credentials_returns_persisted_fields():
    db = FakeDB()
    run(
        db["admin_users"].insert_one(
            {
                "user_id": "u1",
                "board_cert_number": "ABPN-9",
                "license_number": "LIC-42",
                "license_region": "GR",
            }
        )
    )
    out = run(creds.get_user_credentials(db, "u1"))
    assert out == {"board_cert_number": "ABPN-9", "license_number": "LIC-42", "license_region": "GR"}


def test_get_user_credentials_for_env_admin_is_empty():
    db = FakeDB()
    assert run(creds.get_user_credentials(db, None)) == {
        "board_cert_number": None,
        "license_number": None,
        "license_region": None,
    }


def test_set_user_credentials_persists():
    db = FakeDB()
    run(db["admin_users"].insert_one({"user_id": "u1"}))
    out = run(
        creds.set_user_credentials(
            db, user_id="u1", data={"license_number": "LIC-7", "license_region": "GR-Crete"}
        )
    )
    assert out["license_number"] == "LIC-7"
    stored = run(db["admin_users"].find_one({"user_id": "u1"}))
    assert stored["license_number"] == "LIC-7"
    assert stored["license_region"] == "GR-Crete"
    assert "credentials_updated_at" in stored


def test_set_user_credentials_rejects_env_admin():
    db = FakeDB()
    with pytest.raises(creds.CredentialError):
        run(creds.set_user_credentials(db, user_id=None, data={"license_number": "X"}))


def test_set_user_credentials_unknown_user():
    db = FakeDB()
    with pytest.raises(creds.CredentialError):
        run(creds.set_user_credentials(db, user_id="ghost", data={"license_number": "X"}))


def test_enrich_identity_attaches_credentials():
    db = FakeDB()
    run(db["admin_users"].insert_one({"user_id": "u1", "license_number": "LIC-1"}))
    identity = {"user_id": "u1", "display_name": "Dr A", "tenant_id": "default"}
    enriched = run(creds.enrich_identity(db, identity))
    assert enriched["credentials"]["license_number"] == "LIC-1"
    assert enriched["display_name"] == "Dr A"


# --------------------------------------------------------------------------- #
# Session document stamping (pure factory)
# --------------------------------------------------------------------------- #
def test_new_session_stamps_initiator_credentials():
    doc = ts.new_session_document(
        initiator={"user_id": "u1", "display_name": "Dr A", "role": "user", "tenant_id": "default"},
        node_key="ssep_upper",
        case_id="case-1",
        message="need eyes",
        initiator_credentials={"board_cert_number": "ABPN-9", "license_number": "LIC-42", "license_region": "GR"},
    )
    stamp = doc["initiator_credentials"]
    assert stamp["board_cert_number"] == "ABPN-9"
    assert stamp["license_number"] == "LIC-42"
    assert stamp["license_region"] == "GR"
    assert stamp["on_file"] is True
    assert stamp["snapshot_at"] is not None
    assert doc["consultant_credentials"] is None


def test_new_session_credentials_from_identity_when_not_passed():
    doc = ts.new_session_document(
        initiator={
            "user_id": "u1",
            "display_name": "Dr A",
            "role": "user",
            "tenant_id": "default",
            "credentials": {"license_number": "LIC-99"},
        },
        node_key=None,
        case_id=None,
        message=None,
    )
    assert doc["initiator_credentials"]["license_number"] == "LIC-99"
    assert doc["initiator_credentials"]["on_file"] is True


def test_new_session_empty_credentials_marked_not_on_file():
    doc = ts.new_session_document(
        initiator={"user_id": "u1", "display_name": "Dr A", "role": "admin", "tenant_id": "default"},
        node_key=None,
        case_id=None,
        message=None,
    )
    assert doc["initiator_credentials"]["on_file"] is False
    assert doc["initiator_credentials"]["license_number"] is None


# --------------------------------------------------------------------------- #
# End-to-end stamping through create + accept
# --------------------------------------------------------------------------- #
def test_create_and_accept_stamp_both_credentials():
    db = FakeDB()
    initiator = {"user_id": "u1", "display_name": "Dr A", "role": "user", "access_level": "clinician", "tenant_id": "default"}
    consultant = {"user_id": "u2", "display_name": "Dr B", "role": "user", "access_level": "clinician", "tenant_id": "default"}

    session = run(
        ts.create_session(
            db,
            initiator=initiator,
            node_key="ssep_upper",
            case_id="case-1",
            message="help",
            initiator_credentials={"board_cert_number": "ABPN-1", "license_number": "LIC-A", "license_region": "GR-Attica"},
        )
    )
    assert session["initiator_credentials"]["license_number"] == "LIC-A"
    assert session["initiator_credentials"]["license_region"] == "GR-Attica"

    accepted = run(
        ts.accept_session(
            db,
            session_id=session["session_id"],
            consultant=consultant,
            consultant_credentials={"board_cert_number": "ABPN-2", "license_number": "LIC-B", "license_region": "US-CA"},
        )
    )
    assert accepted["consultant_credentials"]["license_number"] == "LIC-B"
    assert accepted["consultant_credentials"]["license_region"] == "US-CA"
    assert accepted["consultant_credentials"]["on_file"] is True
    # The initiator stamp survives the accept update untouched.
    assert accepted["initiator_credentials"]["license_number"] == "LIC-A"
