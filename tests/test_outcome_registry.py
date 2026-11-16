"""Tests for the post-op outcome registry + alert-validation loop (item #12).

Covers the deterministic validation maths (confusion matrix, PPV/NPV with empty
denominators returning None not faked), the sealed-timeline alert detection, and
the human-entered outcome consistency rules driven through an in-memory async
Mongo stand-in.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.app.services import outcome_registry as reg
from backend.app.services.outcome_registry import (
    OutcomeError,
    case_alerted,
    summarise,
)

import backend.app.services.case_seal as cs


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None

    @staticmethod
    def _match(doc: dict[str, Any], query: dict[str, Any]) -> bool:
        return all(doc.get(k) == v for k, v in query.items())

    async def find_one(self, query: dict[str, Any], sort: Any = None) -> dict[str, Any] | None:
        matches = [d for d in self.docs if self._match(d, query)]
        if not matches:
            return None
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda d: d.get(key, 0), reverse=direction < 0)
        return dict(matches[0])

    def find(self, query: dict[str, Any]):
        docs = [dict(d) for d in self.docs if self._match(d, query)]

        class _Cursor:
            async def to_list(self, length: Any = None) -> list[dict[str, Any]]:
                return docs

        return _Cursor()

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        for doc in self.docs:
            if self._match(doc, query):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            doc = dict(query)
            doc.update(update.get("$setOnInsert", {}))
            doc.update(update.get("$set", {}))
            self.docs.append(doc)


class FakeDB:
    def __init__(self) -> None:
        self._cols: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._cols.setdefault(name, FakeCollection())


def _identity() -> dict[str, Any]:
    return {"user_id": "u1", "display_name": "Dr Test", "tenant_id": "default"}


# --------------------------------------------------------------------------- #
# Pure maths
# --------------------------------------------------------------------------- #
def test_summarise_confusion_and_predictive_values():
    pairs = [
        {"alerted": True, "deficit": True},    # TP
        {"alerted": True, "deficit": False},   # FP
        {"alerted": False, "deficit": True},   # FN
        {"alerted": False, "deficit": False},  # TN
        {"alerted": False, "deficit": False},  # TN
    ]
    summary = summarise(pairs)
    c = summary["confusion"]
    assert (c["true_positive"], c["false_positive"], c["false_negative"], c["true_negative"]) == (1, 1, 1, 2)
    assert summary["ppv"] == 0.5
    assert summary["npv"] == pytest.approx(0.6667, abs=0.001)
    assert summary["sensitivity"] == 0.5
    assert summary["specificity"] == pytest.approx(0.6667, abs=0.001)


def test_summarise_empty_denominators_return_none():
    summary = summarise([{"alerted": False, "deficit": False}])
    assert summary["ppv"] is None  # no positives at all
    assert summary["sensitivity"] is None
    assert summary["npv"] == 1.0


def test_case_alerted_detects_warning_severity():
    payload = {"timeline": [
        {"metadata": {"severity": "none"}},
        {"metadata": {"severity": "critical", "group": "ssep"}},
    ]}
    info = case_alerted(payload)
    assert info["alerted"] is True
    assert "ssep" in info["modalities"]


def test_case_alerted_false_when_no_alert():
    payload = {"timeline": [{"metadata": {"severity": "watch"}}, {"metadata": {}}]}
    assert case_alerted(payload)["alerted"] is False


# --------------------------------------------------------------------------- #
# Outcome recording rules
# --------------------------------------------------------------------------- #
def test_record_and_get_outcome_roundtrip():
    db = FakeDB()
    run(reg.record_outcome(db, case_id="c1", deficit=True, persistence="permanent",
                           modalities=["TcMEP"], identity=_identity()))
    stored = run(reg.get_outcome(db, "c1"))
    assert stored["deficit"] is True
    assert stored["persistence"] == "permanent"
    assert stored["modalities"] == ["tcmep"]
    assert stored["recorded_by"] == "Dr Test"


def test_record_outcome_update_supersedes():
    db = FakeDB()
    run(reg.record_outcome(db, case_id="c1", deficit=True, persistence="transient",
                           modalities=["ssep"], identity=_identity()))
    run(reg.record_outcome(db, case_id="c1", deficit=False, persistence="none",
                           modalities=[], identity=_identity()))
    stored = run(reg.get_outcome(db, "c1"))
    assert stored["deficit"] is False
    assert stored["persistence"] == "none"
    assert len(db["case_outcomes"].docs) == 1  # one row per case


def test_record_outcome_rejects_inconsistent_persistence():
    db = FakeDB()
    with pytest.raises(OutcomeError):
        run(reg.record_outcome(db, case_id="c1", deficit=True, persistence="none",
                               modalities=["ssep"], identity=_identity()))
    with pytest.raises(OutcomeError):
        run(reg.record_outcome(db, case_id="c1", deficit=False, persistence="permanent",
                               identity=_identity()))


def test_record_outcome_requires_modality_for_deficit():
    db = FakeDB()
    with pytest.raises(OutcomeError):
        run(reg.record_outcome(db, case_id="c1", deficit=True, persistence="transient",
                               modalities=[], identity=_identity()))


# --------------------------------------------------------------------------- #
# End-to-end join against real sealed payloads
# --------------------------------------------------------------------------- #
def _seal_db_with(db: FakeDB, case_id: str, alerted: bool) -> None:
    severity = "critical" if alerted else "none"
    payload = {"timeline": [{"metadata": {"severity": severity, "group": "ssep"}}]}
    db[cs.SEAL_COLLECTION].docs.append(
        {"case_id": case_id, "version": 1, "payload": payload, "tenant_id": "default"}
    )


def test_compute_validation_joins_outcomes_with_seals():
    db = FakeDB()
    # Case A: alerted + deficit -> TP. Case B: alerted + no deficit -> FP.
    _seal_db_with(db, "A", alerted=True)
    _seal_db_with(db, "B", alerted=True)
    run(reg.record_outcome(db, case_id="A", deficit=True, persistence="permanent",
                           modalities=["ssep"], identity=_identity()))
    run(reg.record_outcome(db, case_id="B", deficit=False, persistence="none",
                           identity=_identity()))

    result = run(reg.compute_validation(db, tenant_id="default"))
    assert result["n"] == 2
    assert result["confusion"]["true_positive"] == 1
    assert result["confusion"]["false_positive"] == 1
    assert result["ppv"] == 0.5
    classifications = {row["case_id"]: row["classification"] for row in result["cases"]}
    assert classifications["A"] == "true_positive"
    assert classifications["B"] == "false_positive"


def test_compute_validation_skips_unsealed_cases():
    db = FakeDB()
    run(reg.record_outcome(db, case_id="ghost", deficit=True, persistence="transient",
                           modalities=["ssep"], identity=_identity()))
    result = run(reg.compute_validation(db, tenant_id="default"))
    assert result["n"] == 0  # no sealed record -> nothing to validate
