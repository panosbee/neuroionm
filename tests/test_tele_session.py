"""Unit tests for the Tele-IONM pure logic (no database required).

These cover identity projection, expert eligibility, the session state-machine,
document factories, presence projection (freshness + dedup + eligibility),
inbound-message validation and the transcript summary builder.
"""

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.services.tele_session import (
    ACTIVE_STATUSES,
    DEFAULT_TENANT,
    EXPERT_ACCESS_LEVELS,
    TeleError,
    assert_transition,
    build_session_summary,
    can_transition,
    identity_from_session,
    is_expert_eligible,
    new_event_document,
    new_session_document,
    presence_is_fresh,
    project_experts,
    public_session,
    validate_client_message,
)


# --------------------------------------------------------------------------- #
# Identity projection
# --------------------------------------------------------------------------- #
def test_identity_from_admin_session():
    payload = {"sub": "test-admin", "display_name": "Test Admin", "role": "admin"}
    identity = identity_from_session(payload)
    assert identity["role"] == "admin"
    assert identity["access_level"] == "admin"
    assert identity["display_name"] == "Test Admin"
    assert identity["tenant_id"] == DEFAULT_TENANT
    # Hardcoded admin has no user_id; a stable id is synthesized from the username.
    assert identity["user_id"] == "admin:test-admin"


def test_identity_from_database_user_session():
    payload = {
        "sub": "dr smith",
        "display_name": "Dr Smith",
        "role": "user",
        "user_id": "abc123",
        "access_level": "clinician",
    }
    identity = identity_from_session(payload)
    assert identity["user_id"] == "abc123"
    assert identity["role"] == "user"
    assert identity["access_level"] == "clinician"


# --------------------------------------------------------------------------- #
# Expert eligibility
# --------------------------------------------------------------------------- #
def test_admin_is_always_expert_eligible():
    assert is_expert_eligible({"role": "admin", "access_level": "user"}) is True


@pytest.mark.parametrize("level", sorted(EXPERT_ACCESS_LEVELS))
def test_expert_access_levels_are_eligible(level):
    assert is_expert_eligible({"role": "user", "access_level": level}) is True


def test_learner_is_not_expert_eligible():
    assert is_expert_eligible({"role": "user", "access_level": "learner"}) is False


# --------------------------------------------------------------------------- #
# State machine
# --------------------------------------------------------------------------- #
def test_valid_transitions():
    assert can_transition("requested", "accepted")
    assert can_transition("requested", "declined")
    assert can_transition("requested", "cancelled")
    assert can_transition("accepted", "active")
    assert can_transition("accepted", "ended")
    assert can_transition("active", "ended")


def test_invalid_transitions():
    assert not can_transition("ended", "active")
    assert not can_transition("declined", "accepted")
    assert not can_transition("cancelled", "active")
    assert not can_transition("active", "requested")


def test_assert_transition_raises_on_illegal():
    with pytest.raises(TeleError):
        assert_transition("ended", "active")


def test_active_statuses_constant():
    assert set(ACTIVE_STATUSES) == {"requested", "accepted", "active"}


# --------------------------------------------------------------------------- #
# Document factories
# --------------------------------------------------------------------------- #
def test_new_session_document_shape():
    initiator = identity_from_session(
        {"sub": "dr smith", "display_name": "Dr Smith", "role": "user", "user_id": "u1", "access_level": "clinician"}
    )
    document = new_session_document(initiator=initiator, node_key="ssep_upper", case_id="c1", message="  please help  ")
    assert document["status"] == "requested"
    assert document["initiator_id"] == "u1"
    assert document["initiator_name"] == "Dr Smith"
    assert document["node_key"] == "ssep_upper"
    assert document["case_id"] == "c1"
    assert document["request_message"] == "please help"
    assert document["consultant_id"] is None
    assert document["consent_screen_share"] is False
    assert document["chat_count"] == 0
    assert isinstance(document["created_at"], datetime)
    assert len(document["session_id"]) == 32  # uuid4 hex


def test_new_session_document_blank_message_becomes_none():
    initiator = identity_from_session({"sub": "x", "display_name": "X", "role": "admin"})
    document = new_session_document(initiator=initiator, node_key=None, case_id=None, message="   ")
    assert document["request_message"] is None


