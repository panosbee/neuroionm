"""Tele-IONM live remote-supervision session service.

This module powers real, multi-user remote IONM supervision built entirely on
the platform's existing stack (FastAPI + MongoDB + signed-cookie identity).
There is no mock data and no external paid service: presence, the request →
accept → active → ended lifecycle, the shared synchronized workspace, chat,
annotations and the audit log are all genuine.

The module is split into:

* Pure logic (identity projection, expert eligibility, session/event document
  factories, the status state-machine, presence projection, inbound-message
  validation, transcript/summary building) — fully unit-testable without a
  database.
* Thin async MongoDB IO wrappers used by the routes and the cross-worker bus.

Phase 1 deliberately covers everything except live screen/voice *media*
(which requires WebRTC infrastructure). Media is layered on later over the same
signalling channel; the `consent` gate and session model already account for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase


# Reserved for a future multi-tenant split. Today the platform is a single
# organization, so every record is stamped with this tenant and all queries are
# tenant-scoped, which makes a later migration a pure data change.
DEFAULT_TENANT = "default"

# A presence row is considered "online" only if it was refreshed within this
# window. A MongoDB TTL index also sweeps stale rows, but the query-time filter
# guarantees freshness between TTL sweeps (which run at ~60s granularity).
PRESENCE_TTL_SECONDS = 45
PRESENCE_FRESH_SECONDS = 30

SESSION_STATUSES = ("requested", "accepted", "active", "ended", "declined", "cancelled")
ACTIVE_STATUSES = ("requested", "accepted", "active")

# Allowed status transitions for the session state-machine.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"accepted", "declined", "cancelled"},
    "accepted": {"active", "ended", "cancelled"},
    "active": {"ended"},
    "ended": set(),
    "declined": set(),
    "cancelled": set(),
}

# Access levels that may act as a remote consultant (accept a support request).
EXPERT_ACCESS_LEVELS = {"admin", "clinician", "educator"}


class TeleError(RuntimeError):
    """Raised for invalid tele-session operations (bad transition, not found...)."""


# --------------------------------------------------------------------------- #
# Identity & eligibility (pure)
# --------------------------------------------------------------------------- #
def identity_from_session(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a verified platform-session payload into a tele identity.

    `payload` is the output of `verify_platform_session` and carries `sub`,
    `display_name`, `role` and (for database users) `user_id`/`access_level`.
    """
    role = str(payload.get("role") or "user")
    access_level = str(payload.get("access_level") or ("admin" if role == "admin" else "user"))
    # The hardcoded admin has no user_id; fall back to a stable id from the username.
    user_id = str(payload.get("user_id") or "").strip() or f"admin:{str(payload.get('sub') or '').strip()}"
    return {
        "user_id": user_id,
        "username": str(payload.get("sub") or "").strip(),
        "display_name": str(payload.get("display_name") or payload.get("sub") or "User").strip(),
        "role": role,
        "access_level": access_level,
        "tenant_id": DEFAULT_TENANT,
    }


def is_expert_eligible(identity: dict[str, Any]) -> bool:
    """Whether an identity may act as a remote consultant."""
    if str(identity.get("role")) == "admin":
        return True
    return str(identity.get("access_level")) in EXPERT_ACCESS_LEVELS


def is_resumable_status(status: str | None) -> bool:
    """A session is resumable while it is still in progress (not ended/closed)."""
    return str(status) in ACTIVE_STATUSES


def can_resume(session: dict[str, Any] | None, identity: dict[str, Any]) -> bool:
    """Whether `identity` may re-attach to an in-progress session after a drop.

    Item #6: a transient network blip must not lose the consult. The session
    state lives in Mongo, so a reconnecting participant (initiator, consultant,
    or an admin) can resume any session that has not yet ended.
    """
    if not session:
        return False
    if not is_resumable_status(session.get("status")):
        return False
    if str(identity.get("role")) == "admin":
        return True
    return identity.get("user_id") in {session.get("initiator_id"), session.get("consultant_id")}


def can_transition(current: str, target: str) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise TeleError(f"Illegal session transition {current!r} -> {target!r}.")


# --------------------------------------------------------------------------- #
# Document factories (pure)
# --------------------------------------------------------------------------- #
_CREDENTIAL_FIELDS = ("board_cert_number", "license_number", "license_region")


