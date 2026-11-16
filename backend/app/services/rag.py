from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.core.config import settings
from backend.app.services.tree import to_public_document


class JinaEmbeddingError(RuntimeError):
    pass


class JinaRerankError(RuntimeError):
    pass


class JinaRerankClient:
    """Cross-encoder reranker that re-scores candidate evidence against the query.

    Embeddings find broadly similar passages; the reranker reads the query and
    each candidate together and returns a sharper relevance score. This is what
    keeps the clinical copilot grounded in the most pertinent sources instead of
    the merely vector-close ones.
    """

    def __init__(self) -> None:
        self.api_key = settings.jina_api_key
        self.base_url = settings.jina_api_url.rstrip("/")
        self.model = settings.jina_rerank_model

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and bool(settings.jina_rerank_enabled)

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        if not self.api_key:
            raise JinaRerankError("JINA_API_KEY is not configured.")
        if not documents:
            return []
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": min(top_n, len(documents)),
                },
            )
        if response.status_code >= 400:
            raise JinaRerankError(
                f"Jina rerank request failed with provider status {response.status_code}."
            )
        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list):
            raise JinaRerankError("Jina rerank response did not include results.")
        ranked: list[dict[str, Any]] = []
        for row in results:
            index = row.get("index")
            score = row.get("relevance_score")
            if not isinstance(index, int) or not isinstance(score, (int, float)):
                continue
            ranked.append({"index": index, "relevance_score": float(score)})
        return ranked


class JinaEmbeddingClient:
    def __init__(self) -> None:
        self.api_key = settings.jina_api_key
        self.base_url = settings.jina_api_url.rstrip("/")
        self.model = settings.jina_embeddings_model

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def embed(self, texts: list[str], task: str) -> list[list[float]]:
        if not self.api_key:
            raise JinaEmbeddingError("JINA_API_KEY is not configured.")
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts, "task": task},
            )
        if response.status_code >= 400:
            raise JinaEmbeddingError(f"Jina embeddings request failed with provider status {response.status_code}.")
        payload = response.json()
        rows = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
        embeddings = [row.get("embedding") for row in rows]
        if len(embeddings) != len(texts) or any(not isinstance(item, list) for item in embeddings):
            raise JinaEmbeddingError("Jina embeddings response did not match the requested input.")
        return embeddings


async def ensure_embedding_indexes(database: AsyncIOMotorDatabase) -> None:
    await database.knowledge_embeddings.create_index("chunk_key", unique=True)
    await database.knowledge_embeddings.create_index([("model", 1), ("source_collection", 1)])
    await database.knowledge_embeddings.create_index([("source_collection", 1), ("source_key", 1)])


async def index_knowledge_embeddings(database: AsyncIOMotorDatabase, batch_size: int = 16) -> dict[str, int | str]:
    client = JinaEmbeddingClient()
    if not client.configured:
        raise JinaEmbeddingError("JINA_API_KEY is not configured.")

    await ensure_embedding_indexes(database)
    chunks = await build_knowledge_chunks(database)
    chunk_keys = [chunk["chunk_key"] for chunk in chunks]
    existing_rows = await database.knowledge_embeddings.find(
        {"chunk_key": {"$in": chunk_keys}},
        {"chunk_key": 1, "text_hash": 1, "model": 1},
    ).to_list(length=None)
    existing = {row["chunk_key"]: row for row in existing_rows}
    changed_chunks = [
        chunk
        for chunk in chunks
        if existing.get(chunk["chunk_key"], {}).get("text_hash") != chunk["text_hash"]
        or existing.get(chunk["chunk_key"], {}).get("model") != client.model
    ]

    embedded_count = 0
    now = datetime.now(timezone.utc)
    for start in range(0, len(changed_chunks), batch_size):
        batch = changed_chunks[start : start + batch_size]
        embeddings = await client.embed([chunk["text"] for chunk in batch], task="retrieval.passage")
        for chunk, embedding in zip(batch, embeddings, strict=True):
            document = {
                **chunk,
                "embedding": embedding,
                "embedding_dimensions": len(embedding),
                "model": client.model,
                "indexed_at": now,
            }
            await database.knowledge_embeddings.replace_one({"chunk_key": chunk["chunk_key"]}, document, upsert=True)
            embedded_count += 1

    stale_result = await database.knowledge_embeddings.delete_many(
        {"model": client.model, "chunk_key": {"$nin": chunk_keys}}
    )
    return {
        "model": client.model,
        "total_chunks": len(chunks),
        "embedded_chunks": embedded_count,
        "skipped_chunks": len(chunks) - embedded_count,
        "deleted_stale_chunks": stale_result.deleted_count,
    }


