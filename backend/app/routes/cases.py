from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.db.mongo import get_database
from backend.app.models.domain import CaseCreate, CaseEventCreate, new_case_document, new_case_event_document
from backend.app.services.tree import to_public_document


router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.post("")
async def create_case(payload: CaseCreate, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    document = new_case_document(payload)
    await database.cases.insert_one(document)
    return to_public_document(document)


@router.get("")
async def list_cases(database: AsyncIOMotorDatabase = Depends(get_database)) -> list[dict[str, Any]]:
    cases = await database.cases.find({}).sort("created_at", -1).to_list(length=100)
    enriched: list[dict[str, Any]] = []
    for item in cases:
        case_id = item["case_id"]
        clean = to_public_document(item)
        clean["event_count"] = await database.case_events.count_documents({"case_id": case_id})
        clean["debrief_count"] = await database.case_debriefs.count_documents({"case_id": case_id})
        latest_debrief = await database.case_debriefs.find_one({"case_id": case_id}, sort=[("created_at", -1)])
        clean["latest_debrief"] = to_public_document(latest_debrief) if latest_debrief else None
        enriched.append(clean)
    return enriched


@router.get("/{case_id}")
async def get_case(case_id: str, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    case = await database.cases.find_one({"case_id": case_id})
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    events = await database.case_events.find({"case_id": case_id}).sort("created_at", 1).to_list(length=None)
    debriefs = await database.case_debriefs.find({"case_id": case_id}).sort("created_at", -1).to_list(length=10)
    clean = to_public_document(case)
    clean["events"] = [to_public_document(event) for event in events]
    clean["debriefs"] = [to_public_document(debrief) for debrief in debriefs]
    return clean


@router.get("/{case_id}/debriefs")
async def list_case_debriefs(case_id: str, database: AsyncIOMotorDatabase = Depends(get_database)) -> list[dict[str, Any]]:
    case = await database.cases.find_one({"case_id": case_id})
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    debriefs = await database.case_debriefs.find({"case_id": case_id}).sort("created_at", -1).to_list(length=50)
    return [to_public_document(item) for item in debriefs]


@router.post("/{case_id}/events")
async def create_case_event(
    case_id: str,
    payload: CaseEventCreate,
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    case = await database.cases.find_one({"case_id": case_id})
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    document = new_case_event_document(case_id, payload)
    await database.case_events.insert_one(document)
    await database.cases.update_one({"case_id": case_id}, {"$set": {"updated_at": document["created_at"]}})
    return to_public_document(document)
