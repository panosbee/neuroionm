"""Tests for the store-and-forward asynchronous expert review (item #5).

A small spec-compliant EDF is synthesised (via the shared `build_edf` helper) so
clip extraction runs against a real decoded recording; the review lifecycle is
driven by an in-memory async Mongo stand-in. No mock data, no database.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.app.services import async_review as ar

from test_signal_import import build_edf


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# In-memory async Mongo stand-in
# --------------------------------------------------------------------------- #
class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [dict(doc) for doc in self._docs[: length or len(self._docs)]]


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None

    @staticmethod
    def _match(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, value in query.items():
            if doc.get(key) != value:
                return False
        return True

    def find(self, query: dict[str, Any]) -> FakeCursor:
        return FakeCursor([doc for doc in self.docs if self._match(doc, query)])

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if self._match(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.docs.append(dict(document))

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        for doc in self.docs:
            if self._match(doc, query):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                return


class FakeDB:
    def __init__(self) -> None:
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.setdefault(name, FakeCollection())


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _two_channel_edf() -> bytes:
    """5 records x 10 samples/record at 1s each -> 10 Hz, 5 s long, two channels."""
    sig_a = {"label": "SSEP L cortical", "dimension": "uV", "samples_per_record": 10}
    sig_b = {"label": "SSEP R cortical", "dimension": "uV", "samples_per_record": 10}
    records = []
    for r in range(5):
        wave_a = [100 + r * 10 + i for i in range(10)]
        wave_b = [-(100 + r * 10 + i) for i in range(10)]
        records.append([wave_a, wave_b])
    return build_edf([sig_a, sig_b], records, record_duration=1.0)


# --------------------------------------------------------------------------- #
# Clip extraction (pure)
# --------------------------------------------------------------------------- #
def test_extract_clip_window_and_channels():
    raw = _two_channel_edf()
    clip = ar.extract_clip(raw, channel_indexes=[0, 1], start_seconds=1.0, duration_seconds=2.0)
    assert clip["start_seconds"] == 1.0
    assert clip["duration_seconds"] == 2.0
    assert len(clip["channels"]) == 2
    # 2 s at 10 Hz = 20 samples per channel.
    assert len(clip["channels"][0]["samples"]) == 20
    assert clip["channels"][0]["sample_rate_hz"] == 10.0
    assert clip["channels"][0]["label"] == "SSEP L cortical"


def test_extract_clip_rejects_window_beyond_recording():
    raw = _two_channel_edf()
    with pytest.raises(ar.AsyncReviewError):
        ar.extract_clip(raw, channel_indexes=[0], start_seconds=10.0, duration_seconds=2.0)


def test_extract_clip_rejects_bad_duration():
    raw = _two_channel_edf()
    with pytest.raises(ar.AsyncReviewError):
        ar.extract_clip(raw, channel_indexes=[0], start_seconds=0.0, duration_seconds=99.0)


def test_extract_clip_rejects_no_channels():
    raw = _two_channel_edf()
    with pytest.raises(ar.AsyncReviewError):
        ar.extract_clip(raw, channel_indexes=[], start_seconds=0.0, duration_seconds=2.0)


# --------------------------------------------------------------------------- #
# Review lifecycle (IO)
# --------------------------------------------------------------------------- #
def _create(db: FakeDB) -> dict[str, Any]:
    raw = _two_channel_edf()
    return run(
        ar.create_review(
            db,
            raw,
            tenant_id="default",
            case_id="case-1",
            node_key="ssep_upper",
            modality="ssep_upper",
            channel_indexes=[0, 1],
            start_seconds=0.5,
            duration_seconds=2.0,
            protocol_snapshot={"criteria": {"amplitude_drop_percent": {"warning_at": 50}}},
            message="Possible amplitude drop after positioning.",
            requested_by={"user_id": "tech-1", "display_name": "Tech One"},
        )
    )


def test_create_review_stores_clip_and_snapshot():
    db = FakeDB()
    review = _create(db)
    assert review["status"] == "open"
    assert review["case_id"] == "case-1"
    assert review["protocol_snapshot"]["criteria"]["amplitude_drop_percent"]["warning_at"] == 50
    assert len(review["clip"]["channels"]) == 2


def test_review_full_lifecycle_claim_answer_close():
    db = FakeDB()
    review = _create(db)
    review_id = review["review_id"]
    expert = {"user_id": "exp-1", "display_name": "Dr Expert"}

    claimed = run(ar.claim_review(db, review_id=review_id, reviewer=expert))
    assert claimed["status"] == "in_review"
    assert claimed["reviewer_id"] == "exp-1"

    answered = run(
        ar.answer_review(
            db,
            review_id=review_id,
            reviewer=expert,
            verdict="concerning",
            notes="Amplitude drop is real; recommend checking positioning.",
            annotations=[{"t_seconds": 1.2, "amplitude_uv": 3.4, "channel_index": 0, "label": "drop"}],
        )
    )
    assert answered["status"] == "answered"
    assert answered["response"]["verdict"] == "concerning"
    assert answered["response"]["annotations"][0]["label"] == "drop"

    closed = run(ar.close_review(db, review_id=review_id))
    assert closed["status"] == "closed"


def test_cannot_claim_twice():
    db = FakeDB()
    review = _create(db)
    expert = {"user_id": "exp-1", "display_name": "Dr Expert"}
    run(ar.claim_review(db, review_id=review["review_id"], reviewer=expert))
    with pytest.raises(ar.AsyncReviewError):
        run(ar.claim_review(db, review_id=review["review_id"], reviewer=expert))


def test_answer_rejects_unknown_verdict():
    db = FakeDB()
    review = _create(db)
    with pytest.raises(ar.AsyncReviewError):
        run(
            ar.answer_review(
                db,
                review_id=review["review_id"],
                reviewer={"user_id": "exp-1", "display_name": "Dr Expert"},
                verdict="totally_fine",
                notes=None,
            )
        )


def test_list_reviews_excludes_clip_payload():
    db = FakeDB()
    _create(db)
    reviews = run(ar.list_reviews(db, tenant_id="default", case_id="case-1", include_clip=False))
    assert len(reviews) == 1
    clip = reviews[0]["clip"]
    # The list view carries only a clip summary, not the full waveform samples.
    assert "channel_count" in clip
    assert clip["channel_count"] == 2
    assert "channels" not in clip


def test_list_reviews_status_filter_validation():
    db = FakeDB()
    _create(db)
    with pytest.raises(ar.AsyncReviewError):
        run(ar.list_reviews(db, tenant_id="default", status="bogus"))