def _credential_stamp(credentials: dict[str, Any] | None) -> dict[str, Any]:
    """A medicolegal credential snapshot embedded into a session record (item #7).

    Captures board certification, licence number and licence region/jurisdiction
    of a participant at the instant of the consult, plus whether any credential
    was on file, so the sealed session proves under what authority each clinician
    acted.
    """
    source = credentials or {}
    snapshot: dict[str, Any] = {}
    on_file = False
    for field in _CREDENTIAL_FIELDS:
        value = source.get(field)
        text = str(value).strip() if value is not None else ""
        snapshot[field] = text or None
        on_file = on_file or bool(text)
    snapshot["on_file"] = on_file
    snapshot["snapshot_at"] = datetime.now(UTC)
    return snapshot


def new_session_document(
    *,
    initiator: dict[str, Any],
    node_key: str | None,
    case_id: str | None,
    message: str | None,
    initiator_credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "session_id": uuid4().hex,
        "tenant_id": str(initiator.get("tenant_id") or DEFAULT_TENANT),
        "status": "requested",
        "case_id": case_id,
        "node_key": node_key,
        "request_message": (message or "").strip() or None,
        "initiator_id": initiator["user_id"],
        "initiator_name": initiator["display_name"],
        "initiator_role": initiator.get("role"),
        # Medicolegal credential stamp of the requester at request time (item #7).
        "initiator_credentials": _credential_stamp(initiator_credentials or initiator.get("credentials")),
        "consultant_id": None,
        "consultant_name": None,
        "consultant_credentials": None,
        "consent_screen_share": False,
        "recording_ref": None,
        "chat_count": 0,
        "annotation_count": 0,
        "workspace_update_count": 0,
        "created_at": now,
        "updated_at": now,
        "requested_at": now,
        "accepted_at": None,
        "started_at": None,
        "ended_at": None,
        "end_reason": None,
    }


def new_event_document(
    session_id: str,
    *,
    tenant_id: str,
    event_type: str,
    actor: dict[str, Any] | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event_id": uuid4().hex,
        "session_id": session_id,
        "tenant_id": tenant_id,
        "event_type": event_type,
        "actor_id": (actor or {}).get("user_id"),
        "actor_name": (actor or {}).get("display_name"),
        "payload": payload or {},
        "created_at": datetime.now(UTC),
    }


def public_session(document: dict[str, Any]) -> dict[str, Any]:
    clean = dict(document)
    clean.pop("_id", None)
    return clean


# --------------------------------------------------------------------------- #
# Presence projection (pure)
# --------------------------------------------------------------------------- #
def presence_is_fresh(presence: dict[str, Any], *, now: datetime | None = None) -> bool:
    moment = now or datetime.now(UTC)
    last_seen = presence.get("last_seen")
    if not isinstance(last_seen, datetime):
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    return (moment - last_seen) <= timedelta(seconds=PRESENCE_FRESH_SECONDS)


