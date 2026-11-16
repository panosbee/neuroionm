"""Tests for the interoperability + system-of-record layer:

* The canonical channel ontology — deterministic normalisation/resolution and the
  AI-propose / human-confirm separation.
* The sealed case object — deterministic hashing, the per-case hash chain, and
  tamper-evidence (any post-seal change is provable).

These cover the pure logic that makes the moat defensible, with no database and
no mock data.
"""

from datetime import UTC, datetime

import pytest

from backend.app.services import case_seal as cs
from backend.app.services import channel_ontology as onto


# --------------------------------------------------------------------------- #
# Ontology: normalisation
# --------------------------------------------------------------------------- #
def test_normalize_collapses_separators_and_case():
    assert onto.normalize_label("L-Tib") == "l tib"
    assert onto.normalize_label("L_TIB") == "l tib"
    assert onto.normalize_label("  l   tib  ") == "l tib"
    assert onto.normalize_label("Lt/Tibial") == "lt tibial"


def test_normalize_variants_share_one_key():
    # The whole point: cosmetic spelling variants collapse to one alias key, so a
    # single human confirmation covers all of them.
    keys = {onto.normalize_label(v) for v in ["L-Tib", "L_Tib", "l  tib", "L.TIB"]}
    assert keys == {"l tib"}


# --------------------------------------------------------------------------- #
# Ontology: canonical vocabulary + mapping construction
# --------------------------------------------------------------------------- #
def test_canonical_code_and_label():
    assert onto.canonical_code("ssep", "tibial", "L") == "ssep:tibial:L"
    mapping = onto.build_mapping("ssep", "tibial", "L")
    assert mapping["canonical_label"] == "Left Posterior tibial nerve Somatosensory evoked potential"
    assert mapping["alert_family"] == "ssep"
    assert mapping["laterality"] == "L"


def test_build_mapping_rejects_unknown_vocabulary():
    with pytest.raises(onto.OntologyError):
        onto.build_mapping("not_a_modality", "tibial", "L")
    with pytest.raises(onto.OntologyError):
        onto.build_mapping("ssep", "not_anatomy", "L")
    with pytest.raises(onto.OntologyError):
        onto.build_mapping("ssep", "tibial", "X")


def test_midline_default_laterality():
    mapping = onto.build_mapping("dwave", None, None)
    assert mapping["laterality"] == "C"
    assert mapping["canonical_code"] == "dwave:na:C"


# --------------------------------------------------------------------------- #
# Ontology: deterministic heuristic proposer (PROPOSE step, advisory only)
# --------------------------------------------------------------------------- #
def test_detect_laterality():
    assert onto.detect_laterality("left tibial ssep") == "L"
    assert onto.detect_laterality("rt apb mep") == "R"
    assert onto.detect_laterality("baep wave v") is None  # ambiguous


def test_propose_reads_messy_label():
    proposal = onto.propose_mapping("L-Tib SSEP")
    assert proposal["source"] == "heuristic"
    assert proposal["resolved"] is True
    assert proposal["mapping"]["modality"] == "ssep"
    assert proposal["mapping"]["anatomy"] == "tibial"
    assert proposal["mapping"]["laterality"] == "L"
    # Advisory: a heuristic proposal is always strictly below a learned alias.
    assert proposal["confidence"] < onto.LEARNED_CONFIDENCE


def test_propose_uses_anatomy_modality_hint_when_modality_unclear():
    # "Wave V" carries no explicit modality token but the cochlear anatomy hints baep.
    proposal = onto.propose_mapping("Wave V Right")
    assert proposal["mapping"]["modality"] == "baep"


def test_propose_empty_label_raises():
    with pytest.raises(onto.OntologyError):
        onto.propose_mapping("   ")


def test_propose_unknown_label_is_unresolved_not_a_guess():
    proposal = onto.propose_mapping("XYZ123")
    assert proposal["resolved"] is False
    assert proposal["mapping"] is None
    assert proposal["confidence"] == 0.0


