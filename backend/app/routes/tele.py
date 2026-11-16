"""Tele-IONM routes: real-time WebSocket signalling + REST lifecycle.

The WebSocket channel (`/api/tele/ws`) carries the whole live collaboration:
presence, the request -> accept -> active -> ended lifecycle, the shared
synchronized workspace, chat, annotations and the consent gate. Because Starlette's
`BaseHTTPMiddleware` auth gate only runs for HTTP scopes, the WebSocket endpoint
authenticates itself from the signed `ionm_admin_session` cookie.

REST endpoints expose read access (expert roster, session transcript) and the
end-of-session integration that links a session into the existing Case timeline,
which in turn feeds the established Debrief and Report engines.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from backend.app.core.config import settings
from backend.app.db.mongo import get_database, mongo_store
from backend.app.models.domain import CaseEventCreate, new_case_event_document
from backend.app.services import professional_credentials as creds
from backend.app.services import tele_session as ts
from backend.app.services.admin_auth import ADMIN_COOKIE_NAME, AdminAuthError, verify_platform_session
from backend.app.services.expert_rota import (
    RotaError,
    acknowledge_summons,
    cancel_summons,
    create_rota_entry,
    escalate_summons,
    get_summons,
    list_rota,
    open_summons,
    remove_rota_entry,
    resolve_on_call,
)
from backend.app.services.tele_bus import TeleConnection, json_safe_text, publish, tele_hub
from backend.app.services.tele_ice import build_ice_servers
from backend.app.services.tree import to_public_document


router = APIRouter(prefix="/api/tele", tags=["tele"])


async def _ws_send(websocket: WebSocket, data: dict[str, Any]) -> None:
    """Send a JSON WebSocket frame with datetime-aware encoding.

    Starlette's ``WebSocket.send_json`` cannot serialize the ``datetime`` values
    carried in Tele session documents, so every outbound frame goes through this
    helper, which mirrors the serialization used by the cross-worker bus.
    """
    await websocket.send_text(json_safe_text(data))


# --------------------------------------------------------------------------- #
# REST dependency
# --------------------------------------------------------------------------- #
def current_identity(request: Request) -> dict[str, Any]:
    try:
        payload = verify_platform_session(request.cookies.get(ADMIN_COOKIE_NAME))
    except AdminAuthError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.") from error
    return ts.identity_from_session(payload)


def require_admin_identity(identity: dict[str, Any] = Depends(current_identity)) -> dict[str, Any]:
    if identity.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required.")
    return identity


class LinkCaseRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=120)


class RotaScheduleModel(BaseModel):
    kind: Literal["always", "recurring", "window"]
    weekdays: list[int] | None = None
    start_minute: int | None = Field(default=None, ge=0, le=1439)
    end_minute: int | None = Field(default=None, ge=1, le=2879)
    tz_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    start: str | None = None
    end: str | None = None


class RotaContactModel(BaseModel):
    channel: Literal["web_push", "viber", "sms", "email"]
    target: str = Field(min_length=1, max_length=200)


class RotaEntryRequest(BaseModel):
    expert_id: str = Field(min_length=1, max_length=120)
    expert_name: str = Field(min_length=1, max_length=160)
    modalities: list[str] = Field(default_factory=list, max_length=24)
    tier: int = Field(default=1, ge=1, le=9)
    schedule: RotaScheduleModel
    contacts: list[RotaContactModel] = Field(default_factory=list, max_length=12)
    active: bool = True


class SummonsOpenRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    modality: str | None = Field(default=None, max_length=80)


# --------------------------------------------------------------------------- #
# REST endpoints
# --------------------------------------------------------------------------- #
@router.get("/experts")
async def list_experts(
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    experts = await ts.list_available_experts(
        database, tenant_id=identity["tenant_id"], exclude_user_id=identity["user_id"]
    )
    return {"experts": experts, "self_eligible": ts.is_expert_eligible(identity)}


# --------------------------------------------------------------------------- #
# Expert rota + escalation summons (item #3)
# --------------------------------------------------------------------------- #
@router.get("/rota")
async def get_rota(
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    entries = await list_rota(database, tenant_id=identity["tenant_id"])
    return {"entries": entries, "count": len(entries)}


@router.post("/rota", status_code=status.HTTP_201_CREATED)
async def create_rota(
    payload: RotaEntryRequest,
    identity: dict[str, Any] = Depends(require_admin_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        entry = await create_rota_entry(
            database,
            tenant_id=identity["tenant_id"],
            expert_id=payload.expert_id,
            expert_name=payload.expert_name,
            modalities=payload.modalities,
            tier=payload.tier,
            schedule=payload.schedule.model_dump(exclude_none=True),
            contacts=[contact.model_dump() for contact in payload.contacts],
            active=payload.active,
            created_by=identity.get("user_id"),
        )
    except RotaError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"entry": entry}


@router.delete("/rota/{rota_id}")
async def delete_rota(
    rota_id: str,
    _: dict[str, Any] = Depends(require_admin_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    removed = await remove_rota_entry(database, rota_id=rota_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rota entry not found.")
    return {"status": "deleted", "rota_id": rota_id}


@router.get("/on-call")
async def get_on_call(
    modality: str | None = None,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    on_call = await resolve_on_call(database, tenant_id=identity["tenant_id"], modality=modality)
    return {"modality": modality, "on_call": on_call, "count": len(on_call)}


@router.post("/summons", status_code=status.HTTP_201_CREATED)
async def open_summons_endpoint(
    payload: SummonsOpenRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    session = await ts.get_session(database, payload.session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if identity["user_id"] != session.get("initiator_id") and identity["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the initiator can summon support.")
    online_ids = {
        expert["user_id"]
        for expert in await ts.list_available_experts(database, tenant_id=identity["tenant_id"])
    }
    summons = await open_summons(
        database,
        tenant_id=identity["tenant_id"],
        session_id=payload.session_id,
        modality=payload.modality or session.get("node_key"),
        requested_by=identity,
        online_expert_ids=online_ids,
    )
    return {"summons": summons}


@router.post("/summons/{summons_id}/escalate")
async def escalate_summons_endpoint(
    summons_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    summons = await get_summons(database, summons_id)
    if not summons:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Summons not found.")
    if identity["user_id"] != summons.get("requested_by_id") and identity["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the requester can escalate.")
    try:
        updated = await escalate_summons(database, summons_id=summons_id)
    except RotaError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"summons": updated}


@router.post("/summons/{summons_id}/ack")
async def acknowledge_summons_endpoint(
    summons_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    if not ts.is_expert_eligible(identity):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an eligible consultant can acknowledge.")
    try:
        updated = await acknowledge_summons(database, summons_id=summons_id, expert=identity)
    except RotaError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"summons": updated}


@router.post("/summons/{summons_id}/cancel")
async def cancel_summons_endpoint(
    summons_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    summons = await get_summons(database, summons_id)
    if not summons:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Summons not found.")
    if identity["user_id"] != summons.get("requested_by_id") and identity["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the requester can cancel.")
    try:
        updated = await cancel_summons(database, summons_id=summons_id)
    except RotaError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"summons": updated}


@router.get("/ice-config")
async def get_ice_config(
    identity: dict[str, Any] = Depends(current_identity),
) -> dict[str, Any]:
    """Return the per-user ICE server list for the WebRTC voice / screen channel.

    TURN credentials (when configured) are minted per authenticated user with a
    short lifetime, so they are never embedded in static frontend assets.
    """
    ice_servers = build_ice_servers(
        user_id=identity["user_id"],
        stun_urls=settings.tele_stun_urls,
        turn_urls=settings.tele_turn_urls,
        turn_secret=settings.tele_turn_secret,
        turn_username=settings.tele_turn_username,
        turn_credential=settings.tele_turn_credential,
        turn_ttl_seconds=settings.tele_turn_ttl_seconds,
    )
    return {"ice_servers": ice_servers, "ttl": settings.tele_turn_ttl_seconds}


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    session = await ts.get_session(database, session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if identity["user_id"] not in {session.get("initiator_id"), session.get("consultant_id")} and identity["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant of this session.")
    events = await ts.list_events(database, session_id)
    return {"session": session, "events": events, "summary": ts.build_session_summary(session, events)}


@router.post("/sessions/{session_id}/end")
async def end_session_rest(
    session_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    session = await ts.get_session(database, session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if identity["user_id"] not in {session.get("initiator_id"), session.get("consultant_id")} and identity["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant of this session.")
    ended = await ts.end_session(database, session_id=session_id, actor=identity)
    await publish(
        database,
        audience="session",
        session_id=session_id,
        tenant_id=identity["tenant_id"],
        data={"type": "session_ended", "session_id": session_id, "session": ended},
    )
    return ended


@router.post("/sessions/{session_id}/link-case")
async def link_session_to_case(
    session_id: str,
    payload: LinkCaseRequest,
    identity: dict[str, Any] = Depends(current_identity),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    """Attach a finished session's transcript to a Case timeline as an event.

    This is the integration point with the existing Debrief / Report engines:
    once linked, the case event participates in any AI debrief or generated report.
    """
    session = await ts.get_session(database, session_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if identity["user_id"] not in {session.get("initiator_id"), session.get("consultant_id")} and identity["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant of this session.")

    case = await database.cases.find_one({"case_id": payload.case_id})
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found.")

    events = await ts.list_events(database, session_id)
    summary = ts.build_session_summary(session, events)
    event_payload = CaseEventCreate(
        event_type="tele_session",
        title=f"Tele-IONM consult — {session.get('consultant_name') or 'remote expert'}",
        description=summary,
        node_key=session.get("node_key"),
        metadata={
            "session_id": session_id,
            "initiator": session.get("initiator_name"),
            "consultant": session.get("consultant_name"),
            "chat_count": session.get("chat_count", 0),
            "annotation_count": session.get("annotation_count", 0),
            "workspace_update_count": session.get("workspace_update_count", 0),
            "status": session.get("status"),
        },
    )
    document = new_case_event_document(payload.case_id, event_payload)
    await database.case_events.insert_one(document)
    await database.cases.update_one({"case_id": payload.case_id}, {"$set": {"updated_at": document["created_at"]}})
    await database.tele_sessions.update_one(
        {"session_id": session_id}, {"$set": {"case_id": payload.case_id, "updated_at": document["created_at"]}}
    )
    return {"case_event": to_public_document(document), "session_id": session_id, "case_id": payload.case_id}


# --------------------------------------------------------------------------- #
# WebSocket signalling
# --------------------------------------------------------------------------- #
@router.websocket("/ws")
async def tele_ws(websocket: WebSocket) -> None:
    try:
        payload = verify_platform_session(websocket.cookies.get(ADMIN_COOKIE_NAME))
    except AdminAuthError:
        await websocket.close(code=4401)
        return

    identity = ts.identity_from_session(payload)
    database = mongo_store.db()
    connection = TeleConnection(connection_id=uuid4().hex, websocket=websocket, identity=identity)

    await websocket.accept()
    tele_hub.register(connection)
    await ts.upsert_presence(
        database,
        connection_id=connection.connection_id,
        identity=identity,
        available_for_tele=connection.available_for_tele,
        node_key=connection.node_key,
    )
    await _ws_send(
        websocket,
        {
            "type": "welcome",
            "connection_id": connection.connection_id,
            "identity": {
                "user_id": identity["user_id"],
                "display_name": identity["display_name"],
                "role": identity["role"],
                "expert_eligible": ts.is_expert_eligible(identity),
            },
        },
    )
    experts = await ts.list_available_experts(database, tenant_id=identity["tenant_id"], exclude_user_id=identity["user_id"])
    await _ws_send(websocket, {"type": "presence", "experts": experts})

    # Transport reliability (#6): if this user has a still-live session, surface it
    # so a client that dropped and reconnected can resume seamlessly.
    resumable = await ts.find_resumable_session_for_user(
        database, user_id=identity["user_id"], tenant_id=identity["tenant_id"]
    )
    if resumable:
        await _ws_send(websocket, {"type": "resumable_session", "session": resumable})

    try:
        while True:
            raw = await websocket.receive_json()
            try:
                message = ts.validate_client_message(raw)
                await _handle_message(database, connection, message)
            except ts.TeleError as error:
                await _ws_send(websocket, {"type": "error", "detail": str(error)})
    except WebSocketDisconnect:
        pass
    except Exception:
        # Any unexpected error: drop the connection cleanly rather than crashing
        # the worker. State lives in Mongo, so the user can simply reconnect.
        pass
    finally:
        tele_hub.unregister(connection.connection_id)
        await ts.remove_presence(database, connection.connection_id)
        if connection.session_id:
            await publish(
                database,
                audience="session",
                session_id=connection.session_id,
                tenant_id=identity["tenant_id"],
                data={
                    "type": "participant_left",
                    "session_id": connection.session_id,
                    "user": {"user_id": identity["user_id"], "display_name": identity["display_name"]},
                },
            )
        await publish(
            database,
            audience="tenant",
            tenant_id=identity["tenant_id"],
            data={"type": "__presence_dirty__"},
        )


async def _handle_message(
    database: AsyncIOMotorDatabase,
    connection: TeleConnection,
    message: dict[str, Any],
) -> None:
    identity = connection.identity
    tenant_id = identity["tenant_id"]
    msg_type = message["type"]

    if msg_type == "heartbeat":
        await ts.touch_presence(database, connection.connection_id)
        await _ws_send(connection.websocket, {"type": "heartbeat_ack"})
        return

    if msg_type == "set_available":
        connection.available_for_tele = bool(message["available"])
        await ts.upsert_presence(
            database,
            connection_id=connection.connection_id,
            identity=identity,
            available_for_tele=connection.available_for_tele,
            node_key=connection.node_key,
        )
        await _ws_send(connection.websocket, {"type": "availability", "available": connection.available_for_tele})
        await publish(database, audience="tenant", tenant_id=tenant_id, data={"type": "__presence_dirty__"})
        return

    if msg_type == "set_node":
        connection.node_key = message.get("node_key")
        await ts.upsert_presence(
            database,
            connection_id=connection.connection_id,
            identity=identity,
            available_for_tele=connection.available_for_tele,
            node_key=connection.node_key,
        )
        return

    if msg_type == "request_support":
        if connection.session_id:
            raise ts.TeleError("You are already in a session.")
        session = await ts.create_session(
            database,
            initiator=identity,
            node_key=message.get("node_key"),
            case_id=message.get("case_id"),
            message=message.get("message"),
            initiator_credentials=await creds.get_user_credentials(database, identity.get("user_id")),
        )
        connection.session_id = session["session_id"]
        await _ws_send(connection.websocket, {"type": "support_requested", "session": session})
        await publish(
            database,
            audience="experts",
            tenant_id=tenant_id,
            data={"type": "incoming_request", "session": session},
        )
        return

    if msg_type == "accept":
        session = await ts.accept_session(
            database,
            session_id=message["session_id"],
            consultant=identity,
            consultant_credentials=await creds.get_user_credentials(database, identity.get("user_id")),
        )
        connection.session_id = session["session_id"]
        await _ws_send(connection.websocket, {"type": "session_started", "session": session, "role": "consultant"})
        await publish(
            database,
            audience="user",
            to_user=session["initiator_id"],
            tenant_id=tenant_id,
            data={"type": "request_accepted", "session": session},
        )
        await publish(
            database,
            audience="experts",
            tenant_id=tenant_id,
            data={"type": "request_closed", "session_id": session["session_id"]},
        )
        await publish(database, audience="tenant", tenant_id=tenant_id, data={"type": "__presence_dirty__"})
        return

    if msg_type == "resume":
        session = await ts.get_session(database, message["session_id"])
        if not ts.can_resume(session, identity):
            raise ts.TeleError("This session cannot be resumed.")
        connection.session_id = session["session_id"]
        events = await ts.list_events(database, session["session_id"])
        await _ws_send(
            connection.websocket,
            {
                "type": "session_resumed",
                "session": session,
                "events": events,
                "role": "consultant" if identity["user_id"] == session.get("consultant_id") else "initiator",
            },
        )
        # Let the peer know the participant is back so it can re-offer media.
        await publish(
            database,
            audience="session",
            session_id=session["session_id"],
            tenant_id=tenant_id,
            data={
                "type": "participant_rejoined",
                "session_id": session["session_id"],
                "user": {"user_id": identity["user_id"], "display_name": identity["display_name"]},
            },
        )
        return

    if msg_type == "decline":
        # A single expert dismissing a broadcast request does not cancel it for the
        # others; record it for the audit log only.
        session = await ts.get_session(database, message["session_id"])
        if session:
            await ts.append_event(
                database,
                message["session_id"],
                tenant_id=tenant_id,
                event_type="request_declined_by_expert",
                actor=identity,
            )
        return

    if msg_type == "cancel":
        session = await ts.get_session(database, message["session_id"])
        if not session:
            raise ts.TeleError("Session not found.")
        if session.get("initiator_id") != identity["user_id"]:
            raise ts.TeleError("Only the initiator can cancel the request.")
        await ts.resolve_request(database, session_id=message["session_id"], actor=identity, target_status="cancelled")
        connection.session_id = None
        await _ws_send(connection.websocket, {"type": "request_cancelled", "session_id": message["session_id"]})
        await publish(
            database,
            audience="experts",
            tenant_id=tenant_id,
            data={"type": "request_closed", "session_id": message["session_id"]},
        )
        return

    # The remaining message types require the sender to be a participant.
    session = await _require_participant(database, connection, message["session_id"])

    if msg_type == "consent":
        await ts.set_consent(database, session_id=session["session_id"], actor=identity, granted=bool(message["granted"]))
        await publish(
            database,
            audience="session",
            session_id=session["session_id"],
            tenant_id=tenant_id,
            data={"type": "consent", "granted": bool(message["granted"]), "by": identity["display_name"]},
        )
        return

    if msg_type == "workspace_update":
        await ts.record_workspace_update(database, session_id=session["session_id"])
        await publish(
            database,
            audience="session",
            session_id=session["session_id"],
            tenant_id=tenant_id,
            data={
                "type": "workspace_update",
                "session_id": session["session_id"],
                "patch": message["patch"],
                "sender": {"user_id": identity["user_id"], "display_name": identity["display_name"]},
            },
        )
        return

    if msg_type == "chat":
        event = await ts.record_chat(database, session_id=session["session_id"], actor=identity, text=message["text"])
        await publish(
            database,
            audience="session",
            session_id=session["session_id"],
            tenant_id=tenant_id,
            data={
                "type": "chat",
                "session_id": session["session_id"],
                "text": message["text"],
                "sender": {"user_id": identity["user_id"], "display_name": identity["display_name"]},
                "created_at": event.get("created_at").isoformat() if event.get("created_at") else None,
            },
        )
        return

    if msg_type == "annotation":
        await ts.record_annotation(database, session_id=session["session_id"], actor=identity, annotation=message["annotation"])
        await publish(
            database,
            audience="session",
            session_id=session["session_id"],
            tenant_id=tenant_id,
            data={
                "type": "annotation",
                "session_id": session["session_id"],
                "annotation": message["annotation"],
                "sender": {"user_id": identity["user_id"], "display_name": identity["display_name"]},
            },
        )
        return

    if msg_type == "webrtc_signal":
        # Opaque SDP/ICE relayed verbatim to the other peer. Audience "session"
        # also echoes to the sender, so the frontend ignores frames whose
        # `from.user_id` is itself. ICE is high-frequency, so this is NOT audited.
        await publish(
            database,
            audience="session",
            session_id=session["session_id"],
            tenant_id=tenant_id,
            data={
                "type": "webrtc_signal",
                "session_id": session["session_id"],
                "signal": message["signal"],
                "from": {"user_id": identity["user_id"], "display_name": identity["display_name"]},
            },
        )
        return

    if msg_type == "media_state":
        # Low-frequency announcement of which local tracks (voice / screen) a peer
        # is publishing. Recorded to the audit log and relayed to the other peer.
        await ts.record_media_event(
            database,
            session_id=session["session_id"],
            actor=identity,
            audio=message["audio"],
            screen=message["screen"],
        )
        await publish(
            database,
            audience="session",
            session_id=session["session_id"],
            tenant_id=tenant_id,
            data={
                "type": "media_state",
                "session_id": session["session_id"],
                "audio": bool(message["audio"]),
                "screen": bool(message["screen"]),
                "from": {"user_id": identity["user_id"], "display_name": identity["display_name"]},
            },
        )
        return

    if msg_type == "end":
        ended = await ts.end_session(database, session_id=session["session_id"], actor=identity)
        # Do NOT detach this connection here: the session_ended frame is fanned out
        # by the bus, which selects targets by connection.session_id. Nulling it now
        # would exclude the very sender who ended the session. The bus detaches every
        # participant (sender included) in its session_ended post-processing.
        await publish(
            database,
            audience="session",
            session_id=ended["session_id"],
            tenant_id=tenant_id,
            data={"type": "session_ended", "session_id": ended["session_id"], "session": ended},
        )
        await publish(
            database,
            audience="experts",
            tenant_id=tenant_id,
            data={"type": "request_closed", "session_id": ended["session_id"]},
        )
        return


async def _require_participant(
    database: AsyncIOMotorDatabase,
    connection: TeleConnection,
    session_id: str,
) -> dict[str, Any]:
    session = await ts.get_session(database, session_id)
    if not session:
        raise ts.TeleError("Session not found.")
    if connection.user_id not in {session.get("initiator_id"), session.get("consultant_id")}:
        raise ts.TeleError("Not a participant of this session.")
    if session.get("status") in {"ended", "declined", "cancelled"}:
        raise ts.TeleError("Session is no longer active.")
    connection.session_id = session_id
    return session