async def retrieve_knowledge(database: AsyncIOMotorDatabase, question: str, top_k: int = 6) -> dict[str, Any]:
    client = JinaEmbeddingClient()
    if not client.configured:
        return {"status": "not_configured", "model": client.model, "items": []}

    documents = await database.knowledge_embeddings.find({"model": client.model}).to_list(length=None)
    if not documents:
        return {"status": "not_indexed", "model": client.model, "items": []}

    query_embedding = (await client.embed([question], task="retrieval.query"))[0]
    scored = []
    for document in documents:
        score = cosine_similarity(query_embedding, document.get("embedding", []))
        if score > 0:
            scored.append((score, document))

    scored.sort(key=lambda item: item[0], reverse=True)

    # Pull a wider candidate set than the final top_k so the cross-encoder
    # reranker has room to reorder. The reranker reads the query and each
    # candidate together, producing a sharper relevance score than cosine alone.
    candidate_pool = max(top_k, settings.jina_rerank_candidates)
    candidates = scored[:candidate_pool]

    reranked = await _apply_rerank(question, candidates)
    if reranked is not None:
        ordered, retrieval_strategy = reranked, "embedding+rerank"
    else:
        ordered, retrieval_strategy = [(score, doc, None) for score, doc in candidates], "embedding"

    items = []
    for cosine_score, document, rerank_score in ordered[:top_k]:
        item = {
            "score": round(cosine_score, 5),
            "chunk_key": document.get("chunk_key"),
            "source_collection": document.get("source_collection"),
            "source_key": document.get("source_key"),
            "title": document.get("title"),
            "text": document.get("text"),
            "source": document.get("source"),
            "metadata": document.get("metadata", {}),
        }
        if rerank_score is not None:
            item["rerank_score"] = round(rerank_score, 5)
            item["relevance"] = round(rerank_score, 5)
        else:
            item["relevance"] = round(cosine_score, 5)
        items.append(item)
    return {
        "status": "ready",
        "model": client.model,
        "retrieval_strategy": retrieval_strategy,
        "rerank_model": settings.jina_rerank_model if retrieval_strategy == "embedding+rerank" else None,
        "items": items,
    }


async def _apply_rerank(
    question: str, candidates: list[tuple[float, dict[str, Any]]]
) -> list[tuple[float, dict[str, Any], float]] | None:
    """Re-score candidates with the Jina cross-encoder.

    Returns a list of (cosine_score, document, rerank_score) sorted by rerank
    score, or None when reranking is unavailable so the caller can fall back to
    the cosine ordering. A provider failure must never break retrieval, so the
    cosine ordering is preserved if the reranker errors.
    """
    rerank_client = JinaRerankClient()
    if not rerank_client.configured or not candidates:
        return None
    documents = [str(document.get("text") or document.get("title") or "") for _, document in candidates]
    try:
        ranked = await rerank_client.rerank(question, documents, top_n=len(documents))
    except JinaRerankError:
        return None
    if not ranked:
        return None
    ordered: list[tuple[float, dict[str, Any], float]] = []
    for row in ranked:
        index = row["index"]
        if 0 <= index < len(candidates):
            cosine_score, document = candidates[index]
            ordered.append((cosine_score, document, row["relevance_score"]))
    return ordered or None


