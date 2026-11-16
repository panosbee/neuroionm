import pytest

from pydantic import ValidationError

from backend.app.middleware.auth_gate import is_admin_page, is_public_path, safe_next_path
from backend.app.models.domain import new_case_debrief_document
from backend.app.routes.ai import normalize_case_debrief, normalize_clinical_copilot
from backend.app.routes.admin import AdminUserCreateRequest, BodyMapCoordinateUpdateRequest, is_browser_form_login
from backend.app.services.ai import parse_json_object
from backend.app.services.admin_auth import (
    AdminAuthError,
    authenticate_admin,
    create_user_session,
    hash_password,
    public_admin,
    safe_redirect_path,
    verify_admin_session,
    verify_password_hash,
    verify_platform_session,
)
from backend.app.services.bodymap_coordinates import (
    CURRENT_COORDINATE_SYSTEM,
    load_bodymap_asset,
    load_seed_coordinates,
    normalize_coordinate_document,
    normalize_seed_coordinate_map,
)
from backend.app.services.academy_content import build_case_scenarios, grade_case_scenario
from backend.app.services.academy_progress import merge_progress
from backend.app.services.waveforms import build_waveform_lab, grade_waveform_quiz


def planner_fixture():
    return {
        "node": {
            "key": "academy.test.spine",
            "title_el": "Test Spine Lesson",
            "domain": "spine",
            "node_type": "disc_level",
        },
        "roots": ["L4", "L5"],
        "cranial_nerves": [],
        "protocols": [{"key": "protocol.spine.mep_emg.setup", "title_el": "Spine MEP/EMG setup"}],
        "pins": [
            {
                "target_id": "Tibialis anterior:left:anterior",
                "lat_name": "Tibialis anterior",
                "name_el": "Πρόσθιος κνημιαίος",
                "side": "L",
                "view": "anterior",
                "x": 42,
                "y": 72,
                "placement": "muscle belly",
                "action": "dorsiflexion",
                "roots": ["L4", "L5"],
            }
        ],
        "clinical_plan": {
            "channel_plan": [
                {
                    "lat_name": "Tibialis anterior",
                    "name_el": "Πρόσθιος κνημιαίος",
                    "roots": ["L4", "L5"],
                    "placement": "muscle belly",
                }
            ],
            "gaps": [],
        },
    }


def test_waveform_lab_has_adjustable_scenarios_and_quiz():
    lab = build_waveform_lab(
        planner_fixture(),
        {"amplitude_drop_percent": 70, "latency_shift_ms": 6, "threshold_ma": 4, "noise_scale": 2.0},
    )

    scenario_keys = {scenario["scenario_key"] for scenario in lab["scenarios"]}

    assert {"baseline", "amplitude_drop", "latency_shift", "combined_warning", "artifact", "stimulation_failure"}.issubset(scenario_keys)
    assert lab["parameters"]["amplitude_drop_percent"] == 70
    assert len(lab["quiz"]) >= 3
    assert lab["scenarios"][0]["channels"][0]["samples"]


def test_waveform_quiz_grading_detects_wrong_artifact_answer():
    lab = build_waveform_lab(planner_fixture())
    answers = {
        "artifact_recognition": ["baseline"],
        "warning_patterns": ["amplitude_drop", "latency_shift", "combined_warning", "threshold_change"],
        "technical_first": ["baseline_comparison", "impedance_cables", "anesthesia_physiology"],
    }

    grade = grade_waveform_quiz(lab, answers)

    assert grade["score_percent"] < 100
    assert grade["passed"] is False
    assert any("artifact" in item.lower() for item in grade["recommendations"])


def test_case_scenario_scoring_penalizes_missing_and_extra_actions():
    waveforms = build_waveform_lab(planner_fixture())
    scenarios = build_case_scenarios(planner_fixture(), waveforms)
    lesson = {"case_scenarios": scenarios}
    scenario = scenarios[1]

    grade = grade_case_scenario(
        lesson,
        scenario["id"],
        [scenario["expected_actions"][0], "Escalate immediately without checking artifact/noise."],
    )

    assert grade["passed"] is False
    assert grade["missing_actions"]
    assert grade["extra_actions"]
    assert grade["recommendations"]


def test_progress_merge_builds_reportable_mistakes_and_review_targets():
    lesson = {
        "node": {"title_el": "Test Lesson", "domain": "spine"},
        "checklist": [{"id": "review_anatomy"}, {"id": "waveform_lab"}],
    }
    current = {"learner_id": "learner-1", "node_key": "academy.test", "checklist": {"review_anatomy": True}}
    patch = {
        "placement_validation": {
            "summary": {"status": "needs_correction", "accuracy_percent": 0},
            "results": [
                {
                    "target_id": "target-1",
                    "status": "off",
                    "target": {"name_el": "Target"},
                    "feedback": "Reposition to target.",
                }
            ],
        },
        "placement_passed": False,
        "checkpoint_grade": {
            "score_percent": 50,
            "results": [
                {"checkpoint_id": "linked_anatomy", "passed": False, "missing_values": ["L5"], "extra_values": []}
            ],
        },
    }

    merged = merge_progress(current, patch, lesson)

    assert merged["lesson_status"] == "in_progress"
    assert merged["mistakes"]
    assert "Electrode placement lab" in merged["recommended_review"]