def test_new_event_document_shape():
    actor = {"user_id": "u1", "display_name": "Dr Smith"}
    event = new_event_document("s1", tenant_id=DEFAULT_TENANT, event_type="chat_message", actor=actor, payload={"text": "hi"})
    assert event["session_id"] == "s1"
    assert event["event_type"] == "chat_message"
    assert event["actor_id"] == "u1"
    assert event["actor_name"] == "Dr Smith"
    assert event["payload"] == {"text": "hi"}
    assert len(event["event_id"]) == 32


def test_public_session_strips_mongo_id():
    assert "_id" not in public_session({"_id": "x", "session_id": "s1"})


# --------------------------------------------------------------------------- #
# Presence projection
# --------------------------------------------------------------------------- #
def _presence(user_id, *, available=True, role="user", access_level="clinician", age_seconds=0, display_name=None):
    return {
        "user_id": user_id,
        "display_name": display_name or f"User {user_id}",
        "role": role,
        "access_level": access_level,
        "available_for_tele": available,
        "last_seen": datetime.now(UTC) - timedelta(seconds=age_seconds),
        "node_key": "ssep_upper",
    }


def test_presence_is_fresh():
    assert presence_is_fresh(_presence("u1", age_seconds=5)) is True
    assert presence_is_fresh(_presence("u1", age_seconds=120)) is False
    assert presence_is_fresh({"last_seen": None}) is False


def test_project_experts_filters_unavailable_and_stale_and_ineligible():
    rows = [
        _presence("u1", available=True, access_level="clinician"),
        _presence("u2", available=False, access_level="clinician"),  # not available
        _presence("u3", available=True, access_level="clinician", age_seconds=300),  # stale
        _presence("u4", available=True, access_level="learner"),  # not eligible
        _presence("u5", available=True, role="admin", access_level="user"),  # admin eligible
    ]
    experts = project_experts(rows)
    ids = {e["user_id"] for e in experts}
    assert ids == {"u1", "u5"}


def test_project_experts_excludes_self():
    rows = [_presence("u1"), _presence("u2")]
    experts = project_experts(rows, exclude_user_id="u1")
    assert {e["user_id"] for e in experts} == {"u2"}


def test_project_experts_dedupes_multiple_connections():
    rows = [_presence("u1"), _presence("u1"), _presence("u1")]
    experts = project_experts(rows)
    assert len(experts) == 1
    assert experts[0]["connections"] == 3


def test_project_experts_sorted_by_display_name():
    rows = [_presence("u1", display_name="Zoe"), _presence("u2", display_name="Alice")]
    experts = project_experts(rows)
    assert [e["display_name"] for e in experts] == ["Alice", "Zoe"]


# --------------------------------------------------------------------------- #
# Inbound message validation
# --------------------------------------------------------------------------- #
def test_validate_rejects_non_dict():
    with pytest.raises(TeleError):
        validate_client_message("nope")


def test_validate_rejects_unknown_type():
    with pytest.raises(TeleError):
        validate_client_message({"type": "explode"})


def test_validate_heartbeat():
    assert validate_client_message({"type": "heartbeat"}) == {"type": "heartbeat"}


def test_validate_set_available_coerces_bool():
    assert validate_client_message({"type": "set_available", "available": 1})["available"] is True


def test_validate_request_support_cleans_fields():
    out = validate_client_message(
        {"type": "request_support", "node_key": "  ssep_upper  ", "case_id": "c1", "message": "help"}
    )
    assert out["node_key"] == "ssep_upper"
    assert out["case_id"] == "c1"
    assert out["message"] == "help"


def test_validate_accept_requires_session_id():
    with pytest.raises(TeleError):
        validate_client_message({"type": "accept"})
    assert validate_client_message({"type": "accept", "session_id": "s1"})["session_id"] == "s1"


def test_validate_chat_requires_text():
    with pytest.raises(TeleError):
        validate_client_message({"type": "chat", "session_id": "s1", "text": "   "})
    out = validate_client_message({"type": "chat", "session_id": "s1", "text": "hello"})
    assert out["text"] == "hello"


def test_validate_chat_truncates_long_text():
    out = validate_client_message({"type": "chat", "session_id": "s1", "text": "x" * 5000})
    assert len(out["text"]) == 2000


def test_validate_workspace_update_whitelists_patch():
    out = validate_client_message(
        {
            "type": "workspace_update",
            "session_id": "s1",
            "patch": {"node_key": "ssep_upper", "evil_field": "drop tables", "cursor": {"x": 0.5, "y": 0.25}},
        }
    )
    assert out["patch"]["node_key"] == "ssep_upper"
    assert "evil_field" not in out["patch"]
    assert out["patch"]["cursor"] == {"x": 0.5, "y": 0.25}


