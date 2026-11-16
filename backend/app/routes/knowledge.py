from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.db.mongo import get_database
from backend.app.services.planner import KnowledgeNodeNotFound, build_planner
from backend.app.services.tree import build_tree, to_public_document


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/tree")
async def get_tree(database: AsyncIOMotorDatabase = Depends(get_database)) -> list[dict[str, Any]]:
    nodes = await database.knowledge_nodes.find({"status": "active"}).to_list(length=None)
    return build_tree(nodes)


@router.get("/nodes/{node_key:path}")
async def get_node(node_key: str, database: AsyncIOMotorDatabase = Depends(get_database)) -> dict[str, Any]:
    node = await database.knowledge_nodes.find_one({"key": node_key})
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
    return to_public_document(node)


@router.get("/planner")
async def get_planner(
    node_key: str = Query(min_length=1),
    database: AsyncIOMotorDatabase = Depends(get_database),
) -> dict[str, Any]:
    try:
        return await build_planner(database, node_key)
    except KnowledgeNodeNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge node not found")
