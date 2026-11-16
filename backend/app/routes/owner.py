"""OWNER (control-plane) API.

These endpoints exist ONLY on the main instance (``IONM_OWNER_ENABLED=true``).
On tenant instances ``settings.owner_enabled`` is False and every route here
returns 404, so a tenant admin can never reach the control plane even if they
guessed the URL.

The owner submits provisioning intents; the root daemon executes them. The web
app itself performs no privileged action.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from backend.app.core.config import settings
from backend.app.db.mongo import get_database
from backend.app.services.admin_auth import (
    ADMIN_COOKIE_NAME,
    AdminAuthError,
    public_admin,
    verify_owner_session,
)
from backend.app.services.tenant_control import (
    TenantControlError,
    create_deprovision_request,
    create_provision_request,
    create_rotate_admin_request,
    ensure_tenant_control_indexes,
    get_request,
    list_requests,
    list_tenants,
)


router = APIRouter(prefix="/api/owner", tags=["owner"])


class TenantProvisionRequest(BaseModel):
    slug: str = Field(min_length=3, max_length=32)
    company: str = Field(min_length=2, max_length=160)
    lang: str = Field(default="el", min_length=2, max_length=2)
    admin_user: str | None = Field(default=None, max_length=120)
    admin_password: str = Field(min_length=8, max_length=120)
    domain: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    tls: bool = True


class TenantDeprovisionRequest(BaseModel):
    confirm_slug: str = Field(min_length=3, max_length=32)
    drop_db: bool = False


class TenantRotateAdminRequest(BaseModel):
    admin_password: str = Field(min_length=8, max_length=120)
    admin_user: str | None = Field(default=None, max_length=120)


async def require_owner(request: Request) -> dict[str, Any]:
    if not settings.owner_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    try:
        return verify_owner_session(request.cookies.get(ADMIN_COOKIE_NAME))
    except AdminAuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error


@router.get("/session")
async def owner_session(owner: dict[str, Any] = Depends(require_owner)) -> dict[str, Any]:
    return {"authenticated": True, "owner": public_admin(owner)}


@router.get("/tenants")
async def get_tenants(
    _: dict[str, Any] = Depends(require_owner),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    await ensure_tenant_control_indexes(database)
    items = await list_tenants(database)
    return {"items": items, "count": len(items)}


@router.post("/tenants", status_code=status.HTTP_202_ACCEPTED)
async def provision_tenant(
    payload: TenantProvisionRequest,
    owner: dict[str, Any] = Depends(require_owner),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    await ensure_tenant_control_indexes(database)
    try:
        request_doc = await create_provision_request(
            database,
            slug=payload.slug,
            company=payload.company,
            lang=payload.lang,
            admin_user=payload.admin_user,
            admin_password=payload.admin_password,
            domain=payload.domain,
            port=payload.port,
            tls=payload.tls,
            requested_by=str(owner.get("sub") or owner.get("display_name") or "owner"),
        )
    except TenantControlError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"request": request_doc}


@router.post("/tenants/{slug}/rotate-admin", status_code=status.HTTP_202_ACCEPTED)
async def rotate_tenant_admin(
    slug: str,
    payload: TenantRotateAdminRequest,
    owner: dict[str, Any] = Depends(require_owner),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    await ensure_tenant_control_indexes(database)
    try:
        request_doc = await create_rotate_admin_request(
            database,
            slug=slug,
            admin_password=payload.admin_password,
            admin_user=payload.admin_user,
            requested_by=str(owner.get("sub") or owner.get("display_name") or "owner"),
        )
    except TenantControlError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"request": request_doc}


@router.post("/tenants/{slug}/deprovision", status_code=status.HTTP_202_ACCEPTED)
async def deprovision_tenant(
    slug: str,
    payload: TenantDeprovisionRequest,
    owner: dict[str, Any] = Depends(require_owner),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    await ensure_tenant_control_indexes(database)
    if payload.confirm_slug.strip().lower() != slug.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Η επιβεβαίωση slug δεν ταιριάζει.",
        )
    try:
        request_doc = await create_deprovision_request(
            database,
            slug=slug,
            drop_db=payload.drop_db,
            requested_by=str(owner.get("sub") or owner.get("display_name") or "owner"),
        )
    except TenantControlError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"request": request_doc}


@router.get("/requests")
async def get_requests(
    _: dict[str, Any] = Depends(require_owner),
    database: AsyncIOMotorDatabase = Depends(get_database),
    limit: int = 50,
) -> dict[str, Any]:
    await ensure_tenant_control_indexes(database)
    items = await list_requests(database, limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/requests/{request_id}")
async def get_single_request(
    request_id: str,
    _: dict[str, Any] = Depends(require_owner),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    document = await get_request(database, request_id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return {"request": document}
