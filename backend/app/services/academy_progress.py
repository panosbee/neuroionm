from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.services.tree import to_public_document


LEARNER_COOKIE_NAME = "ionm_learner_id"


async def ensure_academy_progress_indexes(database: AsyncIOMotorDatabase) -> None:
    await database.academy_progress.create_index([("learner_id", 1), ("node_key", 1)], unique=True)
    await database.academy_progress.create_index([("learner_id", 1), ("updated_at", -1)])
    await database.academy_attempts.create_index([("learner_id", 1), ("node_key", 1), ("created_at", -1)])


async def list_learner_profiles(database: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    await ensure_academy_progress_indexes(database)
    users = await database.admin_users.find({"status": {"$in": ["active", "pending"]}}).sort("full_name", 1).to_list(length=300)
    return [learner_summary(user) for user in users]


def learner_summary(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "learner_id": user.get("user_id"),
        "username": user.get("username"),
        "full_name": user.get("full_name") or " ".join([user.get("first_name", ""), user.get("last_name", "")]).strip(),
        "email": user.get("email"),
        "access_level": user.get("access_level"),
        "status": user.get("status"),
        "organization": user.get("organization"),
        "specialty": user.get("specialty"),
    }


async def get_learner_profile(database: AsyncIOMotorDatabase, learner_id: str | None) -> dict[str, Any] | None:
    if not learner_id:
        return None
    user = await database.admin_users.find_one({"user_id": learner_id, "status": {"$in": ["active", "pending"]}})
    return learner_summary(user) if user else None


async def require_learner(database: AsyncIOMotorDatabase, learner_id: str | None) -> dict[str, Any]:
    learner = await get_learner_profile(database, learner_id)
    if not learner:
        raise ValueError("Select an Academy learner before saving progress.")
    return learner


async def get_or_create_progress(
    database: AsyncIOMotorDatabase,
    learner_id: str,
    node_key: str,
    lesson: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await ensure_academy_progress_indexes(database)
    progress = await database.academy_progress.find_one({"learner_id": learner_id, "node_key": node_key})
    if progress:
        return to_public_document(progress)
    now = datetime.now(UTC)
    document = {
        "progress_id": uuid.uuid4().hex,
        "learner_id": learner_id,
        "node_key": node_key,
        "node_title": lesson_node_title(lesson),
        "domain": lesson_domain(lesson),
        "checklist": {},
        "lesson_status": "not_started",
        "completion_percent": 0,
        "scores": {},
        "mistakes": [],
        "recommended_review": [],
        "created_at": now,
        "updated_at": now,
    }
    await database.academy_progress.insert_one(document)
    return to_public_document(document)


async def update_progress(
    database: AsyncIOMotorDatabase,
    learner_id: str,
    node_key: str,
    patch: dict[str, Any],
    lesson: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = await get_or_create_progress(database, learner_id, node_key, lesson)
    merged = merge_progress(current, patch, lesson)
    merged["updated_at"] = datetime.now(UTC)
    await database.academy_progress.replace_one({"learner_id": learner_id, "node_key": node_key}, merged, upsert=True)
    return to_public_document(merged)


async def record_attempt(
    database: AsyncIOMotorDatabase,
    learner_id: str,
    node_key: str,
    attempt_type: str,
    payload: dict[str, Any],
) -> None:
    await ensure_academy_progress_indexes(database)
    await database.academy_attempts.insert_one(
        {
            "attempt_id": uuid.uuid4().hex,
            "learner_id": learner_id,
            "node_key": node_key,
            "attempt_type": attempt_type,
            "payload": payload,
            "created_at": datetime.now(UTC),
        }
    )


def merge_progress(current: dict[str, Any], patch: dict[str, Any], lesson: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(current)
    if lesson:
        merged["node_title"] = lesson_node_title(lesson)
        merged["domain"] = lesson_domain(lesson)
    if "checklist" in patch:
        merged["checklist"] = {**(merged.get("checklist") or {}), **(patch.get("checklist") or {})}
    for key in [
        "placement_validation",
        "placement_passed",
        "placement_accuracy",
        "waveform_reviewed",
        "waveform_scenario",
        "waveform_parameters",
        "waveform_quiz_grade",
        "checkpoint_grade",
        "checkpoint_score",
        "case_grades",
        "last_case_grade",
    ]:
        if key in patch:
            merged[key] = patch[key]
    summary = summarize_progress(merged, lesson)
    merged.update(summary)
    return merged


def summarize_progress(progress: dict[str, Any], lesson: dict[str, Any] | None) -> dict[str, Any]:
    checklist_total = len((lesson or {}).get("checklist", []))
    checklist_done = sum(1 for value in (progress.get("checklist") or {}).values() if value)
    checkpoint_grade = progress.get("checkpoint_grade") or {}
    waveform_quiz_grade = progress.get("waveform_quiz_grade") or {}
    case_grades = progress.get("case_grades") or {}
    best_case = max([grade.get("score_percent", 0) for grade in case_grades.values()] or [0])
    components = []
    if checklist_total:
        components.append(checklist_done / checklist_total)
    components.append(1 if progress.get("placement_passed") else 0)
    components.append(1 if progress.get("waveform_reviewed") else 0)
    components.append((checkpoint_grade.get("score_percent") or progress.get("checkpoint_score") or 0) / 100)
    components.append((waveform_quiz_grade.get("score_percent") or 0) / 100)
    components.append(best_case / 100)
    completion_percent = round(sum(components) / max(len(components), 1) * 100)
    scores = {
        "checkpoint": checkpoint_grade.get("score_percent") or progress.get("checkpoint_score"),
        "waveform_quiz": waveform_quiz_grade.get("score_percent"),
        "best_case": best_case if case_grades else None,
        "placement_accuracy": progress.get("placement_accuracy"),
    }
    mistakes = build_mistakes(progress)
    recommended_review = build_recommended_review(progress, mistakes)
    lesson_status = "completed" if completion_percent >= 85 and not mistakes else "in_progress" if completion_percent else "not_started"
    return {
        "completion_percent": completion_percent,
        "lesson_status": lesson_status,
        "scores": scores,
        "mistakes": mistakes,
        "recommended_review": recommended_review,
    }


def build_mistakes(progress: dict[str, Any]) -> list[dict[str, Any]]:
    mistakes: list[dict[str, Any]] = []
    placement = progress.get("placement_validation") or {}
    for result in placement.get("results", []):
        if result.get("status") not in {"correct", None}:
            mistakes.append(
                {
                    "type": "placement",
                    "label": result.get("target", {}).get("name_el") or result.get("target_id"),
                    "detail": result.get("feedback") or "Placement needs correction.",
                    "severity": "high" if result.get("status") == "off" else "medium",
                }
            )
    for grade_key, mistake_type in [("checkpoint_grade", "checkpoint"), ("waveform_quiz_grade", "waveform")]:
        grade = progress.get(grade_key) or {}
        for result in grade.get("results", []):
            if not result.get("passed", True):
                mistakes.append(
                    {
                        "type": mistake_type,
                        "label": result.get("checkpoint_id") or result.get("question_id"),
                        "detail": ", ".join(result.get("missing_values") or result.get("extra_values") or []) or "Answer needs review.",
                        "severity": "medium",
                    }
                )
    for grade in (progress.get("case_grades") or {}).values():
        if not grade.get("passed", True):
            mistakes.append(
                {
                    "type": "case_scenario",
                    "label": grade.get("title") or grade.get("scenario_id"),
                    "detail": ", ".join(grade.get("missing_actions") or grade.get("extra_actions") or []) or "Case action selection needs review.",
                    "severity": "high" if grade.get("score_percent", 0) < 60 else "medium",
                }
            )
    return mistakes[:24]


def build_recommended_review(progress: dict[str, Any], mistakes: list[dict[str, Any]]) -> list[str]:
    review = []
    if not progress.get("placement_passed"):
        review.append("Electrode placement lab")
    if not progress.get("waveform_reviewed"):
        review.append("Waveform scenario review")
    for mistake in mistakes:
        if mistake["type"] == "checkpoint":
            review.append("Checkpoint anatomy/protocol questions")
        elif mistake["type"] == "waveform":
            review.append("Waveform quiz and artifact recognition")
        elif mistake["type"] == "case_scenario":
            review.append("Scored case scenario reasoning")
        elif mistake["type"] == "placement":
            review.append(f"Placement target: {mistake['label']}")
    deduped = []
    for item in review:
        if item not in deduped:
            deduped.append(item)
    return deduped[:12]


async def learner_report(database: AsyncIOMotorDatabase, learner_id: str) -> dict[str, Any]:
    learner = await get_learner_profile(database, learner_id)
    progress_docs = await database.academy_progress.find({"learner_id": learner_id}).sort("updated_at", -1).to_list(length=500)
    progress_items = [to_public_document(item) for item in progress_docs]
    completed = [item for item in progress_items if item.get("lesson_status") == "completed"]
    needs_review = [item for item in progress_items if item.get("mistakes") or item.get("completion_percent", 0) < 85]
    score_values = [score for item in progress_items for score in (item.get("scores") or {}).values() if isinstance(score, (int, float))]
    return {
        "learner": learner,
        "summary": {
            "lessons_started": len(progress_items),
            "lessons_completed": len(completed),
            "average_score": round(sum(score_values) / len(score_values)) if score_values else 0,
            "needs_review_count": len(needs_review),
        },
        "passed": lesson_brief_list(completed[:12]),
        "needs_review": lesson_brief_list(needs_review[:12]),
        "mistakes": [mistake for item in progress_items for mistake in item.get("mistakes", [])][:30],
        "recommended_review": dedupe([review for item in progress_items for review in item.get("recommended_review", [])])[:20],
        "module_completion": await module_completion(database, progress_items),
        "adaptive_path": await build_adaptive_path(database, progress_items),
        "updated_at": datetime.now(UTC),
    }


# Priority bands for the adaptive learning path. Lower number = sooner. The path
# is computed deterministically from the learner's own recorded attempts/scores,
# not from a heuristic guess: remediation of a logged mistake always precedes a
# partially-finished lesson, which precedes a brand-new lesson.
ADAPTIVE_PRIORITY = {"remediate": 0, "continue": 1, "start": 2}


async def build_adaptive_path(
    database: AsyncIOMotorDatabase, progress_items: list[dict[str, Any]], limit: int = 6
) -> list[dict[str, Any]]:
    """Rank the learner's next lessons from their recorded performance.

    This turns the static curriculum into an adaptive, case-driven path: lessons
    where the learner logged placement/quiz/case mistakes are surfaced first for
    remediation, then partially-completed lessons, then the next unstarted lesson
    in module order. Every input is a real recorded score — nothing is invented.
    """
    nodes = (
        await database.knowledge_nodes.find({"context": "academy", "status": "active"})
        .sort([("sort_order", 1), ("key", 1)])
        .to_list(length=None)
    )
    progress_lookup = {item.get("node_key"): item for item in progress_items}

    candidates: list[dict[str, Any]] = []
    for node in nodes:
        if not _is_lesson_node(node):
            continue
        node_key = node.get("key")
        progress = progress_lookup.get(node_key)
        recommendation = _adaptive_recommendation(node, progress)
        if recommendation:
            candidates.append(recommendation)

    candidates.sort(
        key=lambda item: (
            ADAPTIVE_PRIORITY.get(item["priority"], 9),
            item.get("completion_percent", 0),
            item.get("sort_order", 0),
        )
    )
    for index, candidate in enumerate(candidates[:limit], start=1):
        candidate["rank"] = index
    return candidates[:limit]


def _is_lesson_node(node: dict[str, Any]) -> bool:
    if node.get("node_type") in {"folder", "domain"}:
        return False
    metadata = node.get("metadata") or {}
    return bool(
        node.get("linked_muscles")
        or node.get("linked_cns")
        or node.get("linked_roots")
        or metadata.get("ssep_montage")
    )


def _adaptive_recommendation(node: dict[str, Any], progress: dict[str, Any] | None) -> dict[str, Any] | None:
    node_key = node.get("key")
    title = node.get("title_el") or node.get("title_en") or node_key
    domain = node.get("domain")
    sort_order = node.get("sort_order", 0)

    if progress is None:
        return {
            "node_key": node_key,
            "title": title,
            "domain": domain,
            "priority": "start",
            "reason": "not_started",
            "focus_areas": [],
            "completion_percent": 0,
            "sort_order": sort_order,
        }

    mistakes = progress.get("mistakes") or []
    completion = progress.get("completion_percent", 0)
    if mistakes:
        focus = dedupe([mistake.get("type") for mistake in mistakes if mistake.get("type")])
        return {
            "node_key": node_key,
            "title": progress.get("node_title") or title,
            "domain": progress.get("domain") or domain,
            "priority": "remediate",
            "reason": "logged_mistakes",
            "focus_areas": focus,
            "completion_percent": completion,
            "sort_order": sort_order,
        }
    if progress.get("lesson_status") != "completed" and completion < 85:
        return {
            "node_key": node_key,
            "title": progress.get("node_title") or title,
            "domain": progress.get("domain") or domain,
            "priority": "continue",
            "reason": "in_progress",
            "focus_areas": dedupe(progress.get("recommended_review") or []),
            "completion_percent": completion,
            "sort_order": sort_order,
        }
    return None


def lesson_brief_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "node_key": item.get("node_key"),
            "title": item.get("node_title") or item.get("node_key"),
            "domain": item.get("domain"),
            "completion_percent": item.get("completion_percent", 0),
            "lesson_status": item.get("lesson_status"),
            "scores": item.get("scores", {}),
        }
        for item in items
    ]


async def module_completion(database: AsyncIOMotorDatabase, progress_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modules = await database.academy_modules.find({}).sort("sort_order", 1).to_list(length=None)
    nodes = await database.knowledge_nodes.find({"context": "academy", "status": "active"}).sort("sort_order", 1).to_list(length=None)
    completed_keys = {item.get("node_key") for item in progress_items if item.get("lesson_status") == "completed"}
    output = []
    for module in modules:
        module_key = module.get("module_key")
        domain_hint = academy_domain_hint(module_key)
        module_nodes = [node for node in nodes if node_matches_module(node, module_key, domain_hint)]
        total = len(module_nodes)
        done = sum(1 for node in module_nodes if node.get("key") in completed_keys)
        output.append(
            {
                "module_key": module_key,
                "title": module.get("title"),
                "completed": done,
                "total": total,
                "completion_percent": round(done / total * 100) if total else 0,
            }
        )
    return output


def lesson_node_title(lesson: dict[str, Any] | None) -> str | None:
    node = (lesson or {}).get("node") or {}
    return node.get("title_el") or node.get("title_en") or node.get("key")


def lesson_domain(lesson: dict[str, Any] | None) -> str | None:
    return ((lesson or {}).get("node") or {}).get("domain")


def dedupe(items: list[str]) -> list[str]:
    output = []
    for item in items:
        if item and item not in output:
            output.append(item)
    return output


def academy_domain_hint(module_key: str | None) -> str | None:
    if not module_key:
        return None
    mapping = {
        "academy.spine": "spine",
        "academy.cranial": "cranial",
        "academy.thyroid": "thyroid",
        "academy.ssep": "ssep",
        "academy.pedicle": "pedicle",
    }
    return mapping.get(module_key)


def node_matches_module(node: dict[str, Any], module_key: str | None, domain_hint: str | None) -> bool:
    if module_key == "academy.foundations":
        return node.get("node_type") in {"mode", "domain", "folder"}
    if module_key == "academy.scenarios":
        return bool(node.get("linked_muscles") or node.get("linked_protocols"))
    if domain_hint:
        return node.get("domain") == domain_hint
    return False
