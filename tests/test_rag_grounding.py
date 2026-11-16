"""Citation grounding and reranking guards for the RAG layer.

These tests pin the no-invented-citations rule: every citation the copilot lists
must map back to a passage that was actually retrieved, and the reranker must
degrade gracefully to cosine ordering when it is not configured.
"""

import asyncio

from backend.app.services.rag import (
    _apply_rerank,
    ground_citations,
    normalize_title,
)


def test_normalize_title_strips_punctuation_and_case():
    assert normalize_title("  Recurrent Laryngeal Nerve (RLN)!  ") == "recurrent laryngeal nerve rln"


def test_ground_citations_marks_exact_match_as_grounded():
    retrieved = [
        {
            "title": "Recurrent Laryngeal Nerve Monitoring",
            "source_collection": "knowledge_nodes",
            "source_key": "thyroid_rln",
            "source": "AANEM",
            "relevance": 0.91,
        }
    ]
    evidence = [{"title": "Recurrent Laryngeal Nerve Monitoring", "why_it_matters": "RLN safety"}]

    result = ground_citations(evidence, retrieved)

    assert result["grounded_count"] == 1
    assert result["ungrounded_count"] == 0
    assert result["fully_grounded"] is True
    citation = result["citations"][0]
    assert citation["grounded"] is True
    assert citation["source_key"] == "thyroid_rln"
    assert citation["relevance"] == 0.91


def test_ground_citations_flags_invented_citation():
    retrieved = [
        {
            "title": "Vagus Nerve Continuous IONM",
            "source_collection": "knowledge_nodes",
            "source_key": "thyroid_vagus",
        }
    ]
    evidence = [
        {"title": "Vagus Nerve Continuous IONM", "why_it_matters": "CIONM"},
        {"title": "Fabricated Cochrane Review 2099", "why_it_matters": "hallucinated"},
    ]

    result = ground_citations(evidence, retrieved)

    assert result["grounded_count"] == 1
    assert result["ungrounded_count"] == 1
    assert result["fully_grounded"] is False
    grounded = result["citations"][0]
    flagged = result["citations"][1]
    assert grounded["grounded"] is True
    assert flagged["grounded"] is False
    assert "source_key" not in flagged


def test_ground_citations_token_overlap_match():
    retrieved = [
        {
            "title": "SSEP Cortical and Subcortical Montage",
            "source_collection": "ssep_protocols",
            "source_key": "ssep_cortical",
        }
    ]
    evidence = [{"title": "Cortical and Subcortical SSEP Montage", "why_it_matters": "SSEP"}]

    result = ground_citations(evidence, retrieved)

    assert result["grounded_count"] == 1
    assert result["citations"][0]["source_key"] == "ssep_cortical"


def test_apply_rerank_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr("backend.app.services.rag.settings.jina_api_key", None, raising=False)
    candidates = [(0.9, {"text": "alpha"}), (0.8, {"text": "beta"})]

    result = asyncio.run(_apply_rerank("query", candidates))

    assert result is None
