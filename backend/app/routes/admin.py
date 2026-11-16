from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field, ValidationError

from backend.app.core.config import settings
from backend.app.db.mongo import get_database
from backend.app.services.admin_auth import (
    ADMIN_COOKIE_NAME,
    AdminAuthError,
    create_admin_session,
    create_user_session,
    hash_password,
    normalize_username,
    public_admin,
    safe_redirect_path,
    verify_password_hash,
    verify_admin_session,
    verify_platform_session,
)
from backend.app.services.account_credentials import (
    CredentialError,
    authenticate_env_admin,
    change_password,
    change_username,
    ensure_credential_indexes,
)
from backend.app.services.admin_knowledge import (
    KnowledgeIngestionError,
    ensure_admin_indexes,
    ingest_pdf_document,
    parse_tags,
    public_document,
)
from backend.app.services.professional_credentials import (
    CredentialError as ProfileCredentialError,
    get_user_credentials,
    set_user_credentials,
)
from backend.app.services.bodymap_coordinates import (
    ADMIN_COORDINATE_SOURCE,
    CURRENT_COORDINATE_SYSTEM,
    LEGACY_COORDINATE_SOURCE,
    LEGACY_COORDINATE_SYSTEM,
    load_bodymap_asset,
    load_seed_coordinates,
    normalize_coordinate_document,
    normalize_seed_coordinate_map,
)
from backend.app.services.tree import to_public_document


router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=120)


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=120)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=5, max_length=160)
    phone: str = Field(min_length=3, max_length=40)
    professional_title: str | None = Field(default=None, max_length=120)
    specialty: str | None = Field(default=None, max_length=120)
    organization: str | None = Field(default=None, max_length=160)
    department: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    license_number: str | None = Field(default=None, max_length=100)
    board_cert_number: str | None = Field(default=None, max_length=100)
    license_region: str | None = Field(default=None, max_length=120)
    access_level: Literal["learner", "clinician", "educator", "admin"] = "learner"
    status: Literal["active", "inactive", "pending"] = "active"
    notes: str | None = Field(default=None, max_length=1200)


class AdminPasswordResetRequest(BaseModel):
    password: str = Field(min_length=8, max_length=120)


class AccountPasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=120)
    new_password: str = Field(min_length=8, max_length=120)


class AccountUsernameChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=120)
    new_username: str = Field(min_length=3, max_length=80)


class AccountCredentialsRequest(BaseModel):
    board_cert_number: str | None = Field(default=None, max_length=100)
    license_number: str | None = Field(default=None, max_length=100)
    license_region: str | None = Field(default=None, max_length=120)


class BodyMapPinUpdate(BaseModel):
    view: Literal["anterior", "posterior"]
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    side: Literal["L", "R", "C"]


class BodyMapCoordinateUpdateRequest(BaseModel):
    lat_name: str = Field(min_length=1, max_length=160)
    coordinates: list[BodyMapPinUpdate] = Field(min_length=1, max_length=8)
    curation_status: Literal["draft", "reviewed", "production_curated"] = "draft"
    notes: str | None = Field(default=None, max_length=800)


class BodyMapCoordinateResetRequest(BaseModel):
    lat_name: str = Field(min_length=1, max_length=160)


async def require_admin(request: Request) -> dict[str, Any]:
    try:
        return verify_admin_session(request.cookies.get(ADMIN_COOKIE_NAME))
    except AdminAuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error


async def require_session(request: Request) -> dict[str, Any]:
    try:
        return verify_platform_session(request.cookies.get(ADMIN_COOKIE_NAME))
    except AdminAuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error