# --------------------------------------------------------------------------- #
# Ontology: alias document factory (the learned map row)
# --------------------------------------------------------------------------- #
def test_new_alias_document_shape():
    mapping = onto.build_mapping("tcmep", "apb", "R")
    actor = {"username": "test-admin", "role": "admin", "user_id": "admin:panos"}
    doc = onto.new_alias_document(raw_label="R-APB", mapping=mapping, confirmed_by=actor)
    assert doc["alias_key"] == "r apb"
    assert doc["canonical_code"] == "tcmep:apb:R"
    assert doc["confirmations"] == 1
    assert doc["scope"] == "tenant"
    assert doc["confirmed_by"] == "test-admin"


# --------------------------------------------------------------------------- #
# Seal: deterministic canonical JSON + hashing
# --------------------------------------------------------------------------- #
def _sample_case():
    return {
        "case_id": "case-1",
        "title": "C4-C5 ACDF",
        "mode": "live",
        "procedure_key": "acdf",
        "selected_node_keys": ["c4_c5"],
        "non_identifying_ref": "REF-01",
        "notes": "n",
        "created_at": datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
    }


def _sample_events():
    return [
        {"event_id": "e1", "event_type": "baseline", "title": "Baseline", "description": None, "node_key": "c4_c5", "metadata": {}, "created_at": datetime(2026, 6, 10, 12, 5, tzinfo=UTC)},
        {"event_id": "e2", "event_type": "signal_change", "title": "SSEP drop", "description": "52%", "node_key": "c4_c5", "metadata": {"severity": "warning"}, "created_at": datetime(2026, 6, 10, 12, 30, tzinfo=UTC)},
    ]


def _sample_channel_map():
    return [
        {"raw_label": "L-Tib", "source": "learned", "baseline_pp_amplitude_uv": 2.0, "latest_pp_amplitude_uv": 1.0,
         "mapping": onto.build_mapping("ssep", "tibial", "L")},
    ]


def test_canonical_json_is_order_independent():
    a = {"b": 1, "a": 2, "c": {"y": 1, "x": 2}}
    b = {"c": {"x": 2, "y": 1}, "a": 2, "b": 1}
    assert cs.canonical_json(a) == cs.canonical_json(b)


def test_content_hash_is_deterministic():
    payload = cs.build_canonical_payload(
        case=_sample_case(), events=_sample_events(), debriefs=[], channel_map=_sample_channel_map(), version=1
    )
    h1 = cs.compute_content_hash(payload, cs.GENESIS_PREVIOUS_HASH)
    h2 = cs.compute_content_hash(payload, cs.GENESIS_PREVIOUS_HASH)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_content_hash_changes_when_content_changes():
    base = cs.build_canonical_payload(case=_sample_case(), events=_sample_events(), debriefs=[], channel_map=_sample_channel_map(), version=1)
    tampered_events = _sample_events()
    tampered_events[1]["description"] = "5%"  # someone edits the recorded drop
    tampered = cs.build_canonical_payload(case=_sample_case(), events=tampered_events, debriefs=[], channel_map=_sample_channel_map(), version=1)
    assert cs.compute_content_hash(base, cs.GENESIS_PREVIOUS_HASH) != cs.compute_content_hash(tampered, cs.GENESIS_PREVIOUS_HASH)


def test_content_hash_depends_on_previous_link():
    payload = cs.build_canonical_payload(case=_sample_case(), events=_sample_events(), debriefs=[], channel_map=_sample_channel_map(), version=1)
    h_genesis = cs.compute_content_hash(payload, cs.GENESIS_PREVIOUS_HASH)
    h_chained = cs.compute_content_hash(payload, "a" * 64)
    assert h_genesis != h_chained  # chaining binds each seal to its predecessor


# --------------------------------------------------------------------------- #
# Seal: verification + tamper-evidence
# --------------------------------------------------------------------------- #
def _make_seal_document(version, previous_hash, *, events=None):
    payload = cs.build_canonical_payload(
        case=_sample_case(), events=events if events is not None else _sample_events(),
        debriefs=[], channel_map=_sample_channel_map(), version=version,
    )
    content_hash = cs.compute_content_hash(payload, previous_hash)
    return cs.new_seal_document(
        case_id="case-1", version=version, canonical_payload=payload,
        content_hash=content_hash, previous_hash=previous_hash,
        sealed_by={"username": "test-admin", "role": "admin"}, reason=None,
    )


