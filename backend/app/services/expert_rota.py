"""Expert on-call rota, escalation routing and the summons ledger (item #3).

Real-time presence (`tele_session.list_available_experts`) answers *who is online
right now*. This module answers the harder operational question a hospital needs:
*whose duty is it to respond, and what happens when the primary on-call does not
answer*. It provides:

  * a scheduled on-call rota (recurring weekly windows, explicit windows, or
    always-on cover), keyed by modality and escalation tier;
  * a deterministic resolver that, for a given instant and modality, returns the
    ordered list of clinicians whose duty it is to respond;
  * an escalation plan that layers the rota over live presence so the system can
    walk from the primary on-call to the backup to off-platform summons;
  * a summons ledger (`tele_summons`) that records every escalation attempt and
    its acknowledgement — the auditable backbone of "we paged the on-call team".

Off-platform delivery (web-push / Viber / SMS) is represented as queued dispatch
intents on each summons attempt. The actual transport is performed by a notifier
that consumes pending dispatches; this module never fabricates a delivery it did
not make.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase


ROTA_COLLECTION = "expert_rota"
SUMMONS_COLLECTION = "tele_summons"
DEFAULT_TENANT = "default"

MINUTES_PER_DAY = 1440
MINUTES_PER_WEEK = MINUTES_PER_DAY * 7

SCHEDULE_KINDS = {"always", "recurring", "window"}
CONTACT_CHANNELS = {"web_push", "viber", "sms", "email"}
SUMMONS_STATUSES = {"pending", "answered", "exhausted", "cancelled"}


class RotaError(ValueError):
    """Raised for invalid rota / escalation operations."""


# --------------------------------------------------------------------------- #
# Validation / normalization (pure)
# --------------------------------------------------------------------------- #
def _clean_str(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_len] or None


def normalize_modalities(values: Any) -> list[str]:
    """A sorted, de-duplicated, lower-cased modality cover list ([] == covers all)."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out: set[str] = set()
    for item in values:
        text = str(item).strip().lower()
        if text:
            out.add(text)
    return sorted(out)


def normalize_contacts(values: Any) -> list[dict[str, str]]:
    """Validate off-platform contact channels for escalation summons."""
    if not values:
        return []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in values:
        if not isinstance(raw, dict):
            raise RotaError("Each contact must be an object with channel and target.")
        channel = str(raw.get("channel") or "").strip().lower()
        target = _clean_str(raw.get("target"), 200)
        if channel not in CONTACT_CHANNELS:
            raise RotaError(f"Unsupported contact channel: {channel!r}.")
        if not target:
            raise RotaError(f"Contact channel {channel!r} requires a target.")
        key = (channel, target)
        if key in seen:
            continue
        seen.add(key)
        out.append({"channel": channel, "target": target})
    return out


