"""Tests for the unified-copilot model router (#13).

The routing policy is the single deterministic decision point that keeps
PHI-bearing reasoning on the in-region EU model while sending general reasoning
to the external API. All assertions exercise the pure policy directly.
"""

from __future__ import annotations

import pytest

from backend.app.core.config import settings
from backend.app.services import model_router as mr


@pytest.fixture(autouse=True)
def reset_model_settings(monkeypatch):
    """Default each test to: no local model, PHI routing not strictly required."""
    monkeypatch.setattr(settings, "local_model_enabled", False, raising=False)
    monkeypatch.setattr(settings, "local_model_base_url", None, raising=False)
    monkeypatch.setattr(settings, "local_model_api_key", None, raising=False)
    monkeypatch.setattr(settings, "local_model_name", "ionm-eu-clinical", raising=False)
    monkeypatch.setattr(settings, "phi_routing_required", False, raising=False)
    monkeypatch.setattr(settings, "deepseek_api_key", "ext-key", raising=False)
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com", raising=False)
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-v4-pro", raising=False)
    yield


def _enable_local(monkeypatch):
    monkeypatch.setattr(settings, "local_model_enabled", True, raising=False)
    monkeypatch.setattr(settings, "local_model_base_url", "http://10.0.0.5:8080/v1", raising=False)
    monkeypatch.setattr(settings, "local_model_api_key", "local-key", raising=False)


# --------------------------------------------------------------------------- #
# Focus modes
# --------------------------------------------------------------------------- #
def test_normalize_focus_defaults_and_validates():
    assert mr.normalize_focus(None) == "setup"
    assert mr.normalize_focus(" Signal ") == "signal"
    assert mr.normalize_focus("evidence") == "evidence"
    with pytest.raises(mr.ModelRoutingError):
        mr.normalize_focus("nonsense")


def test_focus_directive_distinct_per_mode():
    directives = {mr.focus_directive(mode) for mode in mr.FOCUS_MODES}
    assert len(directives) == len(mr.FOCUS_MODES)
    assert "SETUP" in mr.focus_directive("setup")
    assert "SIGNAL" in mr.focus_directive("signal")
    assert "EVIDENCE" in mr.focus_directive("evidence")


# --------------------------------------------------------------------------- #
# PHI detection
# --------------------------------------------------------------------------- #
def test_context_phi_detection():
    assert mr.context_contains_phi({"node": {"key": "x"}, "case": None}) is False
    assert mr.context_contains_phi({"case": {"case_id": "c1"}}) is True
    assert mr.context_contains_phi({"case_events": [{"e": 1}], "case": None}) is False
    assert mr.context_contains_phi({"events": [{"e": 1}]}) is True
    assert mr.context_contains_phi({"case_id": ""}) is False
    assert mr.context_contains_phi("not a dict") is False


# --------------------------------------------------------------------------- #
# Routing policy
# --------------------------------------------------------------------------- #
def test_no_phi_routes_to_external():
    route = mr.select_route(contains_phi=False)
    assert route.provider == mr.PROVIDER_DEEPSEEK
    assert route.is_local is False
    assert route.reason == "no_phi_external_ok"
    assert route.api_key == "ext-key"


def test_phi_with_local_stays_in_region(monkeypatch):
    _enable_local(monkeypatch)
    route = mr.select_route(contains_phi=True)
    assert route.provider == mr.PROVIDER_LOCAL_EU
    assert route.is_local is True
    assert route.base_url == "http://10.0.0.5:8080/v1"
    assert route.model == "ionm-eu-clinical"
    assert route.reason == "phi_kept_in_region"


def test_phi_without_local_falls_back_when_not_required():
    route = mr.select_route(contains_phi=True)
    assert route.provider == mr.PROVIDER_DEEPSEEK
    assert route.contains_phi is True
    assert route.reason == "phi_no_local_model_available"


def test_phi_without_local_refuses_when_required(monkeypatch):
    monkeypatch.setattr(settings, "phi_routing_required", True, raising=False)
    with pytest.raises(mr.ModelRoutingError):
        mr.select_route(contains_phi=True)


def test_route_public_hides_api_key(monkeypatch):
    _enable_local(monkeypatch)
    route = mr.select_route(contains_phi=True)
    public = route.public()
    assert "api_key" not in public
    assert public == {
        "provider": mr.PROVIDER_LOCAL_EU,
        "model": "ionm-eu-clinical",
        "contains_phi": True,
        "reason": "phi_kept_in_region",
    }


def test_route_for_context_derives_phi(monkeypatch):
    _enable_local(monkeypatch)
    route = mr.route_for_context({"case": {"case_id": "c9"}})
    assert route.is_local is True
    route2 = mr.route_for_context({"node": {"key": "n1"}})
    assert route2.provider == mr.PROVIDER_DEEPSEEK
    assert route2.contains_phi is False
