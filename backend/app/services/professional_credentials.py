"""Professional credential profile + medicolegal stamping (item #7).

For a tele-IONM consult to stand up medicolegally it must record not only *who*
interpreted the signals but *under what authority and in which jurisdiction*.
This module owns the three credential fields a clinician carries — board
certification, professional licence number, and licence region/jurisdiction —
persisted on their user profile, and resolves them at the moment a tele session
is created or accepted so each session record carries an immutable credential
stamp of both the requester and the consultant.

The credentials live on the `admin_users` profile document (the licence_number
field already existed there). They are read at session time and copied onto the
session, so the stamp reflects the credential of record at the instant of the
consult — not a value that could later drift.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


USER_COLLECTION = "admin_users"
CREDENTIAL_FIELDS = ("board_cert_number", "license_number", "license_region")


class CredentialError(ValueError):
    """Raised for invalid credential profile operations."""


def normalize_credentials(data: dict[str, Any] | None) -> dict[str, str | None]:
    """Project an arbitrary dict onto the three credential fields (trimmed)."""
    source = data or {}
    result: dict[str, str | None] = {}
    for field in CREDENTIAL_FIELDS:
        value = source.get(field)
        text = str(value).strip() if value is not None else ""
        result[field] = text or None
    return result


def credentials_present(credentials: dict[str, Any] | None) -> bool:
    """True when at least one credential field is populated."""
    normalized = normalize_credentials(credentials)
    return any(normalized[field] for field in CREDENTIAL_FIELDS)


def credential_stamp(credentials: dict[str, Any] | None) -> dict[str, Any]:
    """A self-describing credential snapshot for embedding in a session record."""
    normalized = normalize_credentials(credentials)
    return {
        **normalized,
        "on_file": credentials_present(normalized),
        "snapshot_at": datetime.now(UTC),
    }


async def get_user_credentials(database: AsyncIOMotorDatabase, user_id: str | None) -> dict[str, str | None]:
    """The persisted credentials for a database user (all None for env admins)."""
    if not user_id:
        return normalize_credentials(None)
    document = await database[USER_COLLECTION].find_one({"user_id": user_id})
    return normalize_credentials(document)


async def set_user_credentials(
    database: AsyncIOMotorDatabase,
    *,
    user_id: str | None,
    data: dict[str, Any],
) -> dict[str, str | None]:
    """Persist a user's credential profile. Raises if there is no profile row.

    Env/bootstrap admins have no `admin_users` document, so they cannot store
    credentials here; that is surfaced as a clear error rather than silently
    succeeding.
    """
    if not user_id:
        raise CredentialError("Only database user profiles can store professional credentials.")
    normalized = normalize_credentials(data)
    result = await database[USER_COLLECTION].update_one(
        {"user_id": user_id},
        {"$set": {**normalized, "credentials_updated_at": datetime.now(UTC), "updated_at": datetime.now(UTC)}},
    )
    if result.matched_count == 0:
        raise CredentialError("No user profile found for the current account.")
    return normalized


async def enrich_identity(database: AsyncIOMotorDatabase, identity: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `identity` with its current credentials attached."""
    credentials = await get_user_credentials(database, identity.get("user_id"))
    return {**identity, "credentials": credentials}
