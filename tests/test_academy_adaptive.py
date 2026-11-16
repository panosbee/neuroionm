"""Adaptive, case-driven Academy path ranking.

The path is computed only from the learner's own recorded progress: lessons with
logged mistakes are surfaced first for remediation, then partially-completed
lessons, then the next unstarted lesson in module order. A tiny async Mongo
stand-in drives the real service code.
"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.app.services.academy_progress import build_adaptive_path


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, *_args: Any, **_kwargs: Any) -> "_Cursor":
        return self

    async def to_list(self, length: Any = None) -> list[dict[str, Any]]:
        return list(self._docs)


class _Collection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def find(self, *_args: Any, **_kwargs: Any) -> _Cursor:
        return _Cursor(self._docs)


class _DB:
    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.knowledge_nodes = _Collection(nodes)


def _nodes() -> list[dict[str, Any]]:
    return [
        {"key": "academy.folder", "node_type": "folder", "context": "academy", "status": "active", "sort_order": 1},
        {"key": "academy.spine.l4", "node_type": "disc_level", "domain": "spine", "context": "academy",
         "status": "active", "sort_order": 2, "linked_roots": ["L4"], "title_el": "L4"},
        {"key": "academy.cranial.cn7", "node_type": "cn", "domain": "cranial", "context": "academy",
         "status": "active", "sort_order": 3, "linked_cns": ["VII"], "title_el": "CN VII"},
        {"key": "academy.ssep.median", "node_type": "ssep_nerve", "domain": "ssep", "context": "academy",
         "status": "active", "sort_order": 4, "metadata": {"ssep_montage": {"channels": []}}, "title_el": "Median"},
    ]


def test_adaptive_path_prioritizes_remediation_then_continue_then_start():
    progress_items = [
        {
            "node_key": "academy.ssep.median",
            "node_title": "Median SSEP",
            "domain": "ssep",
            "lesson_status": "in_progress",
            "completion_percent": 40,
            "mistakes": [{"type": "waveform"}, {"type": "case_scenario"}],
        },
        {
            "node_key": "academy.spine.l4",
            "node_title": "L4 lesson",
            "domain": "spine",
            "lesson_status": "in_progress",
            "completion_percent": 55,
            "mistakes": [],
            "recommended_review": ["Electrode placement lab"],
        },
    ]
    path = asyncio.run(build_adaptive_path(_DB(_nodes()), progress_items))

    # Folder node is excluded; three lesson nodes remain.
    keys = [item["node_key"] for item in path]
    assert "academy.folder" not in keys

    assert path[0]["node_key"] == "academy.ssep.median"
    assert path[0]["priority"] == "remediate"
    assert set(path[0]["focus_areas"]) == {"waveform", "case_scenario"}
    assert path[0]["rank"] == 1

    assert path[1]["node_key"] == "academy.spine.l4"
    assert path[1]["priority"] == "continue"

    # The untouched CN VII lesson is a fresh start, ranked last.
    assert path[2]["node_key"] == "academy.cranial.cn7"
    assert path[2]["priority"] == "start"


def test_adaptive_path_excludes_completed_lessons():
    progress_items = [
        {
            "node_key": "academy.spine.l4",
            "lesson_status": "completed",
            "completion_percent": 100,
            "mistakes": [],
        }
    ]
    path = asyncio.run(build_adaptive_path(_DB(_nodes()), progress_items))
    keys = [item["node_key"] for item in path]
    assert "academy.spine.l4" not in keys
    # Remaining lessons are unstarted starts.
    assert all(item["priority"] == "start" for item in path)
