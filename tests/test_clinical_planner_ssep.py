"""Planner coverage for the SSEP cortical/subcortical montage and thyroid IONM.

These tests pin the deterministic SSEP generator classification (peripheral /
subcortical / cortical) and confirm the thyroid procedure template and CIONM
protocol are present and internally consistent in the seed data.
"""

import json
from pathlib import Path

from backend.app.services.clinical_planner import (
    build_ssep_plan,
    classify_ssep_channel,
)

SEED_PATH = Path(__file__).resolve().parents[1] / "backend" / "app" / "seed_data" / "ionm_seed.json"


def _load_seed():
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def test_classify_ssep_channel_generators():
    assert classify_ssep_channel("N9 (Erb)") == "peripheral"
    assert classify_ssep_channel("N13") == "subcortical"
    assert classify_ssep_channel("N20") == "cortical"
    assert classify_ssep_channel("P37") == "cortical"
    assert classify_ssep_channel("N22") == "subcortical"
    assert classify_ssep_channel("unknown") == "unclassified"


def test_build_ssep_plan_splits_cortical_and_subcortical():
    node = {
        "metadata": {
            "ssep_montage": {
                "potential": "N20",
                "channels": [
                    {"name": "N9 (Erb)", "montage": "EPi–EPc"},
                    {"name": "N13", "montage": "CV5–Fz"},
                    {"name": "N20", "montage": "CPc–CPi"},
                ],
            }
        }
    }
    plan = build_ssep_plan(node)
    assert plan is not None
    assert plan["has_cortical"] is True
    assert plan["has_subcortical"] is True
    assert plan["has_peripheral"] is True
    assert plan["generators"]["cortical"] == ["N20"]
    assert plan["generators"]["subcortical"] == ["N13"]
    assert plan["generators"]["peripheral"] == ["N9 (Erb)"]
    generators = {channel["generator"] for channel in plan["channels"]}
    assert generators == {"peripheral", "subcortical", "cortical"}


def test_build_ssep_plan_returns_none_without_montage():
    assert build_ssep_plan({"metadata": {}}) is None
    assert build_ssep_plan({}) is None


def test_seed_real_ssep_node_has_cortical_and_subcortical():
    seed = _load_seed()
    nodes = {node["key"]: node for node in seed["knowledge_nodes"]}
    median = nodes["live.ssep.median-nerve"]
    plan = build_ssep_plan(median)
    assert plan is not None
    assert plan["has_cortical"] is True
    assert plan["has_subcortical"] is True


def test_seed_thyroid_template_and_protocol_present():
    seed = _load_seed()
    nodes = {node["key"]: node for node in seed["knowledge_nodes"]}
    protocols = {protocol["key"]: protocol for protocol in seed["protocols"]}

    thyroid = nodes["live.cranial.templates.thyroid"]
    assert "Vocalis (via NIM ETT)" in thyroid["linked_muscles"]
    assert "Cricothyroid" in thyroid["linked_muscles"]
    assert thyroid["linked_cns"] == ["X"]
    assert "protocol.thyroid.cionm" in thyroid["linked_protocols"]
    assert thyroid["metadata"]["cionm"]["combined_event"]

    protocol = protocols["protocol.thyroid.cionm"]
    assert protocol["title_en"]
    assert protocol["content"]["warning"]
    assert protocol["content_en"]["warning"]
