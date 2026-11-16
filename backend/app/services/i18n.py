"""Lightweight i18n helpers for language resolution and content localization.

The platform is Greek-first (default ``el``) with English (``en``) as the second
language. A user's choice is carried by the ``ionm_lang`` cookie that the frontend
language switcher writes; the backend reads it to pin AI answer language and to
serve localized content fields (``title_el`` / ``title_en`` ...).
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import Request

from backend.app.core.config import settings

SUPPORTED_LANGS: tuple[str, ...] = ("el", "en")
LANG_COOKIE = "ionm_lang"
LANG_NAMES: dict[str, str] = {"el": "Greek", "en": "English"}
FALLBACK_LANG = "el"


def normalize_lang(value: str | None) -> str | None:
    """Return a supported language code from a raw value, or ``None``.

    Accepts full locales such as ``en-US`` / ``el_GR`` and collapses them to the
    base language code.
    """
    if not value:
        return None
    code = value.strip().lower().replace("_", "-")
    if not code:
        return None
    base = code.split("-", 1)[0]
    return base if base in SUPPORTED_LANGS else None


def default_lang() -> str:
    """The configured default language (``IONM_DEFAULT_LANG``), guaranteed valid."""
    return normalize_lang(settings.default_lang) or FALLBACK_LANG


def resolve_request_lang(request: Request) -> str:
    """Resolve the active language for a request.

    Precedence: explicit ``?lang=`` query → ``ionm_lang`` cookie →
    ``Accept-Language`` header → configured default.
    """
    query_lang = normalize_lang(request.query_params.get("lang"))
    if query_lang:
        return query_lang

    cookie_lang = normalize_lang(request.cookies.get(LANG_COOKIE))
    if cookie_lang:
        return cookie_lang

    header = request.headers.get("accept-language")
    if header:
        for part in header.split(","):
            candidate = normalize_lang(part.split(";", 1)[0])
            if candidate:
                return candidate

    return default_lang()


def language_name(language: str | None) -> str:
    """Human-readable English name of a language code (for prompts/logs)."""
    return LANG_NAMES.get(normalize_lang(language) or default_lang(), "Greek")


def localize(document: Mapping[str, Any], base: str, language: str | None) -> Any:
    """Pick ``document[base_<language>]`` with graceful fallback.

    Falls back to the default-language field and then the bare ``base`` key so a
    missing translation never produces an empty value.
    """
    if not isinstance(document, Mapping):
        return None
    lang = normalize_lang(language) or default_lang()
    for key in (f"{base}_{lang}", f"{base}_{FALLBACK_LANG}", base):
        value = document.get(key)
        if value not in (None, ""):
            return value
    return None
