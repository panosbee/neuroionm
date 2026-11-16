from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.core.config import settings


CACHE_MAX_AGE = timedelta(days=14)


async def get_pubmed_bibliography(
    database: AsyncIOMotorDatabase,
    planner: dict[str, Any],
    limit: int = 8,
) -> dict[str, Any]:
    query = build_pubmed_query(planner)
    cache_key = f"pubmed::{query}::{limit}"
    cached = await database.bibliography_cache.find_one({"cache_key": cache_key})
    if cached and is_cache_fresh(cached):
        return {**cached["payload"], "cache": "hit"}

    try:
        payload = await fetch_pubmed(query, limit)
    except httpx.HTTPStatusError as error:
        status_code = error.response.status_code
        return {
            "status": "unavailable",
            "query": query,
            "cache": "miss",
            "items": [],
            "error": f"PubMed request failed with HTTP {status_code}.",
        }
    except httpx.HTTPError:
        return {
            "status": "unavailable",
            "query": query,
            "cache": "miss",
            "items": [],
            "error": "PubMed request failed.",
        }

    await database.bibliography_cache.update_one(
        {"cache_key": cache_key},
        {"$set": {"cache_key": cache_key, "fetched_at": datetime.now(UTC), "payload": payload}},
        upsert=True,
    )
    return {**payload, "cache": "miss"}


async def fetch_pubmed(query: str, limit: int) -> dict[str, Any]:
    base_url = settings.pubmed_api_url.rstrip("/")
    async with httpx.AsyncClient(timeout=12) as client:
        search = await client.get(f"{base_url}/esearch.fcgi", params={**pubmed_params(), "db": "pubmed", "retmode": "json", "retmax": limit, "sort": "relevance", "term": query})
        search.raise_for_status()
        search_data = search.json()
        ids = search_data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return {"status": "no_results", "query": query, "items": [], "count": 0}

        summary = await client.get(f"{base_url}/esummary.fcgi", params={**pubmed_params(), "db": "pubmed", "retmode": "json", "id": ",".join(ids)})
        summary.raise_for_status()
        summary_data = summary.json().get("result", {})
        items = [pubmed_summary_item(summary_data[item_id]) for item_id in ids if item_id in summary_data]
        return {"status": "ok", "query": query, "items": items, "count": len(items)}


def pubmed_params() -> dict[str, str]:
    params = {"tool": settings.pubmed_tool}
    if settings.pubmed_api_key:
        params["api_key"] = settings.pubmed_api_key
    if settings.pubmed_email:
        params["email"] = settings.pubmed_email
    return params


def pubmed_summary_item(item: dict[str, Any]) -> dict[str, Any]:
    authors = [author.get("name") for author in item.get("authors", []) if author.get("name")]
    return {
        "pmid": item.get("uid"),
        "title": item.get("title"),
        "journal": item.get("fulljournalname") or item.get("source"),
        "pubdate": item.get("pubdate"),
        "authors": authors[:6],
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{item.get('uid')}/" if item.get("uid") else None,
    }


def build_pubmed_query(planner: dict[str, Any]) -> str:
    node = planner["node"]
    domain = node.get("domain")
    key = node.get("key", "")

    if domain == "ssep" or "ssep" in key:
        return "somatosensory evoked potentials intraoperative neuromonitoring"
    elif domain == "pedicle" or "pedicle" in key:
        return "pedicle screw stimulation triggered EMG threshold intraoperative neuromonitoring"
    elif domain == "thyroid":
        return "recurrent laryngeal nerve monitoring thyroidectomy electromyography"
    elif domain == "cranial":
        return "cranial nerve intraoperative monitoring electromyography"
    return "intraoperative neuromonitoring motor evoked potentials spine"


def is_cache_fresh(cached: dict[str, Any]) -> bool:
    fetched_at = cached.get("fetched_at")
    if not isinstance(fetched_at, datetime):
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - fetched_at <= CACHE_MAX_AGE