def test_bodymap_asset_metadata_tracks_high_resolution_recuration_state():
    asset = load_bodymap_asset()

    assert asset["width"] == 1304
    assert asset["height"] == 3033
    assert asset["coordinate_system"] == "percentage_of_full_image"
    assert asset["status"] == "high_res_asset_installed_coordinates_require_recuration"
    assert "posterior_upper_limb" in asset["view_bounds"]


def test_bodymap_coordinate_update_rejects_out_of_bounds_points():
    try:
        BodyMapCoordinateUpdateRequest(
            lat_name="Tibialis anterior",
            coordinates=[{"view": "anterior", "x": 120, "y": 72, "side": "L"}],
        )
    except ValidationError as error:
        assert "less than or equal to 100" in str(error)
    else:
        raise AssertionError("Out-of-bounds coordinate should fail validation")


def test_bodymap_seed_coordinates_include_reset_source_target():
    seed_coordinates = load_seed_coordinates()

    assert seed_coordinates["Abductor digiti minimi"] == [
        {"view": "anterior", "x": 43.5, "y": 44.5, "side": "L"},
        {"view": "anterior", "x": 10.9, "y": 44.5, "side": "R"},
    ]


def test_legacy_bodymap_coordinates_normalize_into_current_asset_bounds():
    document = {
        "lat_name": "Biceps brachii",
        "coordinate_system": "percentage",
        "source": "ionm-coords.json",
        "coordinates": [
            {"view": "anterior", "x": 39.5, "y": 28, "side": "L"},
            {"view": "anterior", "x": 14.9, "y": 28, "side": "R"},
        ],
    }

    normalized = normalize_coordinate_document(document)

    assert normalized["coordinate_system"] == CURRENT_COORDINATE_SYSTEM
    assert all(31 <= pin["x"] <= 65 for pin in normalized["coordinates"])
    assert all(2 <= pin["y"] <= 47 for pin in normalized["coordinates"])
    assert normalized["coordinates"][0]["x"] > normalized["coordinates"][1]["x"]


def test_normalized_seed_coordinate_map_keeps_c4c5_anchor_baseline():
    normalized = normalize_seed_coordinate_map()

    deltoid = normalized["Deltoid"]
    biceps = normalized["Biceps brachii"]

    assert deltoid[0]["side"] == "L"
    assert round(deltoid[0]["x"], 1) == 57.4
    assert round(deltoid[0]["y"], 1) == 11.6
    assert biceps[0]["y"] > deltoid[0]["y"]
    assert deltoid[0]["x"] > deltoid[1]["x"]


def test_admin_login_detects_native_browser_form_posts():
    class RequestStub:
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "accept": "text/html,application/xhtml+xml",
        }

    assert is_browser_form_login(RequestStub()) is True


def test_admin_user_creation_required_fields_are_minimal():
    request = AdminUserCreateRequest(
        username="clinical.user",
        password="StrongPass123",
        first_name="Clinical",
        last_name="User",
        email="clinical.user@example.org",
        phone="+302101234567",
    )

    assert request.access_level == "learner"
    assert request.status == "active"
    assert request.professional_title is None
    assert request.specialty is None
    with pytest.raises(ValidationError):
        AdminUserCreateRequest(
            username="clinical.user",
            password="StrongPass123",
            first_name="Clinical",
            last_name="User",
            email="clinical.user@example.org",
        )


def test_auth_gate_keeps_health_public_and_workspaces_private():
    assert is_public_path("/api/health") is True
    assert is_public_path("/api/ready") is True
    assert is_public_path("/login.html") is True
    assert is_public_path("/live.html") is False
    assert is_public_path("/academy.html") is False
    assert is_public_path("/api/knowledge/tree") is False


def test_auth_gate_marks_admin_page_as_admin_only_surface():
    assert is_admin_page("/admin.html") is True
    assert is_admin_page("/admin") is True
    assert is_admin_page("/academy.html") is False
    assert is_admin_page("/live.html") is False


def test_auth_redirect_paths_are_relative_only():
    assert safe_next_path("/academy.html", "node=live.spine.cervical.disc.c4-c5") == "/academy.html?node=live.spine.cervical.disc.c4-c5"
    assert safe_next_path("https://example.com", None) == "/"
    assert safe_redirect_path("//example.com", default="/") == "/"
    assert safe_redirect_path("/manual.html", default="/") == "/manual.html"