@router.get("/login")
async def login_page_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login.html", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/login", response_model=None)
async def login_admin(
    request: Request,
    response: Response,
    next: str | None = None,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> Any:
    payload = await parse_admin_login_request(request)
    admin = await authenticate_env_admin(database, payload.username, payload.password)
    if admin:
        session = create_admin_session(admin)
    else:
        user = await authenticate_database_user(database, payload.username, payload.password)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")
        session = create_user_session(user)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials.")
    if is_browser_form_login(request):
        redirect = RedirectResponse(url=safe_redirect_path(next, default="/"), status_code=status.HTTP_303_SEE_OTHER)
        set_admin_cookie(redirect, session["token"])
        return redirect
    set_admin_cookie(response, session["token"])
    return {"admin": session["admin"], "expires_at": session["expires_at"]}


@router.post("/logout")
async def logout_admin(response: Response) -> dict[str, str]:
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/session")
async def admin_session(admin: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
    return {"authenticated": True, "admin": public_admin(admin)}


@router.post("/account/password")
async def change_account_password(
    payload: AccountPasswordChangeRequest,
    session: dict[str, Any] = Depends(require_session),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Let the *currently authenticated* account (owner, company admin or a
    database user) change its own password. Works for env-based accounts via a
    database credential override, so no env edit or service restart is needed."""
    await ensure_credential_indexes(database)
    try:
        account = await change_password(database, session, payload.current_password, payload.new_password)
    except CredentialError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"account": account, "status": "password_changed"}


@router.post("/account/username")
async def change_account_username(
    payload: AccountUsernameChangeRequest,
    response: Response,
    session: dict[str, Any] = Depends(require_session),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Let the currently authenticated account change its own login username."""
    await ensure_credential_indexes(database)
    try:
        account = await change_username(database, session, payload.current_password, payload.new_username)
    except CredentialError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    # Env/bootstrap accounts carry their login name inside the signed session
    # token (no stable user_id), so after a rename we must re-issue the cookie or
    # the now-stale `sub` would break the next self-service call. Database users
    # are keyed by their immutable user_id, so their session stays valid as-is.
    if not session.get("user_id"):
        refreshed = create_admin_session(account)
        set_admin_cookie(response, refreshed["token"])
    return {"account": account, "status": "username_changed"}


@router.get("/account/profile")
async def get_account_profile(
    session: dict[str, Any] = Depends(require_session),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Return the current account's identity plus its professional credentials.

    Database users resolve their stored credential profile; env/bootstrap admins
    carry no `admin_users` row, so their credentials come back empty (on_file is
    False), which the UI surfaces as "no credential on file".
    """
    user_id = session.get("user_id")
    credentials = await get_user_credentials(database, user_id)
    return {
        "account": {
            "user_id": user_id,
            "username": session.get("sub"),
            "display_name": session.get("display_name"),
            "role": session.get("role"),
            "access_level": session.get("access_level"),
        },
        "credentials": credentials,
        "credentials_editable": bool(user_id),
    }


@router.post("/account/credentials")
async def update_account_credentials(
    payload: AccountCredentialsRequest,
    session: dict[str, Any] = Depends(require_session),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Self-service update of the current user's professional credentials.

    These fields (board certification, licence number, licence region) are
    stamped into every Tele-IONM session the user initiates or accepts, so they
    form part of the medicolegal record (item #7)."""
    try:
        credentials = await set_user_credentials(
            database,
            user_id=session.get("user_id"),
            data=payload.model_dump(),
        )
    except ProfileCredentialError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"credentials": credentials, "status": "credentials_updated"}


@router.get("/users")
async def list_users(
    _: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    await ensure_admin_indexes(database)
    users = await database.admin_users.find({}).sort("created_at", -1).to_list(length=200)
    return {"items": [public_user_document(user) for user in users], "count": len(users)}


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminUserCreateRequest,
    admin: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    await ensure_admin_indexes(database)
    email = payload.email.strip().lower()
    username = payload.username.strip()
    username_normalized = normalize_username(username)
    phone = clean_optional(payload.phone)
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Valid email is required.")
    if not phone:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Phone is required.")
    if await database.admin_users.find_one({"email": email}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists.")
    if await database.admin_users.find_one({"username_normalized": username_normalized}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with this username already exists.")
    now = datetime.now(UTC)
    document = {
        "user_id": uuid.uuid4().hex,
        "username": username,
        "username_normalized": username_normalized,
        "password_hash": hash_password(payload.password),
        "password_set_at": now,
        "first_name": payload.first_name.strip(),
        "last_name": payload.last_name.strip(),
        "full_name": f"{payload.first_name.strip()} {payload.last_name.strip()}",
        "email": email,
        "phone": phone,
        "professional_title": clean_optional(payload.professional_title),
        "specialty": clean_optional(payload.specialty),
        "organization": clean_optional(payload.organization),
        "department": clean_optional(payload.department),
        "city": clean_optional(payload.city),
        "country": clean_optional(payload.country),
        "license_number": clean_optional(payload.license_number),
        "board_cert_number": clean_optional(payload.board_cert_number),
        "license_region": clean_optional(payload.license_region),
        "access_level": payload.access_level,
        "status": payload.status,
        "notes": clean_optional(payload.notes),
        "login_enabled": True,
        "password_reset_required": False,
        "created_by": admin.get("sub"),
        "created_at": now,
        "updated_at": now,
    }
    await database.admin_users.insert_one(document)
    return {"user": public_user_document(document)}


@router.post("/users/{user_id}/password")
async def reset_user_password(
    user_id: str,
    payload: AdminPasswordResetRequest,
    admin: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    await ensure_admin_indexes(database)
    now = datetime.now(UTC)
    result = await database.admin_users.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "password_hash": hash_password(payload.password),
                "password_set_at": now,
                "password_reset_at": now,
                "password_reset_by": admin.get("sub"),
                "password_reset_required": False,
                "login_enabled": True,
                "updated_at": now,
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    user = await database.admin_users.find_one({"user_id": user_id})
    return {"user": public_user_document(user)}


@router.get("/knowledge/documents")
async def list_knowledge_documents(
    _: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    await ensure_admin_indexes(database)
    documents = await database.admin_knowledge_documents.find({}).sort("created_at", -1).to_list(length=100)
    return {"items": [public_document(document) for document in documents], "count": len(documents)}


@router.post("/knowledge/documents", status_code=status.HTTP_201_CREATED)
async def upload_knowledge_document(
    title: str = Form(min_length=2, max_length=180),
    domain: str = Form(default="general", max_length=80),
    tags: str = Form(default="", max_length=300),
    file: UploadFile = File(...),
    admin: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    if file.content_type not in {"application/pdf", "application/x-pdf", "application/octet-stream"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Only PDF uploads are accepted.")
    file_bytes = await file.read()
    try:
        return await ingest_pdf_document(
            database,
            file_bytes=file_bytes,
            original_filename=file.filename or "knowledge.pdf",
            title=title,
            domain=domain,
            tags=parse_tags(tags),
            uploaded_by=admin.get("sub", "admin"),
        )
    except KnowledgeIngestionError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/bodymap/asset")
async def bodymap_asset(_: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"asset": load_bodymap_asset(), "body_image_url": "/assets/body.jpg?v=20260531-highres-openstax"}


@router.get("/bodymap/coordinates")
async def list_bodymap_coordinates(
    _: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    coordinates = await database.electrode_coordinates.find({}).sort("lat_name", 1).to_list(length=None)
    seed_coordinate_lookup = normalize_seed_coordinate_map()
    muscle_names = [item.get("lat_name") for item in coordinates if item.get("lat_name")]
    muscles = await database.muscles.find({"lat_name": {"$in": muscle_names}}).to_list(length=None)
    muscle_lookup = {muscle.get("lat_name"): muscle for muscle in muscles}
    items = []
    for item in coordinates:
        lat_name = item.get("lat_name")
        muscle = muscle_lookup.get(lat_name, {})
        public_item = to_public_document(normalize_coordinate_document(item))
        public_item["name_el"] = muscle.get("name_el")
        public_item["placement"] = muscle.get("placement")
        public_item["action"] = muscle.get("action")
        public_item["roots"] = muscle.get("roots", [])
        public_item["cranial_nerves"] = muscle.get("cranial_nerves", [])
        public_item["coordinate_count"] = len(public_item.get("coordinates") or [])
        public_item["curation_status"] = public_item.get("curation_status") or "legacy_needs_recuration"
        public_item["seed_coordinates"] = seed_coordinate_lookup.get(lat_name, [])
        public_item["seed_coordinate_source"] = LEGACY_COORDINATE_SOURCE
        items.append(public_item)
    return {"items": items, "count": len(items), "asset": load_bodymap_asset()}


@router.patch("/bodymap/coordinates")
async def update_bodymap_coordinates(
    payload: BodyMapCoordinateUpdateRequest,
    admin: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    existing = await database.electrode_coordinates.find_one({"lat_name": payload.lat_name})
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coordinate muscle not found")
    now = datetime.now(UTC)
    coordinates = [pin.model_dump() for pin in payload.coordinates]
    await database.electrode_coordinates.update_one(
        {"lat_name": payload.lat_name},
        {
            "$set": {
                "coordinates": coordinates,
                "coordinate_system": CURRENT_COORDINATE_SYSTEM,
                "source": ADMIN_COORDINATE_SOURCE,
                "curation_status": payload.curation_status,
                "curation_notes": clean_optional(payload.notes),
                "curated_by": admin.get("sub"),
                "curated_at": now,
                "coordinate_source": ADMIN_COORDINATE_SOURCE,
            }
        },
    )
    updated = await database.electrode_coordinates.find_one({"lat_name": payload.lat_name})
    return {"coordinate": to_public_document(updated)}


@router.post("/bodymap/coordinates/reset")
async def reset_bodymap_coordinate(
    payload: BodyMapCoordinateResetRequest,
    admin: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    seed_coordinates = load_seed_coordinates()
    coordinates = seed_coordinates.get(payload.lat_name)
    if not coordinates:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seed coordinate target not found")
    existing = await database.electrode_coordinates.find_one({"lat_name": payload.lat_name})
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coordinate muscle not found")
    now = datetime.now(UTC)
    await database.electrode_coordinates.update_one(
        {"lat_name": payload.lat_name},
        {
            "$set": {
                "coordinates": coordinates,
                "coordinate_system": LEGACY_COORDINATE_SYSTEM,
                "source": LEGACY_COORDINATE_SOURCE,
                "restored_from_seed_by": admin.get("sub"),
                "restored_from_seed_at": now,
            },
            "$unset": {
                "curation_status": "",
                "curation_notes": "",
                "curated_by": "",
                "curated_at": "",
                "coordinate_source": "",
            },
        },
    )
    updated = await database.electrode_coordinates.find_one({"lat_name": payload.lat_name})
    public_item = to_public_document(normalize_coordinate_document(updated))
    public_item["curation_status"] = "legacy_needs_recuration"
    return {"coordinate": public_item}


@router.get("/bodymap/export")
async def export_bodymap_coordinates(
    _: dict[str, Any] = Depends(require_admin),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    coordinates = await database.electrode_coordinates.find({}).sort("lat_name", 1).to_list(length=None)
    payload = {item["lat_name"]: item.get("coordinates", []) for item in coordinates if item.get("lat_name")}
    return {"coordinates": payload, "count": len(payload), "asset": load_bodymap_asset()}


def clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def authenticate_database_user(database: AsyncIOMotorDatabase, username: str, password: str) -> dict[str, Any] | None:
    await ensure_admin_indexes(database)
    username_normalized = normalize_username(username)
    query = {
        "$or": [
            {"username_normalized": username_normalized},
            {"username": {"$regex": f"^{re.escape(username.strip())}$", "$options": "i"}},
            {"email": username.strip().lower()},
        ]
    }
    user = await database.admin_users.find_one(query)
    if not user or user.get("status") != "active" or not user.get("login_enabled") or not user.get("password_hash"):
        return None
    if not verify_password_hash(password, str(user.get("password_hash"))):
        return None
    return user


def public_user_document(document: dict[str, Any] | None) -> dict[str, Any]:
    clean = to_public_document(document or {})
    clean.pop("password_hash", None)
    clean["has_password"] = bool(document and document.get("password_hash"))
    return clean


async def parse_admin_login_request(request: Request) -> AdminLoginRequest:
    content_type = request.headers.get("content-type", "").lower()
    try:
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            raw_payload = {"username": form.get("username", ""), "password": form.get("password", "")}
        else:
            raw_payload = await request.json()
        return AdminLoginRequest.model_validate(raw_payload)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Username and password are required.") from error


def is_browser_form_login(request: Request) -> bool:
    content_type = request.headers.get("content-type", "").lower()
    accept = request.headers.get("accept", "").lower()
    return "application/x-www-form-urlencoded" in content_type and "text/html" in accept and "application/json" not in accept


def set_admin_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.environment.lower() in {"production", "prod"},
        samesite="lax",
        max_age=60 * 60 * max(1, settings.admin_session_hours),
        path="/",
    )
