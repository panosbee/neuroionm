from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    parent_key: str | None = None
    title_el: str
    title_en: str | None = None
    node_type: str
    domain: str
    context: list[str] = Field(default_factory=list)
    linked_roots: list[str] = Field(default_factory=list)
    linked_muscles: list[str] = Field(default_factory=list)
    linked_cns: list[str] = Field(default_factory=list)
    linked_protocols: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    sort_order: int = 0
    status: Literal["active", "draft", "retired"] = "active"
    summary_el: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    mode: Literal["live", "academy"] = "live"
    procedure_key: str | None = None
    selected_node_keys: list[str] = Field(default_factory=list)
    non_identifying_ref: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)


class CaseEventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=4000)
    node_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def new_case_document(payload: CaseCreate) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "case_id": str(uuid4()),
        "title": payload.title.strip(),
        "mode": payload.mode,
        "procedure_key": payload.procedure_key,
        "selected_node_keys": payload.selected_node_keys,
        "non_identifying_ref": payload.non_identifying_ref,
        "notes": payload.notes,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }


def new_case_event_document(case_id: str, payload: CaseEventCreate) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "event_id": str(uuid4()),
        "case_id": case_id,
        "event_type": payload.event_type,
        "title": payload.title.strip(),
        "description": payload.description,
        "node_key": payload.node_key,
        "metadata": payload.metadata,
        "created_at": now,
    }


def new_case_debrief_document(
    case_id: str,
    *,
    focus: str,
    node_key: str | None,
    model: str,
    debrief: dict[str, Any],
    retrieval: dict[str, Any],
    raw_usage: dict[str, Any] | None,
    timeline_event_count: int,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "debrief_id": str(uuid4()),
        "case_id": case_id,
        "focus": focus,
        "node_key": node_key,
        "model": model,
        "debrief": debrief,
        "retrieval": retrieval,
        "raw_usage": raw_usage,
        "timeline_event_count": timeline_event_count,
        "created_at": now,
    }
