"""Tests for self-service credential management + the credential-override engine.

These prove the three guarantees the product needs for handed-over company
admins, with NO database and NO mock data — a tiny in-memory async stand-in for
the two Mongo collections the engine touches drives the real service code:

  1. A company admin (an *environment* account) can change their own password
     and username; the old credential immediately stops working.
  2. The owner can rotate that admin from the control plane: rotating the env
     credential transparently supersedes any self-service override the admin set.
  3. An override can never escalate a role — the role is always re-derived from
     the environment account, never read back from user-writable storage.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import backend.app.services.account_credentials as ac
from backend.app.services.admin_auth import hash_password, verify_password_hash


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Minimal in-memory async Mongo stand-in (only what the engine uses)
# --------------------------------------------------------------------------- #
class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None

    @staticmethod
    def _match(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, value in query.items():
            if isinstance(value, dict):
                if "$ne" in value and doc.get(key) == value["$ne"]:
                    return False
                if "$in" in value and doc.get(key) not in value["$in"]:
                    return False
            elif doc.get(key) != value:
                return False
        return True

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
                return
        if upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            for key, value in update.get("$setOnInsert", {}).items():
                doc[key] = value
            for key, value in update.get("$set", {}).items():
                doc[key] = value
            self.docs.append(doc)


class FakeDB:
    def __init__(self) -> None:
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.setdefault(name, FakeCollection())

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def use_env_accounts(monkeypatch: pytest.MonkeyPatch, accounts: list[dict[str, Any]]) -> None:
    """Pin the configured env/bootstrap accounts for both call sites."""
    monkeypatch.setattr("backend.app.services.admin_auth.admin_accounts", lambda: accounts)
    monkeypatch.setattr("backend.app.services.account_credentials.admin_accounts", lambda: accounts)


def env_admin(username: str, password: str, role: str = "admin") -> dict[str, Any]:
    return {
        "username": username,
        "display_name": f"{username} display",
        "role": role,
        "password_hash": hash_password(password),
    }


# --------------------------------------------------------------------------- #
# 1. Company admin self-service
# --------------------------------------------------------------------------- #
def test_env_admin_changes_own_password(monkeypatch: pytest.MonkeyPatch) -> None:
    account = env_admin("admin@acme", "InitialPw1")
    use_env_accounts(monkeypatch, [account])
    db = FakeDB()

    assert run(ac.authenticate_env_admin(db, "admin@acme", "InitialPw1"))["role"] == "admin"

    session = {"sub": "admin@acme", "role": "admin"}
    result = run(ac.change_password(db, session, "InitialPw1", "BrandNewPw2"))
    assert result["username"] == "admin@acme"
    assert result["role"] == "admin"

    # Old password is dead, new password authenticates.
    assert run(ac.authenticate_env_admin(db, "admin@acme", "InitialPw1")) is None
    assert run(ac.authenticate_env_admin(db, "admin@acme", "BrandNewPw2"))["role"] == "admin"


def test_env_admin_password_requires_correct_current(monkeypatch: pytest.MonkeyPatch) -> None:
    account = env_admin("admin@acme", "InitialPw1")
    use_env_accounts(monkeypatch, [account])
    db = FakeDB()
    session = {"sub": "admin@acme", "role": "admin"}
    with pytest.raises(ac.CredentialError):
        run(ac.change_password(db, session, "WrongCurrent", "BrandNewPw2"))
    # Original credential is untouched.
    assert run(ac.authenticate_env_admin(db, "admin@acme", "InitialPw1"))["role"] == "admin"


def test_env_admin_changes_own_username(monkeypatch: pytest.MonkeyPatch) -> None:
    account = env_admin("admin@acme", "InitialPw1")
    use_env_accounts(monkeypatch, [account])
    db = FakeDB()
    session = {"sub": "admin@acme", "role": "admin"}

    result = run(ac.change_username(db, session, "InitialPw1", "boss@acme"))
    assert result["username"] == "boss@acme"

    # Old username no longer logs in; new username + same password does.
    assert run(ac.authenticate_env_admin(db, "admin@acme", "InitialPw1")) is None
    renamed = run(ac.authenticate_env_admin(db, "boss@acme", "InitialPw1"))
    assert renamed["username"] == "boss@acme"
    assert renamed["role"] == "admin"


# --------------------------------------------------------------------------- #
# 2. Owner rotation supersedes a self-service override
# --------------------------------------------------------------------------- #
def test_owner_rotation_supersedes_self_service_override(monkeypatch: pytest.MonkeyPatch) -> None:
    account = env_admin("admin@acme", "InitialPw1")
    use_env_accounts(monkeypatch, [account])  # same list ref → mutating == owner rotation
    db = FakeDB()
    session = {"sub": "admin@acme", "role": "admin"}

    # Company admin sets their own password.
    run(ac.change_password(db, session, "InitialPw1", "TenantChosen2"))
    assert run(ac.authenticate_env_admin(db, "admin@acme", "TenantChosen2"))["role"] == "admin"

    # Owner rotates the account from the control plane (env credential changes).
    account["password_hash"] = hash_password("OwnerRotated3")

    # The stale self-service override is ignored; the owner-set password wins.
    assert run(ac.authenticate_env_admin(db, "admin@acme", "TenantChosen2")) is None
    assert run(ac.authenticate_env_admin(db, "admin@acme", "OwnerRotated3"))["role"] == "admin"


# --------------------------------------------------------------------------- #
# 3. An override can never escalate a role
# --------------------------------------------------------------------------- #
def test_override_cannot_escalate_role(monkeypatch: pytest.MonkeyPatch) -> None:
    account = env_admin("admin@acme", "InitialPw1", role="admin")
    use_env_accounts(monkeypatch, [account])
    db = FakeDB()
    session = {"sub": "admin@acme", "role": "admin"}
    run(ac.change_password(db, session, "InitialPw1", "NewPw2345"))

    # Tamper with the user-writable override document.
    db["admin_credentials"].docs[0]["role"] = "owner"

    result = run(ac.authenticate_env_admin(db, "admin@acme", "NewPw2345"))
    assert result["role"] == "admin"  # role comes from env, never the override


# --------------------------------------------------------------------------- #
# Database-backed users + collisions
# --------------------------------------------------------------------------- #
def test_database_user_changes_own_password(monkeypatch: pytest.MonkeyPatch) -> None:
    use_env_accounts(monkeypatch, [])
    db = FakeDB()
    db["admin_users"].docs.append(
        {
            "user_id": "u1",
            "username": "dr.smith",
            "username_normalized": "dr.smith",
            "password_hash": hash_password("DocPw1234"),
            "access_level": "clinician",
            "status": "active",
            "login_enabled": True,
        }
    )
    session = {"sub": "dr.smith", "role": "user", "user_id": "u1"}
    run(ac.change_password(db, session, "DocPw1234", "NewDocPw5"))
    doc = db["admin_users"].docs[0]
    assert verify_password_hash("NewDocPw5", doc["password_hash"])
    assert not verify_password_hash("DocPw1234", doc["password_hash"])


def test_username_change_rejects_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    account = env_admin("admin@acme", "InitialPw1")
    use_env_accounts(monkeypatch, [account])
    db = FakeDB()
    db["admin_users"].docs.append(
        {"user_id": "u1", "username": "taken", "username_normalized": "taken", "status": "active"}
    )
    session = {"sub": "admin@acme", "role": "admin"}
    with pytest.raises(ac.CredentialError):
        run(ac.change_username(db, session, "InitialPw1", "taken"))


def test_password_change_rejects_same_password(monkeypatch: pytest.MonkeyPatch) -> None:
    account = env_admin("admin@acme", "InitialPw1")
    use_env_accounts(monkeypatch, [account])
    db = FakeDB()
    session = {"sub": "admin@acme", "role": "admin"}
    with pytest.raises(ac.CredentialError):
        run(ac.change_password(db, session, "InitialPw1", "InitialPw1"))
