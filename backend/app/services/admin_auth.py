from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from backend.app.core.config import settings


# Local/development-only convenience account. NEVER used when IONM_ENV is
# production: there an admin must come from IONM_ADMIN_BOOTSTRAP_* or
# IONM_ADMIN_ACCOUNTS_JSON (see core/config.py). The FALLBACK_* values are only
# used for display when there is no account context at all.
DEV_ADMIN_USERNAME = os.environ.get("IONM_DEV_ADMIN_USERNAME", "dev-admin")
DEV_ADMIN_DISPLAY_NAME = os.environ.get("IONM_DEV_ADMIN_DISPLAY_NAME", "Development Admin")
DEV_ADMIN_PASSWORD = os.environ.get("IONM_DEV_ADMIN_PASSWORD", "")
FALLBACK_ADMIN_USERNAME = "admin"
FALLBACK_ADMIN_DISPLAY_NAME = "Administrator"
ADMIN_COOKIE_NAME = "ionm_admin_session"
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 260000


class AdminAuthError(RuntimeError):
    pass


def normalize_username(value: str) -> str:
    return " ".join(value.strip().lower().split())


def verify_admin_credentials(username: str, password: str) -> bool:
    return authenticate_admin(username, password) is not None


def authenticate_admin(username: str, password: str) -> dict[str, str] | None:
    normalized = normalize_username(username)
    for account in admin_accounts():
        if not hmac.compare_digest(normalize_username(account["username"]), normalized):
            continue
        if verify_account_password(account, password):
            return public_admin(account)
    return None


def create_admin_session(admin: dict[str, str] | None = None) -> dict[str, Any]:
    account = admin or public_admin()
    expires_at = datetime.now(UTC) + timedelta(hours=max(1, settings.admin_session_hours))
    payload = {
        "sub": account["username"],
        "display_name": account["display_name"],
        "role": account["role"],
        "expires_at": expires_at.isoformat(),
    }
    return {"token": sign_payload(payload), "admin": account, "expires_at": expires_at}


def create_user_session(user: dict[str, Any]) -> dict[str, Any]:
    expires_at = datetime.now(UTC) + timedelta(hours=max(1, settings.admin_session_hours))
    role = "admin" if user.get("access_level") == "admin" else "user"
    payload = {
        "sub": normalize_username(str(user.get("username", ""))),
        "display_name": user_display_name(user),
        "role": role,
        "user_id": str(user.get("user_id", "")),
        "access_level": str(user.get("access_level") or role),
        "expires_at": expires_at.isoformat(),
    }
    return {"token": sign_payload(payload), "admin": public_admin(payload), "expires_at": expires_at}


def public_admin(account: dict[str, Any] | None = None) -> dict[str, str]:
    source = account or default_admin_account() or {}
    username = source.get("username") or source.get("sub") or FALLBACK_ADMIN_USERNAME
    role = str(source.get("role") or ("admin" if source.get("access_level") == "admin" else "user"))
    return {
        "username": normalize_username(str(username)),
        "display_name": str(source.get("display_name") or source.get("username") or FALLBACK_ADMIN_DISPLAY_NAME),
        "role": role,
    }


def user_display_name(user: dict[str, Any]) -> str:
    return str(user.get("full_name") or " ".join([str(user.get("first_name", "")).strip(), str(user.get("last_name", "")).strip()]).strip() or user.get("username") or "User")


def default_admin_account() -> dict[str, str] | None:
    """Resolve the implicit/default admin account.

    Priority:
      1. Explicit bootstrap account from the environment (production-safe).
      2. Local/dev convenience account — ONLY when not running in production.
      3. None — production with no bootstrap has no implicit admin; accounts
         must then come from IONM_ADMIN_ACCOUNTS_JSON.
    """
    # The platform OWNER (control-plane) is the implicit/default admin on the
    # main instance only. Tenant instances run with owner_enabled=False, so
    # their implicit admin can never gain owner privileges.
    default_role = "owner" if settings.owner_enabled else "admin"
    if settings.admin_bootstrap_username:
        account: dict[str, str] = {
            "username": settings.admin_bootstrap_username,
            "display_name": settings.admin_bootstrap_display_name or settings.admin_bootstrap_username,
            "role": default_role,
        }
        if settings.admin_bootstrap_password_hash:
            account["password_hash"] = settings.admin_bootstrap_password_hash
            return account
        if settings.admin_bootstrap_password:
            account["password"] = settings.admin_bootstrap_password
            return account
        return None
    if not settings.is_production and DEV_ADMIN_PASSWORD:
        return {
            "username": DEV_ADMIN_USERNAME,
            "display_name": DEV_ADMIN_DISPLAY_NAME,
            "role": default_role,
            "password": DEV_ADMIN_PASSWORD,
        }
    return None


