"""Unit tests for the WebRTC ICE-server builder (Tele-IONM Phase 2).

These are pure functions, so the tests assert exact shapes and verify the
coturn ``use-auth-secret`` HMAC-SHA1 credential scheme byte-for-byte.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from backend.app.services.tele_ice import (
    MIN_TURN_TTL_SECONDS,
    build_ice_servers,
    make_turn_credentials,
)


def test_stun_only_when_no_turn_configured():
    servers = build_ice_servers(user_id="u1", stun_urls="stun:stun.example.org:3478")
    assert servers == [{"urls": ["stun:stun.example.org:3478"]}]


def test_stun_csv_is_split_into_list():
    servers = build_ice_servers(
        user_id="u1",
        stun_urls="stun:a.example:3478, stun:b.example:3478",
    )
    assert servers[0]["urls"] == ["stun:a.example:3478", "stun:b.example:3478"]


def test_empty_when_nothing_configured():
    assert build_ice_servers(user_id="u1", stun_urls=None) == []


def test_turn_static_credentials():
    servers = build_ice_servers(
        user_id="u1",
        stun_urls=None,
        turn_urls="turn:turn.example.org:3478",
        turn_username="alice",
        turn_credential="secret-pass",
    )
    assert servers == [
        {
            "urls": ["turn:turn.example.org:3478"],
            "username": "alice",
            "credential": "secret-pass",
        }
    ]


def test_turn_omitted_when_urls_without_credentials():
    # TURN URLs but no secret and no static credentials -> no broken TURN entry.
    servers = build_ice_servers(
        user_id="u1",
        stun_urls="stun:stun.example.org:3478",
        turn_urls="turn:turn.example.org:3478",
    )
    assert servers == [{"urls": ["stun:stun.example.org:3478"]}]


def test_turn_hmac_credentials_match_coturn_scheme():
    now = 1_000_000
    ttl = 3600
    secret = "shared-secret"
    servers = build_ice_servers(
        user_id="user-42",
        stun_urls=None,
        turn_urls="turn:turn.example.org:3478",
        turn_secret=secret,
        turn_ttl_seconds=ttl,
        now=now,
    )
    assert len(servers) == 1
    entry = servers[0]
    expected_expiry = now + ttl
    assert entry["username"] == f"{expected_expiry}:user-42"
    expected_digest = hmac.new(
        secret.encode("utf-8"), entry["username"].encode("utf-8"), hashlib.sha1
    ).digest()
    assert entry["credential"] == base64.b64encode(expected_digest).decode("ascii")


def test_turn_secret_takes_precedence_over_static():
    now = 2_000_000
    servers = build_ice_servers(
        user_id="u1",
        stun_urls=None,
        turn_urls="turn:turn.example.org:3478",
        turn_secret="sekret",
        turn_username="ignored",
        turn_credential="ignored",
        turn_ttl_seconds=600,
        now=now,
    )
    assert servers[0]["username"].endswith(":u1")
    assert servers[0]["username"].startswith(str(now + 600))


def test_make_turn_credentials_sanitizes_colon_in_user():
    username, _ = make_turn_credentials(
        secret="s", user_id="a:b:c", ttl_seconds=300, now=0
    )
    # Only the single separator colon between expiry and user may appear.
    assert username.count(":") == 1
    assert username.endswith(":a_b_c")


def test_make_turn_credentials_enforces_minimum_ttl():
    username, _ = make_turn_credentials(secret="s", user_id="u1", ttl_seconds=1, now=0)
    expiry = int(username.split(":", 1)[0])
    assert expiry == MIN_TURN_TTL_SECONDS


def test_stun_and_turn_emitted_together():
    servers = build_ice_servers(
        user_id="u1",
        stun_urls="stun:stun.example.org:3478",
        turn_urls=["turn:turn.example.org:3478", "turns:turn.example.org:5349"],
        turn_username="alice",
        turn_credential="pass",
    )
    assert servers[0]["urls"] == ["stun:stun.example.org:3478"]
    assert servers[1]["urls"] == ["turn:turn.example.org:3478", "turns:turn.example.org:5349"]
    assert servers[1]["username"] == "alice"
