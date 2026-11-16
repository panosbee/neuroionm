from __future__ import annotations

from urllib.parse import quote, urlsplit

from fastapi import Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from backend.app.core.config import settings
from backend.app.services.admin_auth import (
    ADMIN_COOKIE_NAME,
    AdminAuthError,
    verify_admin_session,
    verify_owner_session,
    verify_platform_session,
)


PUBLIC_PATHS = {
    "/landing.html",
    "/login.html",
    "/login.js",
    "/i18n.js",
    "/styles.css",
    "/favicon.ico",
    "/api/live",
    "/api/ready",
    "/api/health",
    "/api/admin/login",
    "/api/admin/logout",
    "/api/admin/session",
}

PUBLIC_PREFIXES = (
    "/assets/",
)


class AuthGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = normalize_path(request.url.path)
        if request.method == "OPTIONS" or is_public_path(path):
            return await call_next(request)

        if is_admin_page(path):
            if has_admin_session(request):
                return await call_next(request)
            if has_valid_session(request):
                return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

        if is_owner_page(path):
            if has_owner_session(request):
                return await call_next(request)
            if has_valid_session(request):
                return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

        if has_valid_session(request):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                {"detail": "Login required."},
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"Cache-Control": "no-store"},
            )

        # On the MAIN control-plane instance, unauthenticated visitors landing on
        # the application root get the public marketing page instead of being
        # bounced straight to login. Tenant instances keep going to their login.
        if settings.owner_enabled and path in {"/", "/index.html"}:
            return RedirectResponse(url="/landing.html", status_code=status.HTTP_303_SEE_OTHER)

        next_path = safe_next_path(path, request.url.query)
        return RedirectResponse(url=f"/login.html?next={quote(next_path, safe='')}", status_code=status.HTTP_303_SEE_OTHER)


def normalize_path(path: str) -> str:
    return path or "/"


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def is_admin_page(path: str) -> bool:
    return path in {"/admin.html", "/admin"}


def is_owner_page(path: str) -> bool:
    return path in {"/owner.html", "/owner"}


def has_admin_session(request: Request) -> bool:
    try:
        verify_admin_session(request.cookies.get(ADMIN_COOKIE_NAME))
        return True
    except AdminAuthError:
        return False


def has_owner_session(request: Request) -> bool:
    try:
        verify_owner_session(request.cookies.get(ADMIN_COOKIE_NAME))
        return True
    except AdminAuthError:
        return False


def has_valid_session(request: Request) -> bool:
    try:
        verify_platform_session(request.cookies.get(ADMIN_COOKIE_NAME))
        return True
    except AdminAuthError:
        return False


def safe_next_path(path: str | None, query: str | None = None) -> str:
    candidate = path or "/"
    if query:
        candidate = f"{candidate}?{query}"
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    if parsed.path in {"/login.html", "/api/admin/login"}:
        return "/"
    return candidate