def test_validate_workspace_update_requires_patch_object():
    with pytest.raises(TeleError):
        validate_client_message({"type": "workspace_update", "session_id": "s1", "patch": "nope"})


def test_validate_annotation_normalizes_coordinates():
    out = validate_client_message(
        {"type": "annotation", "session_id": "s1", "annotation": {"x": 0.123456, "y": 0.2, "label": "alert", "kind": "pin"}}
    )
    assert out["annotation"]["x"] == 0.1235
    assert out["annotation"]["label"] == "alert"
    assert out["annotation"]["kind"] == "pin"


def test_validate_consent_coerces_bool():
    assert validate_client_message({"type": "consent", "session_id": "s1", "granted": "yes"})["granted"] is True


# --------------------------------------------------------------------------- #
# WebRTC signalling validation (Phase 2: voice + screen share)
# --------------------------------------------------------------------------- #
def test_validate_webrtc_signal_requires_session_id():
    with pytest.raises(TeleError):
        validate_client_message({"type": "webrtc_signal", "signal": {"kind": "offer", "sdp": "v=0"}})


def test_validate_webrtc_offer_passthrough():
    out = validate_client_message(
        {"type": "webrtc_signal", "session_id": "s1", "signal": {"kind": "offer", "sdp": "v=0\r\n"}}
    )
    assert out["signal"]["kind"] == "offer"
    assert out["signal"]["sdp"] == "v=0\r\n"


def test_validate_webrtc_answer_requires_sdp():
    with pytest.raises(TeleError):
        validate_client_message({"type": "webrtc_signal", "session_id": "s1", "signal": {"kind": "answer"}})


def test_validate_webrtc_rejects_unknown_kind():
    with pytest.raises(TeleError):
        validate_client_message({"type": "webrtc_signal", "session_id": "s1", "signal": {"kind": "renegotiate"}})


def test_validate_webrtc_rejects_oversized_sdp():
    with pytest.raises(TeleError):
        validate_client_message(
            {"type": "webrtc_signal", "session_id": "s1", "signal": {"kind": "offer", "sdp": "x" * 200_001}}
        )


def test_validate_webrtc_candidate_normalizes():
    out = validate_client_message(
        {
            "type": "webrtc_signal",
            "session_id": "s1",
            "signal": {
                "kind": "candidate",
                "candidate": {
                    "candidate": "candidate:1 1 udp 2122260223 192.168.1.2 53000 typ host",
                    "sdpMid": "0",
                    "sdpMLineIndex": 0,
                    "usernameFragment": "abcd",
                },
            },
        }
    )
    assert out["signal"]["kind"] == "candidate"
    assert out["signal"]["candidate"]["sdpMLineIndex"] == 0
    assert out["signal"]["candidate"]["sdpMid"] == "0"


def test_validate_webrtc_candidate_allows_null_end_of_candidates():
    out = validate_client_message(
        {"type": "webrtc_signal", "session_id": "s1", "signal": {"kind": "candidate", "candidate": None}}
    )
    assert out["signal"]["candidate"] is None


def test_validate_media_state_coerces_booleans():
    out = validate_client_message({"type": "media_state", "session_id": "s1", "audio": 1, "screen": 0})
    assert out["audio"] is True
    assert out["screen"] is False


def test_validate_media_state_requires_session_id():
    with pytest.raises(TeleError):
        validate_client_message({"type": "media_state", "audio": True, "screen": False})


# --------------------------------------------------------------------------- #
# Summary builder
# --------------------------------------------------------------------------- #
def test_build_session_summary_counts_events():
    start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=3, seconds=20)
    session = {
        "session_id": "s1",
        "initiator_name": "Dr Smith",
        "consultant_name": "Prof Expert",
        "status": "ended",
        "end_reason": "completed",
        "started_at": start,
        "ended_at": end,
        "workspace_update_count": 4,
    }
    events = [
        {"event_type": "chat_message"},
        {"event_type": "chat_message"},
        {"event_type": "annotation"},
        {"event_type": "session_started"},
    ]
    summary = build_session_summary(session, events)
    assert "Dr Smith" in summary
    assert "Prof Expert" in summary
    assert "2 chat" in summary
    assert "1 annotations" in summary
    assert "3m 20s" in summary