def test_verify_intact_seal_passes():
    seal = _make_seal_document(1, cs.GENESIS_PREVIOUS_HASH)
    verdict = cs.verify_seal_document(seal)
    assert verdict["ok"] is True
    assert verdict["recomputed_hash"] == verdict["stored_hash"]


def test_verify_detects_payload_tampering():
    seal = _make_seal_document(1, cs.GENESIS_PREVIOUS_HASH)
    # Someone edits the sealed payload after the fact but leaves the old hash.
    seal["payload"]["timeline"][1]["description"] = "no real change, honest"
    verdict = cs.verify_seal_document(seal)
    assert verdict["ok"] is False
    assert "altered" in verdict["reason"]


def test_verify_chain_passes_for_well_formed_chain():
    s1 = _make_seal_document(1, cs.GENESIS_PREVIOUS_HASH)
    s2 = _make_seal_document(2, s1["content_hash"], events=_sample_events() + [
        {"event_id": "e3", "event_type": "recovery", "title": "Recovered", "description": None, "node_key": "c4_c5", "metadata": {}, "created_at": datetime(2026, 6, 10, 12, 38, tzinfo=UTC)},
    ])
    chain = cs.verify_chain([s2, s1])  # deliberately out of order
    assert chain["ok"] is True
    assert chain["length"] == 2
    assert all(link["ok"] for link in chain["links"])


def test_verify_chain_detects_broken_link():
    s1 = _make_seal_document(1, cs.GENESIS_PREVIOUS_HASH)
    # v2 points at the wrong predecessor (e.g. v1 was rewritten/removed).
    s2 = _make_seal_document(2, "f" * 64)
    chain = cs.verify_chain([s1, s2])
    assert chain["ok"] is False
    broken = [link for link in chain["links"] if not link["ok"]]
    assert broken and broken[0]["version"] == 2


def test_public_seal_omits_payload_by_default():
    seal = _make_seal_document(1, cs.GENESIS_PREVIOUS_HASH)
    assert "payload" not in cs.public_seal(seal)
    assert "payload" in cs.public_seal(seal, include_payload=True)


def test_content_fingerprint_is_version_independent():
    # The chained content_hash differs across versions/chain positions, but the
    # content fingerprint (which drives the idempotency guard) must NOT — identical
    # content at v1 and v2 has to produce the same fingerprint.
    v1 = cs.build_canonical_payload(case=_sample_case(), events=_sample_events(), debriefs=[], channel_map=_sample_channel_map(), version=1)
    v2 = cs.build_canonical_payload(case=_sample_case(), events=_sample_events(), debriefs=[], channel_map=_sample_channel_map(), version=2)
    assert cs.compute_content_hash(v1, cs.GENESIS_PREVIOUS_HASH) != cs.compute_content_hash(v2, "a" * 64)
    assert cs.compute_content_fingerprint(v1) == cs.compute_content_fingerprint(v2)


def test_content_fingerprint_changes_when_content_changes():
    base = cs.build_canonical_payload(case=_sample_case(), events=_sample_events(), debriefs=[], channel_map=_sample_channel_map(), version=1)
    changed_events = _sample_events()
    changed_events[1]["description"] = "60%"  # the recorded drop is edited
    changed = cs.build_canonical_payload(case=_sample_case(), events=changed_events, debriefs=[], channel_map=_sample_channel_map(), version=1)
    assert cs.compute_content_fingerprint(base) != cs.compute_content_fingerprint(changed)


def test_new_seal_document_carries_content_fingerprint():
    seal = _make_seal_document(1, cs.GENESIS_PREVIOUS_HASH)
    assert seal["content_fingerprint"] == cs.compute_content_fingerprint(seal["payload"])

