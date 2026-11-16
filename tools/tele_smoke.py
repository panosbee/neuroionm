"""Live two-client integration smoke test for Tele-IONM.

Runs against a RUNNING dev server (default http://127.0.0.1:8002). It does NOT mock
anything: it mints two genuinely-signed platform session cookies with the app's own
auth service (one admin initiator, one clinician consultant), opens two real
WebSocket connections to ``/api/tele/ws`` and drives the full collaboration
lifecycle — presence -> request -> accept -> chat (both ways) -> workspace sync ->
annotation -> consent -> end — asserting cross-connection delivery through the
Mongo-backed bus. Finally it verifies the REST session transcript and the
end-of-session case-link integration.

Usage (from repo root, with the dev server already running):
    .\\.venv\\Scripts\\python.exe tools\\tele_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

import httpx
import websockets

from backend.app.services.admin_auth import (
    ADMIN_COOKIE_NAME,
    create_admin_session,
    create_user_session,
)

HTTP_BASE = os.environ.get("TELE_SMOKE_HTTP", "http://127.0.0.1:8002")
WS_URL = os.environ.get("TELE_SMOKE_WS", "ws://127.0.0.1:8002/api/tele/ws")

results: list[str] = []

# Tracks the artifacts created during this run so cleanup can remove exactly them,
# even if a later step fails mid-flight.
created: dict[str, str | None] = {"session_id": "", "case_id": None}


def ok(step: str, detail: str = "") -> None:
    results.append(f"PASS | {step}" + (f" | {detail}" if detail else ""))


def bad(step: str, detail: str = "") -> None:
    results.append(f"FAIL | {step}" + (f" | {detail}" if detail else ""))


def _cookie(token: str) -> str:
    return f"{ADMIN_COOKIE_NAME}={token}"


async def connect(token: str):
    header = {"Cookie": _cookie(token)}
    last_error: Exception | None = None
    for kwargs in ({"additional_headers": header}, {"extra_headers": header}):
        try:
            return await websockets.connect(WS_URL, open_timeout=15, **kwargs)
        except TypeError as error:  # header kwarg name differs across versions
            last_error = error
            continue
    raise last_error  # type: ignore[misc]


class Reader:
    """Buffers every inbound message so we can await specific ones out of order."""

    def __init__(self, ws, name: str) -> None:
        self.ws = ws
        self.name = name
        self.queue: asyncio.Queue = asyncio.Queue()
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            async for raw in self.ws:
                await self.queue.put(json.loads(raw))
        except Exception:
            pass

    async def expect(self, predicate, *, timeout: float = 12.0, label: str = "") -> dict:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        seen: list[str] = []
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError(
                    f"[{self.name}] timeout waiting for {label or predicate}; saw={seen}"
                )
            try:
                message = await asyncio.wait_for(self.queue.get(), remaining)
            except asyncio.TimeoutError as error:
                raise AssertionError(
                    f"[{self.name}] timeout waiting for {label or predicate}; saw={seen}"
                ) from error
            seen.append(message.get("type"))
            if predicate(message):
                return message

    async def close(self) -> None:
        self.task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


def is_type(expected: str):
    return lambda message: message.get("type") == expected


async def run_ws_flow() -> tuple[str, str]:
    admin_token = create_admin_session()["token"]
    consultant_token = create_user_session(
        {
            "username": "dr.consultant",
            "user_id": uuid.uuid4().hex,
            "access_level": "clinician",
            "first_name": "Remote",
            "last_name": "Consultant",
        }
    )["token"]

    initiator = await connect(admin_token)
    consultant = await connect(consultant_token)
    reader_a = Reader(initiator, "INITIATOR")
    reader_b = Reader(consultant, "CONSULTANT")
    session_id = ""
    try:
        welcome_a = await reader_a.expect(is_type("welcome"), label="welcome A")
        welcome_b = await reader_b.expect(is_type("welcome"), label="welcome B")
        ok(
            "both authenticated (welcome)",
            f"A.eligible={welcome_a['identity']['expert_eligible']} "
            f"B.eligible={welcome_b['identity']['expert_eligible']}",
        )
        consultant_uid = welcome_b["identity"]["user_id"]

        await reader_a.expect(is_type("presence"), label="presence A")
        await reader_b.expect(is_type("presence"), label="presence B")
        ok("initial presence delivered to both")

        await consultant.send(json.dumps({"type": "set_available", "available": True}))
        availability = await reader_b.expect(is_type("availability"), label="availability B")
        ok("consultant set available", f"available={availability['available']}")

        roster = await reader_a.expect(
            lambda m: m.get("type") == "presence"
            and any(e.get("user_id") == consultant_uid for e in m.get("experts", [])),
            timeout=12,
            label="A sees consultant in roster",
        )
        ok(
            "initiator roster shows consultant",
            f"experts={[e.get('display_name') for e in roster['experts']]}",
        )

        await initiator.send(
            json.dumps(
                {
                    "type": "request_support",
                    "node_key": "median_nerve_scn",
                    "message": "SSEP amplitude drop upper limb, requesting opinion",
                }
            )
        )
        requested = await reader_a.expect(is_type("support_requested"), label="support_requested A")
        session_id = requested["session"]["session_id"]
        created["session_id"] = session_id
        ok("support request created", f"session_id={session_id} status={requested['session']['status']}")

        await reader_b.expect(
            lambda m: m.get("type") == "incoming_request"
            and m.get("session", {}).get("session_id") == session_id,
            label="incoming_request B",
        )
        ok("consultant received incoming request")

        await consultant.send(json.dumps({"type": "accept", "session_id": session_id}))
        started = await reader_b.expect(is_type("session_started"), label="session_started B")
        ok(
            "consultant accepted -> session_started",
            f"role={started.get('role')} status={started['session']['status']}",
        )

        await reader_a.expect(
            lambda m: m.get("type") == "request_accepted"
            and m.get("session", {}).get("session_id") == session_id,
            label="request_accepted A",
        )
        ok("initiator notified of acceptance")

        await initiator.send(
            json.dumps({"type": "chat", "session_id": session_id, "text": "Baseline lost on left APB"})
        )
        chat1 = await reader_b.expect(
            lambda m: m.get("type") == "chat" and "Baseline lost" in m.get("text", ""),
            label="chat A->B",
        )
        ok("chat initiator -> consultant", f"sender={chat1['sender']['display_name']}")

        await consultant.send(
            json.dumps({"type": "chat", "session_id": session_id, "text": "Confirm anaesthetic stable?"})
        )
        await reader_a.expect(
            lambda m: m.get("type") == "chat" and "anaesthetic stable" in m.get("text", "").lower(),
            label="chat B->A",
        )
        ok("chat consultant -> initiator")

        await initiator.send(
            json.dumps(
                {
                    "type": "workspace_update",
                    "session_id": session_id,
                    "patch": {"map_view": "anterior", "node_key": "median_nerve_scn"},
                }
            )
        )
        workspace = await reader_b.expect(
            lambda m: m.get("type") == "workspace_update" and m.get("patch", {}).get("map_view") == "anterior",
            label="workspace_update A->B",
        )
        ok("workspace sync initiator -> consultant", f"patch={workspace['patch']}")

        await initiator.send(
            json.dumps(
                {
                    "type": "annotation",
                    "session_id": session_id,
                    "annotation": {"x": 0.5, "y": 0.42, "label": "focus here", "kind": "marker"},
                }
            )
        )
        annotation = await reader_b.expect(is_type("annotation"), label="annotation A->B")
        ok(
            "annotation initiator -> consultant",
            f"coords=({annotation['annotation']['x']},{annotation['annotation']['y']})",
        )

        await initiator.send(json.dumps({"type": "consent", "session_id": session_id, "granted": True}))
        consent = await reader_b.expect(
            lambda m: m.get("type") == "consent" and m.get("granted") is True,
            label="consent A->B",
        )
        ok("consent gate sync", f"by={consent.get('by')}")

        await initiator.send(json.dumps({"type": "end", "session_id": session_id}))
        ended_a = await reader_a.expect(is_type("session_ended"), label="session_ended A")
        await reader_b.expect(is_type("session_ended"), label="session_ended B")
        ok("session ended -> both notified", f"status={ended_a['session']['status']}")
    finally:
        await reader_a.close()
        await reader_b.close()

    return admin_token, session_id


def run_rest_checks(admin_token: str, session_id: str) -> str | None:
    cookies = {ADMIN_COOKIE_NAME: admin_token}
    case_id: str | None = None
    with httpx.Client(base_url=HTTP_BASE, cookies=cookies, timeout=20) as client:
        detail = client.get(f"/api/tele/sessions/{session_id}")
        if detail.status_code == 200:
            data = detail.json()
            ok(
                "REST session transcript",
                f"status={data['session']['status']} events={len(data['events'])} "
                f"chat_count={data['session'].get('chat_count')} "
                f"annotations={data['session'].get('annotation_count')}",
            )
        else:
            bad("REST session transcript", f"status={detail.status_code} {detail.text[:120]}")

        created_resp = client.post("/api/cases", json={"title": "Tele smoke case", "mode": "live"})
        if created_resp.status_code not in (200, 201):
            bad("REST case create", f"status={created_resp.status_code} {created_resp.text[:120]}")
            return case_id
        case_id = created_resp.json().get("case_id")
        created["case_id"] = case_id
        ok("REST case created", f"case_id={case_id}")

        linked = client.post(f"/api/tele/sessions/{session_id}/link-case", json={"case_id": case_id})
        if linked.status_code != 200:
            bad("REST link-case", f"status={linked.status_code} {linked.text[:160]}")
            return case_id
        ok("REST session linked to case", f"event={linked.json().get('case_event', {}).get('title')}")

        case = client.get(f"/api/cases/{case_id}")
        if case.status_code == 200 and "Tele-IONM" in case.text:
            event_titles = [e.get("title") for e in case.json().get("events", [])]
            ok("case timeline contains Tele-IONM event", f"events={event_titles}")
        else:
            bad("case timeline missing Tele-IONM event", f"status={case.status_code}")
    return case_id


async def cleanup() -> None:
    """Delete the exact artifacts this run created, so prod stays clean.

    Keyed strictly on the ids produced during this run (no broad deletes), this
    removes the smoke session, its events and the linked case + case event.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    from backend.app.core.config import settings

    session_id = created["session_id"]
    case_id = created["case_id"]
    if not session_id and not case_id:
        return
    client = AsyncIOMotorClient(settings.mongo_uri)
    try:
        database = client[settings.mongo_db]
        deleted_events = 0
        deleted_session = 0
        if session_id:
            deleted_events = (await database.tele_events.delete_many({"session_id": session_id})).deleted_count
            deleted_session = (await database.tele_sessions.delete_one({"session_id": session_id})).deleted_count
        deleted_case_events = 0
        deleted_case = 0
        if case_id:
            deleted_case_events = (await database.case_events.delete_many({"case_id": case_id})).deleted_count
            deleted_case = (await database.cases.delete_one({"case_id": case_id})).deleted_count
        ok(
            "prod cleanup (exact ids)",
            f"tele_events={deleted_events} tele_session={deleted_session} "
            f"case_events={deleted_case_events} case={deleted_case}",
        )
    finally:
        client.close()


async def main() -> int:
    do_cleanup = os.environ.get("TELE_SMOKE_CLEANUP") == "1"
    try:
        admin_token, session_id = await run_ws_flow()
        run_rest_checks(admin_token, session_id)
    finally:
        if do_cleanup:
            try:
                await cleanup()
            except Exception as error:  # cleanup must never mask the real result
                bad("prod cleanup", str(error)[:120])
    print("\n".join(results))
    failures = [line for line in results if line.startswith("FAIL")]
    print(f"\nSUMMARY: {len(results) - len(failures)}/{len(results)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
