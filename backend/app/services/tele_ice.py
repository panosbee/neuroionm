"""ICE server configuration for Tele-IONM Phase 2 (WebRTC voice + screen share).

WebRTC peers need ICE servers to traverse NAT/firewalls:

* **STUN** lets each peer discover its public address — enough for most home /
  office networks where a direct peer-to-peer path exists.
* **TURN** relays media when a direct path is impossible (symmetric NAT, strict
  hospital firewalls). Without TURN a meaningful fraction of real-world calls
  fail, so production deployments should configure a TURN server.

TURN credentials are NEVER hardcoded in the frontend. They are served per
authenticated user from `GET /api/tele/ice-config`, which calls
:func:`build_ice_servers`. Two credential modes are supported:

* **Ephemeral (recommended)** — the coturn ``use-auth-secret`` / "TURN REST API"
  scheme: a shared secret produces short-lived, per-user credentials
  (``username = "<expiry>:<user_id>"``, ``credential = base64(HMAC-SHA1(secret,
  username))``). Credentials expire, so a leaked one is useless within the TTL.
* **Static** — a fixed username/password, for TURN servers configured with
  long-term credentials.

This module is pure (no IO) so it is fully unit-testable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any


# Lower bound for an ephemeral-credential lifetime; a session should never get a
# credential that can expire mid-call.
MIN_TURN_TTL_SECONDS = 60


def _as_urls(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def make_turn_credentials(
    *,
    secret: str,
    user_id: str,
    ttl_seconds: int,
    now: float | None = None,
) -> tuple[str, str]:
    """Return ``(username, credential)`` for coturn's ``use-auth-secret`` scheme.

    ``username`` is ``"<expiry-epoch>:<user_id>"`` and ``credential`` is the
    base64 of ``HMAC-SHA1(secret, username)`` — exactly what coturn validates.
    """
    moment = int(now if now is not None else time.time())
    expiry = moment + max(MIN_TURN_TTL_SECONDS, int(ttl_seconds))
    safe_user = (user_id or "tele").replace(":", "_")
    username = f"{expiry}:{safe_user}"
    digest = hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    credential = base64.b64encode(digest).decode("ascii")
    return username, credential


def build_ice_servers(
    *,
    user_id: str,
    stun_urls: str | list[str] | None,
    turn_urls: str | list[str] | None = None,
    turn_secret: str | None = None,
    turn_username: str | None = None,
    turn_credential: str | None = None,
    turn_ttl_seconds: int = 3600,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Build the ``RTCIceServer[]`` list sent to the browser.

    A STUN entry is always included when any STUN URL is configured. A TURN entry
    is included only when TURN URLs plus credentials (a shared secret, or a static
    username/password pair) are configured; otherwise it is omitted entirely —
    no broken or credential-less TURN entry is ever emitted.
    """
    servers: list[dict[str, Any]] = []

    stun = _as_urls(stun_urls)
    if stun:
        servers.append({"urls": stun})

    turn = _as_urls(turn_urls)
    if turn:
        if turn_secret:
            username, credential = make_turn_credentials(
                secret=turn_secret, user_id=user_id, ttl_seconds=turn_ttl_seconds, now=now
            )
            servers.append({"urls": turn, "username": username, "credential": credential})
        elif turn_username and turn_credential:
            servers.append({"urls": turn, "username": turn_username, "credential": turn_credential})

    return servers