def project_experts(
    presence_rows: list[dict[str, Any]],
    *,
    exclude_user_id: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return the deduplicated set of currently-available experts.

    A user may have several open tabs (connections); they are collapsed to one
    expert entry keyed by `user_id`.
    """
    moment = now or datetime.now(UTC)
    by_user: dict[str, dict[str, Any]] = {}
    for row in presence_rows:
        if not row.get("available_for_tele"):
            continue
        if not presence_is_fresh(row, now=moment):
            continue
        identity = {"role": row.get("role"), "access_level": row.get("access_level")}
        if not is_expert_eligible(identity):
            continue
        user_id = str(row.get("user_id") or "")
        if not user_id or user_id == (exclude_user_id or ""):
            continue
        existing = by_user.get(user_id)
        if existing is None:
            by_user[user_id] = {
                "user_id": user_id,
                "display_name": row.get("display_name") or "Expert",
                "role": row.get("role"),
                "access_level": row.get("access_level"),
                "node_key": row.get("node_key"),
                "connections": 1,
            }
        else:
            existing["connections"] += 1
    return sorted(by_user.values(), key=lambda item: item["display_name"].lower())


# --------------------------------------------------------------------------- #
# Inbound WebSocket message validation (pure)
# --------------------------------------------------------------------------- #
CLIENT_MESSAGE_TYPES = {
    "heartbeat",
    "set_available",
    "set_node",
    "request_support",
    "accept",
    "decline",
    "cancel",
    "consent",
    "workspace_update",
    "chat",
    "annotation",
    # Phase 2 live media (WebRTC voice + screen share). `webrtc_signal` relays
    # opaque SDP/ICE between the two session peers; `media_state` announces which
    # local tracks (audio / screen) a participant is publishing.
    "webrtc_signal",
    "media_state",
    # Transport reliability (#6): a reconnecting client re-attaches to its
    # in-progress session without losing state.
    "resume",
    "end",
}

_MAX_CHAT_LEN = 2000
_MAX_MESSAGE_LEN = 2000
# A complete SDP offer/answer is a few KB; cap generously to bound abuse while
# never truncating a legitimate description (truncation would corrupt the SDP).
_MAX_SDP_LEN = 200_000
_MAX_ICE_CANDIDATE_LEN = 2000


def validate_client_message(raw: Any) -> dict[str, Any]:
    """Validate and normalize an inbound client message. Raises TeleError."""
    if not isinstance(raw, dict):
        raise TeleError("Message must be a JSON object.")
    msg_type = raw.get("type")
    if msg_type not in CLIENT_MESSAGE_TYPES:
        raise TeleError(f"Unsupported message type: {msg_type!r}.")
    out: dict[str, Any] = {"type": msg_type}

    if msg_type in {"accept", "decline", "cancel", "consent", "workspace_update", "chat", "annotation", "webrtc_signal", "media_state", "resume", "end"}:
        session_id = str(raw.get("session_id") or "").strip()
        if not session_id:
            raise TeleError(f"{msg_type} requires a session_id.")
        out["session_id"] = session_id

    if msg_type == "set_available":
        out["available"] = bool(raw.get("available"))
    elif msg_type == "set_node":
        out["node_key"] = _clean_str(raw.get("node_key"), 240)
    elif msg_type == "request_support":
        out["node_key"] = _clean_str(raw.get("node_key"), 240)
        out["case_id"] = _clean_str(raw.get("case_id"), 120)
        out["message"] = _clean_str(raw.get("message"), _MAX_MESSAGE_LEN)
    elif msg_type == "consent":
        out["granted"] = bool(raw.get("granted"))
    elif msg_type == "workspace_update":
        patch = raw.get("patch")
        if not isinstance(patch, dict):
            raise TeleError("workspace_update requires a patch object.")
        out["patch"] = _clean_workspace_patch(patch)
    elif msg_type == "chat":
        text = _clean_str(raw.get("text"), _MAX_CHAT_LEN)
        if not text:
            raise TeleError("chat requires non-empty text.")
        out["text"] = text
    elif msg_type == "annotation":
        annotation = raw.get("annotation")
        if not isinstance(annotation, dict):
            raise TeleError("annotation requires an annotation object.")
        out["annotation"] = _clean_annotation(annotation)
    elif msg_type == "webrtc_signal":
        out["signal"] = _clean_webrtc_signal(raw.get("signal"))
    elif msg_type == "media_state":
        out["audio"] = bool(raw.get("audio"))
        out["screen"] = bool(raw.get("screen"))
    return out


def _clean_str(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


def _clean_workspace_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Whitelist of synchronizable workspace fields for the shared view."""
    allowed_scalars = {
        "node_key": 240,
        "map_view": 40,
        "active_tab": 40,
        "timeline_marker": 200,
        "focus_pin": 200,
    }
    out: dict[str, Any] = {}
    for key, max_len in allowed_scalars.items():
        if key in patch:
            out[key] = _clean_str(patch.get(key), max_len)
    # A small free-form cursor position (normalized 0..1) for the shared body map.
    cursor = patch.get("cursor")
    if isinstance(cursor, dict):
        try:
            out["cursor"] = {"x": round(float(cursor["x"]), 4), "y": round(float(cursor["y"]), 4)}
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _clean_annotation(annotation: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("x", "y"):
        try:
            out[key] = round(float(annotation[key]), 4)
        except (KeyError, TypeError, ValueError):
            out[key] = None
    out["label"] = _clean_str(annotation.get("label"), 160)
    out["kind"] = _clean_str(annotation.get("kind"), 40) or "marker"
    return out


def _clean_webrtc_signal(signal: Any) -> dict[str, Any]:
    """Validate an inbound WebRTC signalling payload (offer / answer / ICE).

    The SDP and ICE candidate strings are opaque to the server and relayed
    verbatim to the peer; we only validate shape and bound size so the bus and
    the peer never receive a malformed or abusive frame.
    """
    if not isinstance(signal, dict):
        raise TeleError("webrtc_signal requires a signal object.")
    kind = signal.get("kind")
    if kind not in {"offer", "answer", "candidate"}:
        raise TeleError("webrtc_signal has an unsupported kind.")
    out: dict[str, Any] = {"kind": kind}
    if kind in {"offer", "answer"}:
        sdp = signal.get("sdp")
        if not isinstance(sdp, str) or not sdp.strip():
            raise TeleError(f"{kind} requires an sdp string.")
        if len(sdp) > _MAX_SDP_LEN:
            raise TeleError("sdp exceeds the maximum allowed size.")
        out["sdp"] = sdp
        return out
    # kind == "candidate"
    candidate = signal.get("candidate")
    # A null candidate is the standard end-of-candidates marker.
    if candidate is None:
        out["candidate"] = None
        return out
    if not isinstance(candidate, dict):
        raise TeleError("candidate must be an object or null.")
    cand_str = candidate.get("candidate")
    if cand_str is not None and not isinstance(cand_str, str):
        raise TeleError("candidate.candidate must be a string.")
    if isinstance(cand_str, str) and len(cand_str) > _MAX_ICE_CANDIDATE_LEN:
        raise TeleError("candidate exceeds the maximum allowed size.")
    sdp_mline_index = candidate.get("sdpMLineIndex")
    out["candidate"] = {
        "candidate": cand_str or "",
        "sdpMid": _clean_str(candidate.get("sdpMid"), 100),
        "sdpMLineIndex": sdp_mline_index if isinstance(sdp_mline_index, int) else None,
        "usernameFragment": _clean_str(candidate.get("usernameFragment"), 256),
    }
    return out


def build_session_summary(session: dict[str, Any], events: list[dict[str, Any]]) -> str:
    """Human-readable transcript summary used for the case timeline / report."""
    chats = [event for event in events if event.get("event_type") == "chat_message"]
    annotations = [event for event in events if event.get("event_type") == "annotation"]
    duration = _format_duration(session.get("started_at"), session.get("ended_at"))
    lines = [
        f"Tele-IONM session {session.get('session_id')}",
        f"Initiator: {session.get('initiator_name')}",
        f"Consultant: {session.get('consultant_name') or '—'}",
        f"Status: {session.get('status')} ({session.get('end_reason') or 'completed'})",
        f"Duration: {duration}",
        f"Messages: {len(chats)} chat, {annotations.__len__()} annotations, {session.get('workspace_update_count', 0)} workspace syncs",
    ]
    return "\n".join(lines)


def _format_duration(start: Any, end: Any) -> str:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return "n/a"
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    seconds = max(0, int((end - start).total_seconds()))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs}s"


# --------------------------------------------------------------------------- #
# MongoDB IO (thin async wrappers)
# --------------------------------------------------------------------------- #
async def ensure_tele_indexes(database: AsyncIOMotorDatabase) -> None:
    await database.tele_presence.create_index("connection_id", unique=True)
    await database.tele_presence.create_index("user_id")
    await database.tele_presence.create_index("last_seen", expireAfterSeconds=PRESENCE_TTL_SECONDS)
    await database.tele_sessions.create_index("session_id", unique=True)
    await database.tele_sessions.create_index([("tenant_id", 1), ("status", 1)])
    await database.tele_sessions.create_index("case_id")
    await database.tele_events.create_index([("session_id", 1), ("created_at", 1)])


async def upsert_presence(
    database: AsyncIOMotorDatabase,
    *,
    connection_id: str,
    identity: dict[str, Any],
    available_for_tele: bool,
    node_key: str | None,
) -> None:
    now = datetime.now(UTC)
    await database.tele_presence.update_one(
        {"connection_id": connection_id},
        {
            "$set": {
                "connection_id": connection_id,
                "tenant_id": identity.get("tenant_id", DEFAULT_TENANT),
                "user_id": identity["user_id"],
                "username": identity.get("username"),
                "display_name": identity.get("display_name"),
                "role": identity.get("role"),
                "access_level": identity.get("access_level"),
                "available_for_tele": bool(available_for_tele),
                "node_key": node_key,
                "last_seen": now,
            },
            "$setOnInsert": {"connected_at": now},
        },
        upsert=True,
    )


async def touch_presence(database: AsyncIOMotorDatabase, connection_id: str) -> None:
    await database.tele_presence.update_one(
        {"connection_id": connection_id},
        {"$set": {"last_seen": datetime.now(UTC)}},
    )


async def remove_presence(database: AsyncIOMotorDatabase, connection_id: str) -> None:
    await database.tele_presence.delete_one({"connection_id": connection_id})


async def list_available_experts(
    database: AsyncIOMotorDatabase,
    *,
    tenant_id: str,
    exclude_user_id: str | None = None,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(seconds=PRESENCE_FRESH_SECONDS)
    rows = await database.tele_presence.find(
        {"tenant_id": tenant_id, "available_for_tele": True, "last_seen": {"$gte": cutoff}}
    ).to_list(length=200)
    return project_experts(rows, exclude_user_id=exclude_user_id)


async def create_session(
    database: AsyncIOMotorDatabase,
    *,
    initiator: dict[str, Any],
    node_key: str | None,
    case_id: str | None,
    message: str | None,
    initiator_credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = new_session_document(
        initiator=initiator,
        node_key=node_key,
        case_id=case_id,
        message=message,
        initiator_credentials=initiator_credentials,
    )
    await database.tele_sessions.insert_one(document)
    await append_event(
        database,
        document["session_id"],
        tenant_id=document["tenant_id"],
        event_type="session_requested",
        actor=initiator,
        payload={"node_key": node_key, "case_id": case_id, "message": document["request_message"]},
    )
    return public_session(document)


async def get_session(database: AsyncIOMotorDatabase, session_id: str) -> dict[str, Any] | None:
    document = await database.tele_sessions.find_one({"session_id": session_id})
    return public_session(document) if document else None


async def find_resumable_session_for_user(
    database: AsyncIOMotorDatabase,
    *,
    user_id: str,
    tenant_id: str = DEFAULT_TENANT,
) -> dict[str, Any] | None:
    """The most recent in-progress session this user can re-attach to (#6).

    Used on reconnect to offer a seamless resume: after a dropped socket the
    client is told which session is still live so it can rejoin without the user
    having to re-initiate the consult.
    """
    cursor = (
        database.tele_sessions.find(
            {
                "tenant_id": tenant_id,
                "status": {"$in": list(ACTIVE_STATUSES)},
                "$or": [{"initiator_id": user_id}, {"consultant_id": user_id}],
            }
        )
        .sort("updated_at", -1)
        .limit(1)
    )
    rows = await cursor.to_list(length=1)
    return public_session(rows[0]) if rows else None


async def accept_session(
    database: AsyncIOMotorDatabase,
    *,
    session_id: str,
    consultant: dict[str, Any],
    consultant_credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = await database.tele_sessions.find_one({"session_id": session_id})
    if not session:
        raise TeleError("Session not found.")
    if not is_expert_eligible(consultant):
        raise TeleError("Only an eligible consultant can accept a session.")
    if session["initiator_id"] == consultant["user_id"]:
        raise TeleError("You cannot accept your own request.")
    assert_transition(session["status"], "accepted")
    now = datetime.now(UTC)
    await database.tele_sessions.update_one(
        {"session_id": session_id, "status": "requested"},
        {
            "$set": {
                "status": "active",
                "consultant_id": consultant["user_id"],
                "consultant_name": consultant["display_name"],
                # Medicolegal credential stamp of the consultant at accept time.
                "consultant_credentials": _credential_stamp(
                    consultant_credentials or consultant.get("credentials")
                ),
                "accepted_at": now,
                "started_at": now,
                "updated_at": now,
            }
        },
    )
    await append_event(database, session_id, tenant_id=session["tenant_id"], event_type="session_accepted", actor=consultant)
    await append_event(database, session_id, tenant_id=session["tenant_id"], event_type="session_started", actor=consultant)
    updated = await database.tele_sessions.find_one({"session_id": session_id})
    return public_session(updated)


async def resolve_request(
    database: AsyncIOMotorDatabase,
    *,
    session_id: str,
    actor: dict[str, Any],
    target_status: Literal["declined", "cancelled"],
) -> dict[str, Any]:
    session = await database.tele_sessions.find_one({"session_id": session_id})
    if not session:
        raise TeleError("Session not found.")
    assert_transition(session["status"], target_status)
    now = datetime.now(UTC)
    await database.tele_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"status": target_status, "updated_at": now, "ended_at": now, "end_reason": target_status}},
    )
    await append_event(database, session_id, tenant_id=session["tenant_id"], event_type=f"session_{target_status}", actor=actor)
    updated = await database.tele_sessions.find_one({"session_id": session_id})
    return public_session(updated)


async def end_session(
    database: AsyncIOMotorDatabase,
    *,
    session_id: str,
    actor: dict[str, Any],
    reason: str = "completed",
) -> dict[str, Any]:
    session = await database.tele_sessions.find_one({"session_id": session_id})
    if not session:
        raise TeleError("Session not found.")
    if session["status"] in {"ended", "declined", "cancelled"}:
        return public_session(session)
    assert_transition(session["status"], "ended")
    now = datetime.now(UTC)
    await database.tele_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"status": "ended", "updated_at": now, "ended_at": now, "end_reason": reason}},
    )
    await append_event(database, session_id, tenant_id=session["tenant_id"], event_type="session_ended", actor=actor, payload={"reason": reason})
    updated = await database.tele_sessions.find_one({"session_id": session_id})
    return public_session(updated)