def normalize_schedule(raw: Any) -> dict[str, Any]:
    """Validate and normalize an on-call schedule.

    Supported kinds:
      * ``always``    — always on duty.
      * ``recurring`` — weekly windows: ``weekdays`` (0=Mon..6=Sun), ``start_minute``
        (0..1439 local) and ``end_minute`` (start+1 .. start+1440; values past 1440
        express an overnight window, e.g. 22:00->06:00). ``tz_offset_minutes`` is the
        local UTC offset so a Greek rota reads in Greek wall-clock time.
      * ``window``    — a single explicit ``start``/``end`` instant (UTC ISO).
    """
    if not isinstance(raw, dict):
        raise RotaError("Schedule must be an object.")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in SCHEDULE_KINDS:
        raise RotaError(f"Unsupported schedule kind: {kind!r}.")
    if kind == "always":
        return {"kind": "always"}
    if kind == "recurring":
        weekdays_raw = raw.get("weekdays")
        if not weekdays_raw:
            raise RotaError("A recurring schedule requires at least one weekday.")
        weekdays: set[int] = set()
        for item in weekdays_raw:
            try:
                day = int(item)
            except (TypeError, ValueError) as error:
                raise RotaError("Weekdays must be integers 0..6.") from error
            if not 0 <= day <= 6:
                raise RotaError("Weekdays must be in the range 0..6 (Mon..Sun).")
            weekdays.add(day)
        try:
            start_minute = int(raw.get("start_minute"))
            end_minute = int(raw.get("end_minute"))
        except (TypeError, ValueError) as error:
            raise RotaError("start_minute and end_minute must be integers.") from error
        if not 0 <= start_minute <= MINUTES_PER_DAY - 1:
            raise RotaError("start_minute must be in the range 0..1439.")
        if not start_minute < end_minute <= start_minute + MINUTES_PER_DAY:
            raise RotaError("end_minute must be greater than start_minute and within 24h of it.")
        tz_offset = raw.get("tz_offset_minutes", 0)
        try:
            tz_offset_minutes = int(tz_offset)
        except (TypeError, ValueError) as error:
            raise RotaError("tz_offset_minutes must be an integer.") from error
        if not -14 * 60 <= tz_offset_minutes <= 14 * 60:
            raise RotaError("tz_offset_minutes is out of range.")
        return {
            "kind": "recurring",
            "weekdays": sorted(weekdays),
            "start_minute": start_minute,
            "end_minute": end_minute,
            "tz_offset_minutes": tz_offset_minutes,
        }
    # kind == "window"
    start = _parse_instant(raw.get("start"))
    end = _parse_instant(raw.get("end"))
    if start is None or end is None:
        raise RotaError("A window schedule requires start and end instants.")
    if end <= start:
        raise RotaError("Window end must be after start.")
    return {"kind": "window", "start": start, "end": end}