def ground_citations(
    evidence_used: list[dict[str, Any]], retrieved_items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Verify that each citation the model produced maps to a retrieved source.

    This is a deterministic provenance check, not a heuristic re-interpretation:
    a citation is grounded only when its title matches a passage that was
    actually retrieved. Ungrounded citations are flagged so the UI can warn the
    clinician that the model referenced something outside the evidence set.
    """
    catalog = []
    for item in retrieved_items:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        catalog.append((normalize_title(title), item))

    grounded: list[dict[str, Any]] = []
    ungrounded: list[dict[str, Any]] = []
    for entry in evidence_used:
        title = str(entry.get("title") or "").strip()
        match = _match_citation(title, catalog)
        record = dict(entry)
        if match is not None:
            record["grounded"] = True
            record["source_collection"] = match.get("source_collection")
            record["source_key"] = match.get("source_key")
            record["source"] = match.get("source")
            record["relevance"] = match.get("relevance")
            grounded.append(record)
        else:
            record["grounded"] = False
            ungrounded.append(record)

    citations = grounded + ungrounded
    return {
        "citations": citations,
        "grounded_count": len(grounded),
        "ungrounded_count": len(ungrounded),
        "fully_grounded": bool(citations) and not ungrounded,
    }


def _match_citation(title: str, catalog: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    if not title or not catalog:
        return None
    normalized = normalize_title(title)
    if not normalized:
        return None
    normalized_tokens = set(normalized.split())
    best_item: dict[str, Any] | None = None
    best_overlap = 0.0
    for candidate_norm, item in catalog:
        if normalized == candidate_norm or normalized in candidate_norm or candidate_norm in normalized:
            return item
        candidate_tokens = set(candidate_norm.split())
        if not candidate_tokens or not normalized_tokens:
            continue
        overlap = len(normalized_tokens & candidate_tokens) / len(normalized_tokens | candidate_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_item = item
    if best_overlap >= 0.6:
        return best_item
    return None


def normalize_title(value: str) -> str:
    lowered = value.casefold().strip()
    cleaned = "".join(character if character.isalnum() or character.isspace() else " " for character in lowered)
    return " ".join(cleaned.split())


async def build_knowledge_chunks(database: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    await _add_collection_chunks(chunks, database, "knowledge_nodes", "key", build_node_text)
    await _add_collection_chunks(chunks, database, "muscles", "lat_name", build_muscle_text)
    await _add_collection_chunks(chunks, database, "protocols", "key", build_protocol_text)
    await _add_collection_chunks(chunks, database, "ssep_protocols", "key", build_generic_text)
    await _add_collection_chunks(chunks, database, "pedicle_roots", "key", build_generic_text)
    await _add_collection_chunks(chunks, database, "nerve_roots", "key", build_generic_text)
    await _add_collection_chunks(chunks, database, "cranial_nerves", "key", build_generic_text)
    await _add_collection_chunks(chunks, database, "spinal_discs", "key", build_generic_text)
    await _add_collection_chunks(chunks, database, "electrode_coordinates", "lat_name", build_coordinate_text)
    await _add_collection_chunks(chunks, database, "admin_knowledge_chunks", "chunk_id", build_admin_knowledge_text)
    return chunks


async def _add_collection_chunks(
    chunks: list[dict[str, Any]],
    database: AsyncIOMotorDatabase,
    collection_name: str,
    key_field: str,
    text_builder: Any,
) -> None:
    rows = await database[collection_name].find({}).to_list(length=None)
    for row in rows:
        document = to_public_document(row)
        source_key = str(document.get(key_field, ""))
        if not source_key:
            continue
        title = chunk_title(document, source_key)
        text = text_builder(document)
        if not text.strip():
            continue
        chunks.append(make_chunk(collection_name, source_key, title, text, document))


def make_chunk(
    collection_name: str,
    source_key: str,
    title: str,
    text: str,
    document: dict[str, Any],
) -> dict[str, Any]:
    chunk_key = f"{collection_name}:{source_key}"
    return {
        "chunk_key": chunk_key,
        "source_collection": collection_name,
        "source_key": source_key,
        "title": title,
        "text": text,
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source": document.get("source") or first_value(document.get("source_ids")),
        "metadata": {
            "domain": document.get("domain") or document.get("region"),
            "node_type": document.get("node_type"),
            "status": document.get("status"),
        },
    }


def build_node_text(document: dict[str, Any]) -> str:
    return join_lines(
        "Knowledge node",
        field("Title", document.get("title_el")),
        field("English title", document.get("title_en")),
        field("Type", document.get("node_type")),
        field("Domain", document.get("domain")),
        field("Summary", document.get("summary_el")),
        field("Linked roots", document.get("linked_roots")),
        field("Linked cranial nerves", document.get("linked_cns")),
        field("Linked muscles", document.get("linked_muscles")),
        field("Linked protocols", document.get("linked_protocols")),
    )


def build_muscle_text(document: dict[str, Any]) -> str:
    return join_lines(
        "Muscle / electrode channel",
        field("Greek name", document.get("name_el")),
        field("Latin name", document.get("lat_name")),
        field("Domains", document.get("domains")),
        field("Electrode placement", document.get("placement") or document.get("placements")),
        field("Functional check", document.get("action") or document.get("actions")),
        field("Source", document.get("source")),
    )


def build_protocol_text(document: dict[str, Any]) -> str:
    return join_lines(
        "Protocol",
        field("Title", document.get("title_el")),
        field("Domain", document.get("domain")),
        field("Content", document.get("content")),
        field("Source", document.get("source")),
    )


def build_coordinate_text(document: dict[str, Any]) -> str:
    coordinates = [
        f"{pin.get('view')} {pin.get('side')} x={pin.get('x')} y={pin.get('y')}"
        for pin in document.get("coordinates", [])
    ]
    return join_lines(
        "Body-map coordinate",
        field("Muscle", document.get("lat_name")),
        field("Coordinates", coordinates),
        field("Image", document.get("image")),
        field("Coordinate system", document.get("coordinate_system")),
        field("Source", document.get("source")),
    )


def build_admin_knowledge_text(document: dict[str, Any]) -> str:
    return join_lines(
        "Admin knowledge-base document chunk",
        field("Title", document.get("title")),
        field("Domain", document.get("domain")),
        field("Tags", document.get("tags")),
        field("Chunk", document.get("chunk_index")),
        field("Content", document.get("text")),
        field("Source", document.get("source")),
    )


def build_generic_text(document: dict[str, Any]) -> str:
    rows = []
    for key, value in document.items():
        if key in {"_id", "embedding"}:
            continue
        rows.append(field(key, value))
    return join_lines(*rows)


def chunk_title(document: dict[str, Any], source_key: str) -> str:
    for field_name in ("title_el", "name_el", "lat_name", "title", "name", "disc", "root", "level", "key"):
        value = document.get(field_name)
        if value:
            return str(value)
    return source_key


def field(label: str, value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    return f"{label}: {stringify(value)}"


def stringify(value: Any) -> str:
    if isinstance(value, list):
        return " · ".join(stringify(item) for item in value if item is not None)
    if isinstance(value, dict):
        return "; ".join(f"{key}={stringify(item)}" for key, item in value.items() if item is not None)
    return str(value)


def join_lines(*rows: str) -> str:
    return "\n".join(row for row in rows if row)


def first_value(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(left_value * left_value for left_value in left))
    right_norm = math.sqrt(sum(right_value * right_value for right_value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot_product / (left_norm * right_norm)