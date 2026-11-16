"""Self-service credential management for admin/owner accounts.

The platform has two kinds of privileged accounts:

* **Environment accounts** — the control-plane *owner* on the main instance and
  the *company admin* on every tenant instance. Their identity, role and initial
  password come from the environment (``IONM_ADMIN_BOOTSTRAP_*`` /
  ``IONM_ADMIN_ACCOUNTS_JSON``). The env file is owned by root and is the
  break-glass source of truth, so the web app must never rewrite it.

* **Database users** — accounts created from the admin panel and stored in the
  ``admin_users`` collection. They already own their password hash in the DB.

To let a handed-over company admin change *their own* username and password
(without ever touching the env file or restarting the service) this module keeps
a small ``admin_credentials`` collection of **credential overrides**. An override
is bound to the env account's credential fingerprint:

* While the fingerprint matches, the override's username/password *replace* the
  env credential — so the account holder fully controls their own login.
* When the owner rotates the account from the control plane the env credential
  (and thus the fingerprint) changes, the override is transparently treated as
  stale, and the owner-set credential wins again. No cross-process cleanup is
  required, which keeps the privileged provisioner and the unprivileged web app
  fully decoupled.

The account *role* is always re-derived from the env account on every login and
is never read back from this user-writable collection, so an override can never
escalate privileges (e.g. a tenant admin can never turn itself into an owner).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.services.admin_auth import (
    admin_accounts,
    env_account_fingerprint,
    find_admin_account,
    hash_password,
    normalize_username,
    public_admin,
    public_admin_identity,
    verify_account_password,
    verify_password_hash,
)

CREDENTIAL_COLLECTION = "admin_credentials"

MIN_PASSWORD_LENGTH = 8
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 80


class CredentialError(ValueError):
    """Raised for invalid self-service credential input (mapped to HTTP 4xx)."""


async def ensure_credential_indexes(database: AsyncIOMotorDatabase) -> None:
    await database[CREDENTIAL_COLLECTION].create_index("identity_username", unique=True)
    await database[CREDENTIAL_COLLECTION].create_index("login_username")


# --------------------------------------------------------------------------- #
# Override storage helpers
# --------------------------------------------------------------------------- #
async def _override_by_identity(database: AsyncIOMotorDatabase, identity: str) -> dict[str, Any] | None:
    return await database[CREDENTIAL_COLLECTION].find_one({"identity_username": identity})


async def _override_by_login(database: AsyncIOMotorDatabase, login_username: str) -> dict[str, Any] | None:
    return await database[CREDENTIAL_COLLECTION].find_one({"login_username": login_username})


def _is_fresh(override: dict[str, Any] | None, account: dict[str, Any]) -> bool:
    """An override is *fresh* (authoritative) while it is still bound to the
    env account's current credential fingerprint."""
    if not override:
        return False
    return str(override.get("base_env_fingerprint") or "") == env_account_fingerprint(account)


async def _upsert_override(
    database: AsyncIOMotorDatabase,
    *,
    identity: str,
    login_username: str,
    login_username_display: str,
    display_name: str,
    password_hash: str,
    fingerprint: str,
    actor: str | None,
) -> None:
    now = datetime.now(UTC)
    await database[CREDENTIAL_COLLECTION].update_one(
        {"identity_username": identity},
        {
            "$set": {
                "login_username": login_username,
                "login_username_display": login_username_display,
                "display_name": display_name,
                "password_hash": password_hash,
                "base_env_fingerprint": fingerprint,
                "updated_at": now,
                "updated_by": actor,
            },
            "$setOnInsert": {"identity_username": identity, "created_at": now},
        },
        upsert=True,
    )


# --------------------------------------------------------------------------- #
# Login resolution (override-aware env authentication)
# --------------------------------------------------------------------------- #
async def authenticate_env_admin(
    database: AsyncIOMotorDatabase, username: str, password: str
) -> dict[str, str] | None:
    """Authenticate an environment/bootstrap admin, honouring a fresh override.

    Returns a ``public_admin`` dict (role anchored to the env account, but
    username/display taken from a fresh override when present) or ``None``.
    """
    requested = normalize_username(username)
    for account in admin_accounts():
        identity = normalize_username(str(account["username"]))
        override = await _override_by_identity(database, identity)
        if _is_fresh(override, account):
            assert override is not None
            login_name = normalize_username(str(override.get("login_username") or identity))
            if login_name != requested:
                continue
            stored_hash = str(override.get("password_hash") or "")
            if stored_hash and verify_password_hash(password, stored_hash):
                return public_admin_identity(
                    account,
                    login_username=str(override.get("login_username") or login_name),
                    display_name=str(override.get("display_name") or ""),
                )
            return None  # override owns this login → never fall back to the env password
        if identity != requested:
            continue
        if verify_account_password(account, password):
            return public_admin(account)
        return None
    return None