async def set_consent(
    database: AsyncIOMotorDatabase,
    *,
    session_id: str,
    actor: dict[str, Any],
    granted: bool,
) -> None:
    session = await database.tele_sessions.find_one({"session_id": session_id})
    if not session:
        raise TeleError("Session not found.")
    await database.tele_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"consent_screen_share": bool(granted), "updated_at": datetime.now(UTC)}},
    )
    await append_event(
        database,
        session_id,
        tenant_id=session["tenant_id"],
        event_type="consent_granted" if granted else "consent_revoked",
        actor=actor,
    )


async def record_chat(database: AsyncIOMotorDatabase, *, session_id: str, actor: dict[str, Any], text: str) -> dict[str, Any]:
    session = await database.tele_sessions.find_one({"session_id": session_id})
    if not session:
        raise TeleError("Session not found.")
    event = await append_event(
        database, session_id, tenant_id=session["tenant_id"], event_type="chat_message", actor=actor, payload={"text": text}
    )
    await database.tele_sessions.update_one({"session_id": session_id}, {"$inc": {"chat_count": 1}, "$set": {"updated_at": datetime.now(UTC)}})
    return event


async def record_annotation(database: AsyncIOMotorDatabase, *, session_id: str, actor: dict[str, Any], annotation: dict[str, Any]) -> dict[str, Any]:
    session = await database.tele_sessions.find_one({"session_id": session_id})
    if not session:
        raise TeleError("Session not found.")
    event = await append_event(
        database, session_id, tenant_id=session["tenant_id"], event_type="annotation", actor=actor, payload=annotation
    )
    await database.tele_sessions.update_one({"session_id": session_id}, {"$inc": {"annotation_count": 1}, "$set": {"updated_at": datetime.now(UTC)}})
    return event


