"""Control-plane data layer for tenant provisioning.

This module is the bridge between the OWNER panel (which runs as the unprivileged
web app) and the privileged provisioner daemon (which runs as root). The panel
NEVER touches systemd/nginx/certbot directly. Instead it writes an intent record
into the ``tenant_requests`` queue; the root daemon claims pending requests,
executes ``provision_tenant.sh``, and writes progress/results back.

Two collections back this design:

``tenant_requests``
    The work queue + immutable audit trail. Each document captures who requested
    what, the live status, a streamed log, and the final result. The admin
    password lives here only transiently: the daemon deletes it the moment the
    provisioning finishes.

``tenants``
    The registry of provisioned companies, written by the daemon after a
    successful run. This is the source of truth the panel lists.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

# Mirrors the slug contract enforced by provision_tenant.sh exactly.
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,30}[a-z0-9])$")
DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{1,251}[a-z0-9])$")

PORT_MIN = 8210
PORT_MAX = 8259

ACTION_PROVISION = "provision"
ACTION_DEPROVISION = "deprovision"
VALID_ACTIONS = {ACTION_PROVISION, ACTION_DEPROVISION}

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
ACTIVE_STATUSES = {STATUS_PENDING, STATUS_RUNNING}

TENANT_ACTIVE = "active"
TENANT_REMOVED = "removed"


class TenantControlError(ValueError):
    """Raised for invalid control-plane input (mapped to HTTP 4xx by the route)."""


async def ensure_tenant_control_indexes(database: AsyncIOMotorDatabase) -> None:
    await database.tenant_requests.create_index("request_id", unique=True)
    await database.tenant_requests.create_index([("status", 1), ("created_at", 1)])
    await database.tenant_requests.create_index("slug")
    await database.tenants.create_index("slug", unique=True)


def normalize_slug(value: str) -> str:
    return value.strip().lower()


def validate_slug(value: str) -> str:
    slug = normalize_slug(value)
    if not SLUG_RE.match(slug):
        raise TenantControlError(
            "Το slug πρέπει να είναι 3-32 χαρακτήρες, πεζά [a-z0-9-], χωρίς '-' στην αρχή/τέλος."
        )
    return slug


def validate_lang(value: str) -> str:
    lang = value.strip().lower()
    if lang not in {"el", "en"}:
        raise TenantControlError("Η γλώσσα πρέπει να είναι 'el' ή 'en'.")
    return lang


def validate_domain(value: str, slug: str) -> str:
    domain = value.strip().lower() if value else f"{slug}.neuroionm.eu"
    if not DOMAIN_RE.match(domain):
        raise TenantControlError("Μη έγκυρο domain.")
    return domain


def validate_port(value: int | None) -> int | None:
    if value is None:
        return None
    if not (PORT_MIN <= value <= PORT_MAX):
        raise TenantControlError(f"Το port πρέπει να είναι μεταξύ {PORT_MIN} και {PORT_MAX}.")
    return value


def public_request(document: dict[str, Any] | None) -> dict[str, Any]:
    """Serialize a request WITHOUT ever leaking the transient admin password."""
    if not document:
        return {}
    return {
        "request_id": document.get("request_id"),
        "action": document.get("action"),
        "rotate_admin": bool(document.get("rotate_admin", False)),
        "slug": document.get("slug"),
        "company": document.get("company"),
        "domain": document.get("domain"),
        "port": document.get("port"),
        "lang": document.get("lang"),
        "admin_user": document.get("admin_user"),
        "tls": document.get("tls", True),
        "status": document.get("status"),
        "log": document.get("log", []),
        "error": document.get("error"),
        "result": document.get("result"),
        "requested_by": document.get("requested_by"),
        "created_at": _iso(document.get("created_at")),
        "started_at": _iso(document.get("started_at")),
        "finished_at": _iso(document.get("finished_at")),
        "password_pending": bool(document.get("admin_password")),
    }


def public_tenant(document: dict[str, Any] | None) -> dict[str, Any]:
    if not document:
        return {}
    return {
        "slug": document.get("slug"),
        "company": document.get("company"),
        "domain": document.get("domain"),
        "port": document.get("port"),
        "lang": document.get("lang"),
        "db": document.get("db"),
        "admin_user": document.get("admin_user"),
        "status": document.get("status", TENANT_ACTIVE),
        "login_url": document.get("login_url"),
        "created_at": _iso(document.get("created_at")),
        "updated_at": _iso(document.get("updated_at")),
    }


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value if isinstance(value, str) else None


async def create_provision_request(
    database: AsyncIOMotorDatabase,
    *,
    slug: str,
    company: str,
    lang: str,
    admin_user: str | None,
    admin_password: str,
    domain: str | None,
    port: int | None,
    tls: bool,
    requested_by: str,
) -> dict[str, Any]:
    slug = validate_slug(slug)
    company = company.strip()
    if not company:
        raise TenantControlError("Το όνομα εταιρείας είναι υποχρεωτικό.")
    lang = validate_lang(lang)
    domain = validate_domain(domain or "", slug)
    port = validate_port(port)
    admin_user = (admin_user or f"admin@{slug}").strip()
    if len(admin_password) < 8:
        raise TenantControlError("Ο κωδικός admin πρέπει να έχει τουλάχιστον 8 χαρακτήρες.")

    existing_active = await database.tenant_requests.find_one(
        {"slug": slug, "status": {"$in": list(ACTIVE_STATUSES)}}
    )
    if existing_active:
        raise TenantControlError(
            f"Υπάρχει ήδη ενεργό αίτημα για το '{slug}' (status: {existing_active.get('status')})."
        )

    now = datetime.now(UTC)
    document = {
        "request_id": uuid.uuid4().hex,
        "action": ACTION_PROVISION,
        "slug": slug,
        "company": company,
        "domain": domain,
        "port": port,
        "lang": lang,
        "admin_user": admin_user,
        "admin_password": admin_password,  # transient — daemon deletes after run
        "tls": bool(tls),
        "status": STATUS_PENDING,
        "log": [],
        "error": None,
        "result": None,
        "requested_by": requested_by,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    await database.tenant_requests.insert_one(document)
    return public_request(document)


async def create_rotate_admin_request(
    database: AsyncIOMotorDatabase,
    *,
    slug: str,
    admin_password: str,
    admin_user: str | None,
    requested_by: str,
) -> dict[str, Any]:
    """Queue an admin-credential rotation for an existing tenant.

    Implemented as an idempotent re-provision: ``provision_tenant.sh`` rewrites
    the tenant env file with the new PBKDF2 hash (keeping the same port, domain
    and database) and restarts only that tenant's service. Because the env
    credential fingerprint changes, any self-service override the company admin
    previously set is transparently superseded, so the owner-set password wins.
    """
    slug = validate_slug(slug)
    tenant = await database.tenants.find_one({"slug": slug})
    if not tenant or tenant.get("status") == TENANT_REMOVED:
        raise TenantControlError(f"Δεν υπάρχει ενεργός tenant '{slug}'.")
    if len(admin_password) < 8:
        raise TenantControlError("Ο κωδικός admin πρέπει να έχει τουλάχιστον 8 χαρακτήρες.")
    existing_active = await database.tenant_requests.find_one(
        {"slug": slug, "status": {"$in": list(ACTIVE_STATUSES)}}
    )
    if existing_active:
        raise TenantControlError(
            f"Υπάρχει ήδη ενεργό αίτημα για το '{slug}' (status: {existing_active.get('status')})."
        )
    admin_user = (admin_user or tenant.get("admin_user") or f"admin@{slug}").strip()
    now = datetime.now(UTC)
    document = {
        "request_id": uuid.uuid4().hex,
        "action": ACTION_PROVISION,  # idempotent update == credential rotation
        "rotate_admin": True,
        "slug": slug,
        "company": tenant.get("company"),
        "domain": tenant.get("domain"),
        "port": tenant.get("port"),
        "lang": tenant.get("lang", "el"),
        "admin_user": admin_user,
        "admin_password": admin_password,  # transient — daemon scrubs after run
        "tls": True,
        "status": STATUS_PENDING,
        "log": [],
        "error": None,
        "result": None,
        "requested_by": requested_by,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    await database.tenant_requests.insert_one(document)
    return public_request(document)


async def create_deprovision_request(
    database: AsyncIOMotorDatabase,
    *,
    slug: str,
    drop_db: bool,
    requested_by: str,
) -> dict[str, Any]:
    slug = validate_slug(slug)
    tenant = await database.tenants.find_one({"slug": slug})
    if not tenant or tenant.get("status") == TENANT_REMOVED:
        raise TenantControlError(f"Δεν υπάρχει ενεργός tenant '{slug}'.")
    existing_active = await database.tenant_requests.find_one(
        {"slug": slug, "status": {"$in": list(ACTIVE_STATUSES)}}
    )
    if existing_active:
        raise TenantControlError(
            f"Υπάρχει ήδη ενεργό αίτημα για το '{slug}' (status: {existing_active.get('status')})."
        )
    now = datetime.now(UTC)
    document = {
        "request_id": uuid.uuid4().hex,
        "action": ACTION_DEPROVISION,
        "slug": slug,
        "company": tenant.get("company"),
        "domain": tenant.get("domain"),
        "port": tenant.get("port"),
        "lang": tenant.get("lang"),
        "admin_user": tenant.get("admin_user"),
        "admin_password": None,
        "tls": False,
        "drop_db": bool(drop_db),
        "status": STATUS_PENDING,
        "log": [],
        "error": None,
        "result": None,
        "requested_by": requested_by,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    await database.tenant_requests.insert_one(document)
    return public_request(document)


async def list_requests(database: AsyncIOMotorDatabase, *, limit: int = 50) -> list[dict[str, Any]]:
    cursor = database.tenant_requests.find({}).sort("created_at", -1).limit(max(1, min(limit, 200)))
    return [public_request(doc) async for doc in cursor]


async def get_request(database: AsyncIOMotorDatabase, request_id: str) -> dict[str, Any] | None:
    document = await database.tenant_requests.find_one({"request_id": request_id})
    return public_request(document) if document else None


async def list_tenants(database: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = database.tenants.find({}).sort("created_at", -1)
    return [public_tenant(doc) async for doc in cursor]