def test_configured_admin_account_uses_password_hash(monkeypatch):
    from backend.app.services import admin_auth

    password_hash = hash_password("second-admin-password", salt="test-salt")
    monkeypatch.setattr(
        admin_auth.settings,
        "admin_accounts_json",
        '[{"username":"second admin","display_name":"Second Admin","password_hash":"' + password_hash + '"}]',
    )

    assert verify_password_hash("second-admin-password", password_hash) is True
    assert authenticate_admin("second admin", "second-admin-password") == {
        "username": "second admin",
        "display_name": "Second Admin",
        "role": "admin",
    }
    assert authenticate_admin("wrong-user", "wrong-password") is None


def test_public_admin_accepts_session_payload_subject():
    assert public_admin({"sub": "ionm admin", "display_name": "IONM Admin", "role": "admin"}) == {
        "username": "ionm admin",
        "display_name": "IONM Admin",
        "role": "admin",
    }


def test_user_session_allows_platform_but_not_admin_scope():
    session = create_user_session({
        "user_id": "learner-123",
        "username": "learner one",
        "full_name": "Learner One",
        "access_level": "learner",
    })

    platform_payload = verify_platform_session(session["token"])
    assert platform_payload["role"] == "user"
    assert public_admin(platform_payload)["username"] == "learner one"
    with pytest.raises(AdminAuthError):
        verify_admin_session(session["token"])


def test_database_admin_session_has_admin_scope():
    session = create_user_session({
        "user_id": "admin-123",
        "username": "clinical admin",
        "full_name": "Clinical Admin",
        "access_level": "admin",
    })

    admin_payload = verify_admin_session(session["token"])
    assert admin_payload["role"] == "admin"
    assert public_admin(admin_payload)["display_name"] == "Clinical Admin"


def test_structured_ai_parser_accepts_fenced_json_object():
    parsed = parse_json_object('```json\n{"summary":"ok","next_checks":[]}\n```')

    assert parsed["summary"] == "ok"
    assert parsed["next_checks"] == []


def test_structured_ai_parser_rejects_non_json_text():
    try:
        parse_json_object("not json")
    except ValueError as error:
        assert "JSON" in str(error)
    else:
        raise AssertionError("Non-JSON structured AI response should fail")


def test_clinical_copilot_normalizer_requires_schema_fields():
    normalized = normalize_clinical_copilot(
        {
            "summary": "Σύντομη σύνοψη.",
            "confidence": "medium",
            "next_checks": [{"priority": "now", "action": "Check baseline", "rationale": "Needed before interpretation."}],
            "surgeon_communication": [{"when": "after checks", "message": "Baseline under review."}],
            "evidence_used": [{"title": "Protocol", "why_it_matters": "Supports channel selection."}],
            "missing_data": ["No active baseline."],
            "watch_items": ["Amplitude trend."],
            "clinical_caution": "Requires qualified clinical judgement.",
        }
    )

    assert normalized["confidence"] == "medium"
    assert normalized["next_checks"][0]["action"] == "Check baseline"


def test_case_debrief_normalizer_requires_replay_schema():
    normalized = normalize_case_debrief(
        {
            "case_summary": "Case reviewed from timeline.",
            "confidence": "low",
            "timeline_replay": [{"event": "Baseline", "interpretation": "Baseline was documented.", "risk_level": "low"}],
            "decision_points": [{"moment": "After baseline", "recommended_action": "Continue monitoring.", "rationale": "No signal change documented."}],
            "documentation_gaps": ["No anesthesia data."],
            "academy_learning_points": [{"topic": "Baseline quality", "why_it_matters": "Supports comparison.", "practice_prompt": "Review baseline acceptance criteria."}],
            "evidence_used": [{"title": "Protocol", "why_it_matters": "Frames the debrief."}],
            "follow_up": ["Complete missing timeline metadata."],
            "clinical_caution": "Requires qualified clinical review.",
        }
    )

    assert normalized["timeline_replay"][0]["risk_level"] == "low"
    assert normalized["academy_learning_points"][0]["topic"] == "Baseline quality"


def test_case_debrief_document_tracks_archive_metadata():
    document = new_case_debrief_document(
        "case-1",
        focus="post_case",
        node_key="live.spine.cervical.disc.c4-c5",
        model="deepseek-test",
        debrief={"case_summary": "Archived summary", "confidence": "low"},
        retrieval={"status": "ready", "items": []},
        raw_usage={"total_tokens": 10},
        timeline_event_count=2,
    )

    assert document["case_id"] == "case-1"
    assert document["debrief_id"]
    assert document["timeline_event_count"] == 2
    assert document["debrief"]["case_summary"] == "Archived summary"