def _parse_instant(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise RotaError(f"Invalid instant: {value!r}.") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# On-call evaluation (pure)
# --------------------------------------------------------------------------- #
def entry_covers_modality(entry: dict[str, Any], modality: str | None) -> bool:
    """An entry with an empty modality list covers every modality."""
    cover = entry.get("modalities") or []
    if not cover:
        return True
    if not modality:
        return True
    return str(modality).strip().lower() in cover


def schedule_is_active(schedule: dict[str, Any], at: datetime) -> bool:
    """Whether an on-call schedule covers instant ``at`` (UTC-aware)."""
    moment = at if at.tzinfo else at.replace(tzinfo=UTC)
    kind = schedule.get("kind")
    if kind == "always":
        return True
    if kind == "window":
        start = _parse_instant(schedule.get("start"))
        end = _parse_instant(schedule.get("end"))
        if start is None or end is None:
            return False
        return start <= moment < end
    if kind == "recurring":
        tz_offset = int(schedule.get("tz_offset_minutes", 0))
        local = moment + timedelta(minutes=tz_offset)
        local_minute_of_week = (local.weekday() * MINUTES_PER_DAY) + (local.hour * 60) + local.minute
        start_minute = int(schedule["start_minute"])
        end_minute = int(schedule["end_minute"])
        for day in schedule.get("weekdays", []):
            window_start = day * MINUTES_PER_DAY + start_minute
            window_end = day * MINUTES_PER_DAY + end_minute
            if _in_circular_window(local_minute_of_week, window_start, window_end):
                return True
        return False
    return False


def _in_circular_window(point: int, start: int, end: int) -> bool:
    """Membership in [start, end) on a 7-day (10080 min) circular timeline."""
    point %= MINUTES_PER_WEEK
    start %= MINUTES_PER_WEEK
    end_mod = end % MINUTES_PER_WEEK
    if end - start >= MINUTES_PER_WEEK:
        return True
    if start <= end_mod:
        return start <= point < end_mod
    # window wraps past the end of the week
    return point >= start or point < end_mod


def entry_is_on_call(entry: dict[str, Any], at: datetime) -> bool:
    if not entry.get("active", True):
        return False
    schedule = entry.get("schedule")
    if not isinstance(schedule, dict):
        return False
    return schedule_is_active(schedule, at)


def rank_key(entry: dict[str, Any]) -> tuple[int, str]:
    return (int(entry.get("tier", 1)), str(entry.get("expert_name") or "").lower())


def resolve_on_call_entries(
    entries: list[dict[str, Any]],
    *,
    modality: str | None,
    at: datetime,
) -> list[dict[str, Any]]:
    """The de-duplicated, tier-ordered on-call entries for a modality at an instant.

    When one expert appears in several entries (e.g. primary for SSEP and backup
    for MEP) the lowest-tier (most-responsible) entry wins.
    """
    by_expert: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not entry_is_on_call(entry, at):
            continue
        if not entry_covers_modality(entry, modality):
            continue
        expert_id = str(entry.get("expert_id") or "")
        if not expert_id:
            continue
        existing = by_expert.get(expert_id)
        if existing is None or rank_key(entry) < rank_key(existing):
            by_expert[expert_id] = entry
    return sorted(by_expert.values(), key=rank_key)


def build_escalation_plan(
    on_call: list[dict[str, Any]],
    *,
    online_expert_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Group on-call experts into ordered escalation tiers.

    Each tier carries the experts on duty at that level, whether each is currently
    present on-platform (so the system can prefer an in-app ping before paging),
    and the off-platform contact channels to summon them through.
    """
    online = online_expert_ids or set()
    tiers: dict[int, list[dict[str, Any]]] = {}
    for entry in on_call:
        tier = int(entry.get("tier", 1))
        expert_id = str(entry.get("expert_id") or "")
        tiers.setdefault(tier, []).append(
            {
                "expert_id": expert_id,
                "expert_name": entry.get("expert_name"),
                "modalities": entry.get("modalities") or [],
                "on_platform": expert_id in online,
                "contacts": entry.get("contacts") or [],
            }
        )
    plan: list[dict[str, Any]] = []
    for level, tier in enumerate(sorted(tiers), start=1):
        members = sorted(tiers[tier], key=lambda item: str(item["expert_name"] or "").lower())
        plan.append({"level": level, "tier": tier, "experts": members})
    return plan


# --------------------------------------------------------------------------- #
# Document factories (pure)
# --------------------------------------------------------------------------- #
def new_rota_document(
    *,
    tenant_id: str,
    expert_id: str,
    expert_name: str,
    modalities: list[str],
    tier: int,
    schedule: dict[str, Any],
    contacts: list[dict[str, str]],
    active: bool,
    created_by: str | None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "rota_id": uuid4().hex,
        "tenant_id": tenant_id,
        "expert_id": expert_id,
        "expert_name": expert_name,
        "modalities": modalities,
        "tier": tier,
        "schedule": schedule,
        "contacts": contacts,
        "active": active,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }


def _dispatch_intents(experts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Queued off-platform dispatch intents for the experts who are not in-app.

    These are *intents* a notifier worker fulfils; they are recorded as queued so
    the system never claims a delivery it has not actually performed.
    """
    intents: list[dict[str, Any]] = []
    for expert in experts:
        if expert.get("on_platform"):
            continue
        for contact in expert.get("contacts", []):
            intents.append(
                {
                    "expert_id": expert.get("expert_id"),
                    "expert_name": expert.get("expert_name"),
                    "channel": contact.get("channel"),
                    "target": contact.get("target"),
                    "status": "queued",
                }
            )
    return intents


def build_attempt(plan_step: dict[str, Any], *, at: datetime) -> dict[str, Any]:
    """An escalation attempt record for one tier of the plan."""
    experts = plan_step.get("experts", [])
    return {
        "attempt_id": uuid4().hex,
        "level": plan_step.get("level"),
        "tier": plan_step.get("tier"),
        "expert_ids": [expert.get("expert_id") for expert in experts],
        "in_app_expert_ids": [e.get("expert_id") for e in experts if e.get("on_platform")],
        "dispatch": _dispatch_intents(experts),
        "attempted_at": at,
    }


# --------------------------------------------------------------------------- #
# MongoDB IO
# --------------------------------------------------------------------------- #
async def ensure_rota_indexes(database: AsyncIOMotorDatabase) -> None:
    await database[ROTA_COLLECTION].create_index("rota_id", unique=True)
    await database[ROTA_COLLECTION].create_index([("tenant_id", 1), ("tier", 1)])
    await database[ROTA_COLLECTION].create_index([("tenant_id", 1), ("expert_id", 1)])
    await database[SUMMONS_COLLECTION].create_index("summons_id", unique=True)
    await database[SUMMONS_COLLECTION].create_index([("tenant_id", 1), ("status", 1)])
    await database[SUMMONS_COLLECTION].create_index("session_id")


async def create_rota_entry(
    database: AsyncIOMotorDatabase,
    *,
    tenant_id: str,
    expert_id: str,
    expert_name: str,
    modalities: Any,
    tier: int,
    schedule: Any,
    contacts: Any,
    active: bool = True,
    created_by: str | None = None,
) -> dict[str, Any]:
    expert_id = str(expert_id or "").strip()
    expert_name = str(expert_name or "").strip()
    if not expert_id:
        raise RotaError("expert_id is required.")
    if not expert_name:
        raise RotaError("expert_name is required.")
    try:
        tier_value = int(tier)
    except (TypeError, ValueError) as error:
        raise RotaError("tier must be an integer.") from error
    if not 1 <= tier_value <= 9:
        raise RotaError("tier must be in the range 1..9.")
    document = new_rota_document(
        tenant_id=tenant_id or DEFAULT_TENANT,
        expert_id=expert_id,
        expert_name=expert_name,
        modalities=normalize_modalities(modalities),
        tier=tier_value,
        schedule=normalize_schedule(schedule),
        contacts=normalize_contacts(contacts),
        active=bool(active),
        created_by=created_by,
    )
    await database[ROTA_COLLECTION].insert_one(document)
    return public_document(document)


async def list_rota(database: AsyncIOMotorDatabase, *, tenant_id: str = DEFAULT_TENANT) -> list[dict[str, Any]]:
    rows = await database[ROTA_COLLECTION].find({"tenant_id": tenant_id}).to_list(length=500)
    rows.sort(key=rank_key)
    return [public_document(row) for row in rows]


async def remove_rota_entry(database: AsyncIOMotorDatabase, *, rota_id: str) -> bool:
    result = await database[ROTA_COLLECTION].delete_one({"rota_id": rota_id})
    return result.deleted_count > 0


async def resolve_on_call(
    database: AsyncIOMotorDatabase,
    *,
    tenant_id: str = DEFAULT_TENANT,
    modality: str | None = None,
    at: datetime | None = None,
) -> list[dict[str, Any]]:
    moment = at or datetime.now(UTC)
    rows = await database[ROTA_COLLECTION].find({"tenant_id": tenant_id, "active": True}).to_list(length=500)
    resolved = resolve_on_call_entries(rows, modality=modality, at=moment)
    return [public_document(row) for row in resolved]


async def open_summons(
    database: AsyncIOMotorDatabase,
    *,
    tenant_id: str = DEFAULT_TENANT,
    session_id: str,
    modality: str | None,
    requested_by: dict[str, Any] | None = None,
    online_expert_ids: set[str] | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Open an escalation summons for an unanswered support request.

    Resolves the on-call rota, layers it over live presence and records the first
    escalation attempt (the primary tier). Returns the summons ledger document.
    """
    moment = at or datetime.now(UTC)
    rows = await database[ROTA_COLLECTION].find({"tenant_id": tenant_id, "active": True}).to_list(length=500)
    on_call = resolve_on_call_entries(rows, modality=modality, at=moment)
    plan = build_escalation_plan(on_call, online_expert_ids=online_expert_ids)
    document: dict[str, Any] = {
        "summons_id": uuid4().hex,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "modality": (str(modality).strip().lower() if modality else None),
        "requested_by_id": (requested_by or {}).get("user_id"),
        "requested_by_name": (requested_by or {}).get("display_name"),
        "status": "pending" if plan else "exhausted",
        "plan": plan,
        "current_level": 1 if plan else 0,
        "attempts": [build_attempt(plan[0], at=moment)] if plan else [],
        "answered_by_id": None,
        "answered_by_name": None,
        "created_at": moment,
        "updated_at": moment,
    }
    await database[SUMMONS_COLLECTION].insert_one(document)
    return public_document(document)


async def escalate_summons(
    database: AsyncIOMotorDatabase,
    *,
    summons_id: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Advance a pending summons to the next escalation tier.

    When no further tier exists the summons is marked ``exhausted`` so an operator
    can be alerted that the rota has been walked end-to-end without an answer.
    """
    moment = at or datetime.now(UTC)
    summons = await database[SUMMONS_COLLECTION].find_one({"summons_id": summons_id})
    if not summons:
        raise RotaError("Summons not found.")
    if summons.get("status") != "pending":
        raise RotaError(f"Summons is {summons.get('status')!r}; cannot escalate.")
    plan = summons.get("plan") or []
    current_level = int(summons.get("current_level", 1))
    next_level = current_level + 1
    next_step = next((step for step in plan if int(step.get("level", 0)) == next_level), None)
    if next_step is None:
        await database[SUMMONS_COLLECTION].update_one(
            {"summons_id": summons_id},
            {"$set": {"status": "exhausted", "updated_at": moment}},
        )
    else:
        await database[SUMMONS_COLLECTION].update_one(
            {"summons_id": summons_id},
            {
                "$set": {"current_level": next_level, "updated_at": moment},
                "$push": {"attempts": build_attempt(next_step, at=moment)},
            },
        )
    updated = await database[SUMMONS_COLLECTION].find_one({"summons_id": summons_id})
    return public_document(updated)


async def acknowledge_summons(
    database: AsyncIOMotorDatabase,
    *,
    summons_id: str,
    expert: dict[str, Any],
    at: datetime | None = None,
) -> dict[str, Any]:
    moment = at or datetime.now(UTC)
    summons = await database[SUMMONS_COLLECTION].find_one({"summons_id": summons_id})
    if not summons:
        raise RotaError("Summons not found.")
    if summons.get("status") not in {"pending", "exhausted"}:
        raise RotaError(f"Summons is {summons.get('status')!r}; cannot acknowledge.")
    await database[SUMMONS_COLLECTION].update_one(
        {"summons_id": summons_id},
        {
            "$set": {
                "status": "answered",
                "answered_by_id": expert.get("user_id"),
                "answered_by_name": expert.get("display_name"),
                "answered_at": moment,
                "updated_at": moment,
            }
        },
    )
    updated = await database[SUMMONS_COLLECTION].find_one({"summons_id": summons_id})
    return public_document(updated)


async def cancel_summons(
    database: AsyncIOMotorDatabase,
    *,
    summons_id: str,
    at: datetime | None = None,
) -> dict[str, Any]:
    moment = at or datetime.now(UTC)
    summons = await database[SUMMONS_COLLECTION].find_one({"summons_id": summons_id})
    if not summons:
        raise RotaError("Summons not found.")
    if summons.get("status") == "answered":
        raise RotaError("An answered summons cannot be cancelled.")
    await database[SUMMONS_COLLECTION].update_one(
        {"summons_id": summons_id},
        {"$set": {"status": "cancelled", "updated_at": moment}},
    )
    updated = await database[SUMMONS_COLLECTION].find_one({"summons_id": summons_id})
    return public_document(updated)


async def get_summons(database: AsyncIOMotorDatabase, summons_id: str) -> dict[str, Any] | None:
    document = await database[SUMMONS_COLLECTION].find_one({"summons_id": summons_id})
    return public_document(document) if document else None


def public_document(document: dict[str, Any] | None) -> dict[str, Any]:
    if not document:
        return {}
    clean = dict(document)
    clean.pop("_id", None)
    return clean
