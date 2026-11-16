"""Cross-worker message bus + per-process connection hub for Tele-IONM.

Production runs the app under several Gunicorn workers, so a naive in-process
WebSocket registry would never let two users on different workers see each other.
To stay on the existing stack (no Redis, no extra service) this module uses a
**MongoDB capped collection as a tailable pub/sub channel**: every worker tails
`tele_bus` and fans each published message out to the WebSocket connections it
holds locally. This is the same proven pattern used for Mongo-based queues.

`TeleHub` is the per-process singleton that owns the local connections and the
background tail task; `publish()` is how any request handler emits an event that
must reach connections on every worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import CursorType
from pymongo.errors import PyMongoError

from backend.app.services.tele_session import DEFAULT_TENANT, list_available_experts


BUS_COLLECTION = "tele_bus"
BUS_CAPPED_BYTES = 8 * 1024 * 1024  # ample headroom for signalling traffic


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_safe_text(data: dict[str, Any]) -> str:
    """Serialize an outbound WebSocket payload, encoding datetimes as ISO strings.

    Starlette's ``WebSocket.send_json`` uses a plain ``json.dumps`` that rejects
    ``datetime`` values; Tele payloads (session documents, event timestamps) carry
    them, so every outbound frame is serialized through this helper instead.
    """
    return json.dumps(data, default=_json_default, separators=(",", ":"))


async def ensure_bus(database: AsyncIOMotorDatabase) -> None:
    """Create the capped bus collection (idempotent) and seed a tail anchor."""
    names = await database.list_collection_names()
    if BUS_COLLECTION not in names:
        try:
            await database.create_collection(BUS_COLLECTION, capped=True, size=BUS_CAPPED_BYTES)
        except PyMongoError:
            # Another worker created it concurrently — that is fine.
            pass
    if await database[BUS_COLLECTION].estimated_document_count() == 0:
        await database[BUS_COLLECTION].insert_one({"type": "__anchor__", "created_at": datetime.now(UTC)})


async def publish(
    database: AsyncIOMotorDatabase,
    *,
    audience: str,
    data: dict[str, Any],
    to_connection: str | None = None,
    to_user: str | None = None,
    session_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> None:
    """Emit a message onto the bus for delivery on every worker.

    `audience` is one of: connection | user | session | experts | tenant.
    """
    await database[BUS_COLLECTION].insert_one(
        {
            "audience": audience,
            "to_connection": to_connection,
            "to_user": to_user,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "data": data,
            "created_at": datetime.now(UTC),
        }
    )


@dataclass
class TeleConnection:
    connection_id: str
    websocket: WebSocket
    identity: dict[str, Any]
    available_for_tele: bool = False
    node_key: str | None = None
    session_id: str | None = None

    @property
    def user_id(self) -> str:
        return str(self.identity.get("user_id"))

    @property
    def tenant_id(self) -> str:
        return str(self.identity.get("tenant_id") or DEFAULT_TENANT)


class TeleHub:
    """Owns this worker's live WebSocket connections and the bus tail task."""

    def __init__(self) -> None:
        self._connections: dict[str, TeleConnection] = {}
        self._database: AsyncIOMotorDatabase | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------- #
    async def start(self, database: AsyncIOMotorDatabase) -> None:
        self._database = database
        self._stop.clear()
        await ensure_bus(database)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._tail_loop(), name="tele-bus-tail")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for connection in list(self._connections.values()):
            with contextlib.suppress(Exception):
                await connection.websocket.close()
        self._connections.clear()

    # -- local connection registry ----------------------------------------- #
    def register(self, connection: TeleConnection) -> None:
        self._connections[connection.connection_id] = connection

    def unregister(self, connection_id: str) -> None:
        self._connections.pop(connection_id, None)

    def get(self, connection_id: str) -> TeleConnection | None:
        return self._connections.get(connection_id)

    def set_session(self, connection_id: str, session_id: str | None) -> None:
        connection = self._connections.get(connection_id)
        if connection is not None:
            connection.session_id = session_id

    # -- direct send to a single local socket ------------------------------- #
    async def send_local(self, connection_id: str, data: dict[str, Any]) -> None:
        connection = self._connections.get(connection_id)
        if connection is None:
            return
        try:
            await connection.websocket.send_text(json_safe_text(data))
        except Exception:
            self.unregister(connection_id)

    # -- presence push ------------------------------------------------------ #
    async def push_presence(self, tenant_id: str) -> None:
        """Recompute the expert roster and push it to local tenant connections."""
        if self._database is None:
            return
        targets = [c for c in self._connections.values() if c.tenant_id == tenant_id]
        if not targets:
            return
        for connection in targets:
            experts = await list_available_experts(
                self._database, tenant_id=tenant_id, exclude_user_id=connection.user_id
            )
            await self.send_local(connection.connection_id, {"type": "presence", "experts": experts})

    # -- bus tail ----------------------------------------------------------- #
    async def _tail_loop(self) -> None:
        assert self._database is not None
        collection = self._database[BUS_COLLECTION]
        while not self._stop.is_set():
            try:
                anchor = await collection.find_one(sort=[("$natural", -1)])
                query: dict[str, Any] = {"_id": {"$gt": anchor["_id"]}} if anchor else {}
                cursor = collection.find(query, cursor_type=CursorType.TAILABLE_AWAIT).max_await_time_ms(1000)
                while cursor.alive and not self._stop.is_set():
                    async for document in cursor:
                        await self._dispatch(document)
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except PyMongoError:
                await asyncio.sleep(1.0)
            except Exception:
                await asyncio.sleep(1.0)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        audience = message.get("audience")
        if not audience:
            return
        data = message.get("data") or {}
        tenant_id = str(message.get("tenant_id") or DEFAULT_TENANT)

        # Presence-refresh signal: every worker recomputes its own roster and pushes
        # it to local connections. The raw signal is never forwarded to clients.
        if data.get("type") == "__presence_dirty__":
            await self.push_presence(tenant_id)
            return

        # When an initiator's request is accepted, bind their local connections to
        # the new session before forwarding, so subsequent session-scoped messages
        # reach them too.
        if data.get("type") == "request_accepted":
            session = data.get("session") or {}
            new_session_id = session.get("session_id")
            target_user = message.get("to_user")
            if new_session_id and target_user:
                for connection in self._connections.values():
                    if connection.user_id == target_user:
                        connection.session_id = new_session_id

        targets = self._resolve_targets(message, tenant_id)
        for connection_id in targets:
            await self.send_local(connection_id, data)

        # After a session ends, detach local connections from it.
        if data.get("type") == "session_ended":
            ended_session = message.get("session_id")
            for connection in self._connections.values():
                if connection.session_id == ended_session:
                    connection.session_id = None

    def _resolve_targets(self, message: dict[str, Any], tenant_id: str) -> list[str]:
        audience = message.get("audience")
        if audience == "connection":
            target = message.get("to_connection")
            return [target] if target and target in self._connections else []
        if audience == "user":
            user_id = message.get("to_user")
            return [c.connection_id for c in self._connections.values() if c.user_id == user_id]
        if audience == "session":
            session_id = message.get("session_id")
            return [c.connection_id for c in self._connections.values() if c.session_id and c.session_id == session_id]
        if audience == "experts":
            return [
                c.connection_id
                for c in self._connections.values()
                if c.tenant_id == tenant_id and c.available_for_tele
            ]
        if audience == "tenant":
            return [c.connection_id for c in self._connections.values() if c.tenant_id == tenant_id]
        return []


tele_hub = TeleHub()