async def record_workspace_update(database: AsyncIOMotorDatabase, *, session_id: str) -> None:
    await database.tele_sessions.update_one(
        {"session_id": session_id},
        {"$inc": {"workspace_update_count": 1}, "$set": {"updated_at": datetime.now(UTC)}},
    )


async def record_media_event(
    database: AsyncIOMotorDatabase,
    *,
    session_id: str,
    actor: dict[str, Any],
    audio: bool,
    screen: bool,
) -> dict[str, Any]:
    """Persist a media-state change (voice / screen share toggled) to the audit log.

    Only the low-frequency `media_state` announcements are recorded — never the
    high-frequency WebRTC SDP/ICE signalling — so the case timeline keeps a clean,
    human-meaningful record of when live voice or screen sharing was active.
    """
    session = await database.tele_sessions.find_one({"session_id": session_id})
    if not session:
        raise TeleError("Session not found.")
    event = await append_event(
        database,
        session_id,
        tenant_id=session["tenant_id"],
        event_type="media_state",
        actor=actor,
        payload={"audio": bool(audio), "screen": bool(screen)},
    )
    await database.tele_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"media_audio": bool(audio), "media_screen": bool(screen), "updated_at": datetime.now(UTC)}},
    )
    return event


async def append_event(
    database: AsyncIOMotorDatabase,
    session_id: str,
    *,
    tenant_id: str,
    event_type: str,
    actor: dict[str, Any] | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = new_event_document(session_id, tenant_id=tenant_id, event_type=event_type, actor=actor, payload=payload)
    await database.tele_events.insert_one(document)
    return public_session(document)


async def list_events(database: AsyncIOMotorDatabase, session_id: str) -> list[dict[str, Any]]:
    events = await database.tele_events.find({"session_id": session_id}).sort("created_at", 1).to_list(length=None)
    return [public_session(event) for event in events]