async def _resolve_env_account(
    database: AsyncIOMotorDatabase, login_username: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve the env account + active override for a current login username."""
    requested = normalize_username(login_username)

    direct = find_admin_account(requested)
    if direct:
        override = await _override_by_identity(database, normalize_username(str(direct["username"])))
        if _is_fresh(override, direct):
            assert override is not None
            # A fresh override may have renamed this account away from its env identity.
            if normalize_username(str(override.get("login_username") or "")) != requested:
                return None, None
        return direct, override

    override = await _override_by_login(database, requested)
    if override:
        account = find_admin_account(str(override.get("identity_username") or ""))
        if account and _is_fresh(override, account):
            return account, override
    return None, None


# --------------------------------------------------------------------------- #
# Username availability
# --------------------------------------------------------------------------- #
async def _ensure_username_available(
    database: AsyncIOMotorDatabase, new_username: str, *, exclude_identity: str | None = None, exclude_user_id: str | None = None
) -> None:
    candidate = normalize_username(new_username)
    for account in admin_accounts():
        identity = normalize_username(str(account["username"]))
        if identity == candidate and identity != exclude_identity:
            raise CredentialError("Το username χρησιμοποιείται ήδη.")
    override = await _override_by_login(database, candidate)
    if override and str(override.get("identity_username") or "") != exclude_identity:
        raise CredentialError("Το username χρησιμοποιείται ήδη.")
    query: dict[str, Any] = {"username_normalized": candidate}
    if exclude_user_id:
        query["user_id"] = {"$ne": exclude_user_id}
    if await database.admin_users.find_one(query):
        raise CredentialError("Το username χρησιμοποιείται ήδη.")


def _validate_password(password: str) -> None:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise CredentialError(f"Ο νέος κωδικός πρέπει να έχει τουλάχιστον {MIN_PASSWORD_LENGTH} χαρακτήρες.")


def _validate_username(username: str) -> str:
    cleaned = " ".join(str(username or "").strip().split())
    if len(cleaned) < MIN_USERNAME_LENGTH or len(cleaned) > MAX_USERNAME_LENGTH:
        raise CredentialError(f"Το username πρέπει να έχει {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} χαρακτήρες.")
    return cleaned


# --------------------------------------------------------------------------- #
# Public API — password
# --------------------------------------------------------------------------- #
async def change_password(
    database: AsyncIOMotorDatabase, session: dict[str, Any], current_password: str, new_password: str
) -> dict[str, str]:
    _validate_password(new_password)
    if current_password == new_password:
        raise CredentialError("Ο νέος κωδικός πρέπει να διαφέρει από τον τρέχοντα.")
    user_id = str(session.get("user_id") or "")
    if user_id:
        return await _change_db_user_password(database, user_id, current_password, new_password)
    return await _change_env_admin_password(database, session, current_password, new_password)


async def _change_db_user_password(
    database: AsyncIOMotorDatabase, user_id: str, current_password: str, new_password: str
) -> dict[str, str]:
    doc = await database.admin_users.find_one({"user_id": user_id})
    if not doc or not doc.get("password_hash"):
        raise CredentialError("Ο λογαριασμός δεν βρέθηκε.")
    if not verify_password_hash(current_password, str(doc["password_hash"])):
        raise CredentialError("Ο τρέχων κωδικός είναι λάθος.")
    now = datetime.now(UTC)
    await database.admin_users.update_one(
        {"user_id": user_id},
        {"$set": {"password_hash": hash_password(new_password), "password_set_at": now, "password_reset_required": False, "updated_at": now}},
    )
    return {"username": str(doc.get("username") or ""), "display_name": str(doc.get("full_name") or doc.get("username") or ""), "role": "admin" if doc.get("access_level") == "admin" else "user"}


async def _change_env_admin_password(
    database: AsyncIOMotorDatabase, session: dict[str, Any], current_password: str, new_password: str
) -> dict[str, str]:
    account, override = await _resolve_env_account(database, str(session.get("sub") or ""))
    if not account:
        raise CredentialError("Ο λογαριασμός δεν βρέθηκε.")
    fresh = _is_fresh(override, account)
    if fresh and override and override.get("password_hash"):
        ok = verify_password_hash(current_password, str(override["password_hash"]))
    else:
        ok = verify_account_password(account, current_password)
    if not ok:
        raise CredentialError("Ο τρέχων κωδικός είναι λάθος.")
    identity = normalize_username(str(account["username"]))
    login_name = normalize_username(str((fresh and override and override.get("login_username")) or identity))
    login_display = str((fresh and override and override.get("login_username_display")) or session.get("sub") or login_name)
    display_name = str((fresh and override and override.get("display_name")) or account.get("display_name") or login_display)
    await _upsert_override(
        database,
        identity=identity,
        login_username=login_name,
        login_username_display=login_display,
        display_name=display_name,
        password_hash=hash_password(new_password),
        fingerprint=env_account_fingerprint(account),
        actor=str(session.get("sub") or ""),
    )
    return public_admin_identity(account, login_username=login_name, display_name=display_name)


# --------------------------------------------------------------------------- #
# Public API — username
# --------------------------------------------------------------------------- #
async def change_username(
    database: AsyncIOMotorDatabase, session: dict[str, Any], current_password: str, new_username: str
) -> dict[str, str]:
    cleaned = _validate_username(new_username)
    user_id = str(session.get("user_id") or "")
    if user_id:
        return await _change_db_user_username(database, user_id, current_password, cleaned)
    return await _change_env_admin_username(database, session, current_password, cleaned)


async def _change_db_user_username(
    database: AsyncIOMotorDatabase, user_id: str, current_password: str, new_username: str
) -> dict[str, str]:
    doc = await database.admin_users.find_one({"user_id": user_id})
    if not doc or not doc.get("password_hash"):
        raise CredentialError("Ο λογαριασμός δεν βρέθηκε.")
    if not verify_password_hash(current_password, str(doc["password_hash"])):
        raise CredentialError("Ο τρέχων κωδικός είναι λάθος.")
    new_norm = normalize_username(new_username)
    if new_norm != str(doc.get("username_normalized") or ""):
        await _ensure_username_available(database, new_username, exclude_user_id=user_id)
    now = datetime.now(UTC)
    await database.admin_users.update_one(
        {"user_id": user_id},
        {"$set": {"username": new_username, "username_normalized": new_norm, "updated_at": now}},
    )
    return {"username": new_username, "display_name": str(doc.get("full_name") or new_username), "role": "admin" if doc.get("access_level") == "admin" else "user"}


async def _change_env_admin_username(
    database: AsyncIOMotorDatabase, session: dict[str, Any], current_password: str, new_username: str
) -> dict[str, str]:
    account, override = await _resolve_env_account(database, str(session.get("sub") or ""))
    if not account:
        raise CredentialError("Ο λογαριασμός δεν βρέθηκε.")
    fresh = _is_fresh(override, account)
    if fresh and override and override.get("password_hash"):
        ok = verify_password_hash(current_password, str(override["password_hash"]))
    else:
        ok = verify_account_password(account, current_password)
    if not ok:
        raise CredentialError("Ο τρέχων κωδικός είναι λάθος.")
    identity = normalize_username(str(account["username"]))
    new_norm = normalize_username(new_username)
    if new_norm != normalize_username(str(session.get("sub") or "")):
        await _ensure_username_available(database, new_username, exclude_identity=identity)
    # The override must always carry a credential; reuse the fresh override hash
    # or re-hash the just-verified current password.
    stored_hash = str((fresh and override and override.get("password_hash")) or "") or hash_password(current_password)
    display_name = str((fresh and override and override.get("display_name")) or account.get("display_name") or new_username)
    await _upsert_override(
        database,
        identity=identity,
        login_username=new_norm,
        login_username_display=new_username,
        display_name=display_name,
        password_hash=stored_hash,
        fingerprint=env_account_fingerprint(account),
        actor=str(session.get("sub") or ""),
    )
    return public_admin_identity(account, login_username=new_norm, display_name=display_name)