def admin_accounts() -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    default = default_admin_account()
    if default:
        accounts.append(default)
    if not settings.admin_accounts_json:
        return accounts
    try:
        configured = json.loads(settings.admin_accounts_json)
    except json.JSONDecodeError as error:
        raise AdminAuthError("Invalid admin account configuration.") from error
    if not isinstance(configured, list):
        raise AdminAuthError("Admin account configuration must be a list.")
    for item in configured:
        if not isinstance(item, dict):
            raise AdminAuthError("Admin account entries must be objects.")
        username = normalize_username(str(item.get("username", "")))
        password_hash = str(item.get("password_hash", ""))
        if not username or not password_hash:
            raise AdminAuthError("Admin account entries require username and password_hash.")
        accounts.append({
            "username": username,
            "display_name": str(item.get("display_name") or username),
            "role": "admin",
            "password_hash": password_hash,
        })
    return dedupe_accounts(accounts)


def dedupe_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for account in accounts:
        deduped[normalize_username(str(account["username"]))] = account
    return list(deduped.values())


def verify_account_password(account: dict[str, Any], password: str) -> bool:
    password_hash = account.get("password_hash")
    if password_hash:
        return verify_password_hash(password, str(password_hash))
    return hmac.compare_digest(password, str(account.get("password", "")))


def hash_password(password: str, salt: str | None = None) -> str:
    salt_value = salt or base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("=")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_value.encode("utf-8"), PASSWORD_HASH_ITERATIONS)
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}${salt_value}${encoded_digest}"


def verify_password_hash(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected_digest = password_hash.split("$", 3)
        iterations = int(iterations_raw)
    except ValueError:
        return False
    if algorithm != PASSWORD_HASH_ALGORITHM or iterations < 100000 or not salt or not expected_digest:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(encoded_digest, expected_digest)


def safe_redirect_path(value: str | None, default: str = "/") -> str:
    if not value:
        return default
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return default
    if parsed.path in {"/login.html", "/api/admin/login"}:
        return default
    return value


def sign_payload(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload_json).decode("ascii")
    signature = hmac.new(settings.admin_session_secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded_payload}.{signature}"


def verify_platform_session(token: str | None) -> dict[str, Any]:
    return verify_signed_session(token, allowed_roles={"owner", "admin", "user"})


def verify_admin_session(token: str | None) -> dict[str, Any]:
    # Owner is a superset of admin: it may use every admin endpoint as well.
    return verify_signed_session(token, allowed_roles={"owner", "admin"})


def verify_owner_session(token: str | None) -> dict[str, Any]:
    return verify_signed_session(token, allowed_roles={"owner"})


def verify_signed_session(token: str | None, *, allowed_roles: set[str]) -> dict[str, Any]:
    if not token or "." not in token:
        raise AdminAuthError("Admin login required.")
    encoded_payload, signature = token.rsplit(".", 1)
    expected = hmac.new(settings.admin_session_secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise AdminAuthError("Invalid admin session.")
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload.encode("ascii")))
        expires_at = datetime.fromisoformat(payload["expires_at"])
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        raise AdminAuthError("Invalid admin session payload.") from error
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise AdminAuthError("Admin session expired.")
    role = str(payload.get("role", ""))
    if role not in allowed_roles:
        raise AdminAuthError("Invalid admin role.")
    if role == "user" and not payload.get("user_id"):
        raise AdminAuthError("Invalid user session.")
    return payload


def known_admin_username(username: str) -> bool:
    normalized = normalize_username(username)
    return any(hmac.compare_digest(normalize_username(account["username"]), normalized) for account in admin_accounts())


def find_admin_account(username: str) -> dict[str, Any] | None:
    """Return the configured env/bootstrap admin account whose username matches.

    ``username`` is matched case/space-insensitively against the *identity*
    username of every account returned by :func:`admin_accounts` (the env
    bootstrap account plus any ``IONM_ADMIN_ACCOUNTS_JSON`` entries). Returns
    ``None`` when no env account owns that username — e.g. for database-backed
    users created through the admin panel.
    """
    normalized = normalize_username(username)
    for account in admin_accounts():
        if hmac.compare_digest(normalize_username(str(account["username"])), normalized):
            return account
    return None


def env_account_fingerprint(account: dict[str, Any]) -> str:
    """Stable fingerprint of an env account's *current* credential.

    A self-service credential override (stored in the database) is bound to this
    fingerprint. When the owner rotates the account from the control plane the
    env credential changes, the fingerprint no longer matches, and the override
    is transparently treated as stale — so the owner-set credential wins without
    any cross-process cleanup. The raw hash/password is never exposed: only its
    SHA-256 digest is persisted.
    """
    basis = account.get("password_hash")
    if not basis:
        basis = "pw:" + str(account.get("password", ""))
    return hashlib.sha256(str(basis).encode("utf-8")).hexdigest()


def public_admin_identity(account: dict[str, Any], *, login_username: str | None = None, display_name: str | None = None) -> dict[str, str]:
    """Like :func:`public_admin` but lets a fresh override substitute the login
    username and display name while keeping the role anchored to the env account
    (the role is NEVER taken from user-writable storage)."""
    base = public_admin(account)
    if login_username:
        base["username"] = normalize_username(login_username)
    if display_name:
        base["display_name"] = display_name
    return base
