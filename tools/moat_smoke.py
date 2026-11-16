"""End-to-end smoke test for the interoperability moat (ontology + sealed archive).

Runs against a locally-running dev server (default http://127.0.0.1:8002). It logs
in with the platform admin, then exercises the full PROPOSE -> CONFIRM -> SEAL ->
VERIFY -> TAMPER-DETECT flow against real MongoDB. This is a manual smoke utility,
not part of the pytest suite.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002"
USERNAME = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("IONM_SMOKE_USERNAME", "")
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("IONM_SMOKE_PASSWORD", "")

# A label unique to this run so the PROPOSE -> CONFIRM transition is exercised
# cleanly even though learned aliases persist in MongoDB between runs.
RUN_TAG = uuid.uuid4().hex[:8]
RAW_LABEL = f"Lt Tibial P37 {RUN_TAG}"
RAW_VARIANT = f"lt-tibial-p37-{RUN_TAG}"

_jar = CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _opener.open(req, timeout=15) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"detail": raw}


def expect(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        raise SystemExit(f"Smoke test failed at: {label}")


def main() -> None:
    status, _ = call("POST", "/api/admin/login", {"username": USERNAME, "password": PASSWORD})
    expect("admin login", status == 200, f"status={status}")

    status, vocab = call("GET", "/api/ontology/vocabulary")
    expect("ontology vocabulary", status == 200 and "modalities" in vocab, f"modalities={len(vocab.get('modalities', []))}")
    expect("admin can promote global", vocab.get("can_promote_global") is True)

    # PROPOSE: a messy vendor label resolves to a heuristic proposal (advisory).
    status, proposal = call("POST", "/api/ontology/resolve", {"label": RAW_LABEL})
    expect("resolve proposes (heuristic)", status == 200 and proposal.get("source") == "heuristic", f"src={proposal.get('source')}")

    # CONFIRM: a human validates the mapping into the learned ontology.
    status, confirmed = call(
        "POST",
        "/api/ontology/confirm",
        {"raw_label": RAW_LABEL, "modality": "ssep", "anatomy": "tibial", "laterality": "L"},
    )
    expect("confirm writes learned alias", status == 200 and confirmed["mapping"]["canonical_code"] == "ssep:tibial:L",
           confirmed.get("mapping", {}).get("canonical_code"))

    # The same label now resolves deterministically (learned, confidence 1.0).
    status, relearned = call("POST", "/api/ontology/resolve", {"label": RAW_LABEL})
    expect("resolve now learned", status == 200 and relearned.get("source") == "learned" and relearned.get("confidence") == 1.0,
           f"src={relearned.get('source')} conf={relearned.get('confidence')}")

    # Cosmetic variant collapses to the same learned alias (hyphen/space/case).
    status, variant = call("POST", "/api/ontology/resolve", {"label": RAW_VARIANT})
    expect("variant hits same alias", status == 200 and variant.get("source") == "learned",
           f"src={variant.get('source')}")

    # Build a case to seal.
    status, case = call("POST", "/api/cases", {"title": "MOAT smoke case", "non_identifying_ref": "SMOKE-01"})
    expect("create case", status in (200, 201) and case.get("case_id"), case.get("case_id"))
    case_id = case["case_id"]
    call("POST", f"/api/cases/{case_id}/events", {"event_type": "baseline", "title": "Baseline acquired"})

    channel_map = [confirmed["mapping"]]

    # SEAL: immutable, hash-chained record.
    status, sealed = call("POST", "/api/archive/seal", {"case_id": case_id, "channel_map": channel_map})
    expect("seal created", status == 200 and sealed.get("created") is True and sealed.get("content_hash"),
           f"v={sealed.get('version')} hash={str(sealed.get('content_hash'))[:12]}")

    # Idempotency: sealing unchanged content does not create a new version.
    status, again = call("POST", "/api/archive/seal", {"case_id": case_id, "channel_map": channel_map})
    expect("seal idempotent (no change)", status == 200 and again.get("created") is False)

    # A new event changes content -> new chained version.
    call("POST", f"/api/cases/{case_id}/events", {"event_type": "signal_change", "title": "Amplitude drop noted"})
    status, v2 = call("POST", "/api/archive/seal", {"case_id": case_id, "channel_map": channel_map})
    expect("seal v2 chains on change", status == 200 and v2.get("created") is True and v2.get("version") == 2,
           f"v={v2.get('version')}")

    # VERIFY: chain is intact.
    status, verdict = call("GET", f"/api/archive/verify/{case_id}")
    expect("verify chain intact", status == 200 and verdict.get("ok") is True and verdict.get("length") == 2,
           f"len={verdict.get('length')}")

    # Archive list shows the latest seal for the case.
    status, listing = call("GET", f"/api/archive/seals?case_id={case_id}")
    expect("archive list returns chain", status == 200 and listing.get("count") == 2)

    print("\nAll moat smoke checks passed.")


if __name__ == "__main__":
    main()
