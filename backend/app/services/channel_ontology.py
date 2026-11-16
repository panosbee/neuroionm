"""Canonical IONM channel ontology — the interoperability layer that turns the
messy, vendor-specific channel labels of any acquisition system (Cadwell, Natus/
Nicolet, IONOMED, ...) into one canonical clinical identity.

This is the semantic wrapper that EDF/EDF+ lacks: raw EDF gives you the waveform
but not the meaning ("this channel is the LEFT TIBIAL SSEP"). The ontology is the
defensible asset of the platform — not because of any single algorithm, but
because it is a *validated map* that grows from every confirmed human correction.

Design (the legally-critical separation, never blurred):

* AI / heuristics PROPOSE.  A probabilistic suggestion ("L-Tib" probably means
  left tibial SSEP) is advisory only and is never, by itself, part of the record.
* A human (or an exact, already-learned alias) CONFIRMS.  Only the confirmed
  mapping is authoritative and only that is what gets sealed into a case object.
* Every confirmation FEEDS the learned alias map, so the deterministic resolver
  recognises that exact raw label verbatim next time — with zero ambiguity and
  no model call. The map only ever grows; the canonical vocabulary is fixed code.

The module is split into:
* Pure, fully unit-testable logic: the canonical vocabulary, the deterministic
  normaliser/resolver, the heuristic proposer, and document factories.
* Thin async MongoDB IO used by the routes (the learned alias map + audit).

Nothing here is mocked. The heuristic proposer is a real token/laterality/family
matcher; when an LLM is wired in at the route layer it only ever *adds* candidate
proposals — it can never write the canonical record.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase


DEFAULT_TENANT = "default"
ALIAS_COLLECTION = "channel_aliases"
ONTOLOGY_AUDIT_COLLECTION = "channel_ontology_audit"

# Confidence a deterministic, already-learned exact alias carries. Heuristic
# proposals are always strictly below this so a learned alias always wins.
LEARNED_CONFIDENCE = 1.0
HEURISTIC_MAX_CONFIDENCE = 0.92


# --------------------------------------------------------------------------- #
# Canonical vocabulary (fixed in code — the stable side of the standard)
# --------------------------------------------------------------------------- #
# A canonical modality family. `alert_family` ties the channel to the published
# alert criteria in services.alert_engine, so a resolved channel can be analysed
# without any further mapping.
MODALITIES: dict[str, dict[str, Any]] = {
    "ssep": {"label": "Somatosensory evoked potential", "alert_family": "ssep", "lateralised": True, "metric": "amplitude_uv"},
    "tcmep": {"label": "Transcranial motor evoked potential", "alert_family": "tcmep", "lateralised": True, "metric": "amplitude_uv"},
    "dwave": {"label": "Epidural D-wave", "alert_family": "dwave", "lateralised": False, "metric": "amplitude_uv"},
    "baep": {"label": "Brainstem auditory evoked potential", "alert_family": "baep", "lateralised": True, "metric": "latency_ms"},
    "vep": {"label": "Visual evoked potential", "alert_family": "ssep", "lateralised": True, "metric": "amplitude_uv"},
    "temg": {"label": "Triggered EMG", "alert_family": "temg", "lateralised": True, "metric": "threshold_ma"},
    "femg": {"label": "Free-running EMG", "alert_family": "emg", "lateralised": True, "metric": "amplitude_uv"},
    "eeg": {"label": "Electroencephalography", "alert_family": "emg", "lateralised": True, "metric": "amplitude_uv"},
    "ekg": {"label": "Electrocardiography", "alert_family": "emg", "lateralised": False, "metric": "amplitude_uv"},
}

LATERALITIES = ("L", "R", "C")
LATERALITY_LABELS = {"L": "Left", "R": "Right", "C": "Midline / central"}

# Canonical anatomical targets keyed by a stable code. `tokens` are the surface
# fragments a vendor label tends to carry; `modality_hint` biases the proposer
# when the modality is otherwise ambiguous.
ANATOMY: dict[str, dict[str, Any]] = {
    # SSEP nerves
    "tibial": {"label": "Posterior tibial nerve", "tokens": ["tib", "ptn", "post tib", "psnt", "p37", "tibial"], "modality_hint": "ssep"},
    "peroneal": {"label": "Common peroneal nerve", "tokens": ["peroneal", "perone", "fibular", "cpn"], "modality_hint": "ssep"},
    "median": {"label": "Median nerve", "tokens": ["median", "med", "n20", "mn"], "modality_hint": "ssep"},
    "ulnar": {"label": "Ulnar nerve", "tokens": ["ulnar", "uln", "un"], "modality_hint": "ssep"},
    # Cranial / brainstem
    "cochlear": {"label": "Cochlear / auditory (wave V)", "tokens": ["baep", "abr", "wave v", "wavev", "cochlear", "auditory", "aep"], "modality_hint": "baep"},
    # MEP / EMG muscle targets
    "apb": {"label": "Abductor pollicis brevis", "tokens": ["apb", "abd poll"], "modality_hint": "tcmep"},
    "adm": {"label": "Abductor digiti minimi", "tokens": ["adm", "abd dig"], "modality_hint": "tcmep"},
    "ta": {"label": "Tibialis anterior", "tokens": ["tib ant", "tibialis anterior", " ta ", "tibant"], "modality_hint": "tcmep"},
    "ah": {"label": "Abductor hallucis", "tokens": ["abd hall", "ah ", "hallucis", "abh"], "modality_hint": "tcmep"},
    "gastroc": {"label": "Gastrocnemius", "tokens": ["gastroc", "gastro", "soleus"], "modality_hint": "tcmep"},
    "vmedialis": {"label": "Vastus medialis", "tokens": ["vastus", "vmed", "quad"], "modality_hint": "tcmep"},
    "deltoid": {"label": "Deltoid", "tokens": ["deltoid", "delt"], "modality_hint": "tcmep"},
    "biceps": {"label": "Biceps brachii", "tokens": ["biceps", "bic"], "modality_hint": "tcmep"},
    "tibialis_post": {"label": "Tibialis posterior", "tokens": ["tib post", "tibialis posterior"], "modality_hint": "tcmep"},
    "anal_sphincter": {"label": "External anal sphincter", "tokens": ["anal", "sphincter", "eas"], "modality_hint": "femg"},
    "orbicularis_oculi": {"label": "Orbicularis oculi", "tokens": ["orb oculi", "orbicularis oc", "oo "], "modality_hint": "femg"},
    "orbicularis_oris": {"label": "Orbicularis oris", "tokens": ["orb oris", "orbicularis or", "oris"], "modality_hint": "femg"},
    "tongue": {"label": "Tongue (CN XII)", "tokens": ["tongue", "genioglossus", "xii", "hypoglossal"], "modality_hint": "femg"},
    "trapezius": {"label": "Trapezius (CN XI)", "tokens": ["trap", "xi", "accessory"], "modality_hint": "femg"},
}

# Surface fragments that disambiguate the modality directly.
_MODALITY_TOKENS: dict[str, list[str]] = {
    "ssep": ["ssep", "sep", "somatosensory", "n20", "p37"],
    "tcmep": ["mep", "tcmep", "tces", "cmap", "motor"],
    "dwave": ["d-wave", "dwave", "d wave", "epidural"],
    "baep": ["baep", "abr", "aep", "auditory", "wave v", "wavev"],
    "vep": ["vep", "visual"],
    "temg": ["t-emg", "temg", "triggered", "trig emg", "stim emg", "screw"],
    "femg": ["emg", "free-run", "freerun", "free run", "spontaneous"],
    "eeg": ["eeg"],
    "ekg": ["ecg", "ekg"],
}

_LEFT_TOKENS = ("left", "lt", "l-", "(l)", " l ")
_RIGHT_TOKENS = ("right", "rt", "r-", "(r)", " r ")


class OntologyError(ValueError):
    """Raised for invalid canonical mapping operations."""


# --------------------------------------------------------------------------- #
# Pure normalisation / resolution
# --------------------------------------------------------------------------- #
def normalize_label(raw: str) -> str:
    """Lower-case, collapse separators and whitespace — the stable lookup key.

    This is what makes "L-Tib", "L_TIB", "l  tib" collapse to one alias key so a
    single confirmation covers cosmetic spelling variants of the same label.
    """
    text = (raw or "").strip().lower()
    text = re.sub(r"[-._/\\|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def canonical_code(modality: str, anatomy: str | None, laterality: str | None) -> str:
    """The stable canonical identifier, e.g. ``ssep:tibial:L``."""
    if modality not in MODALITIES:
        raise OntologyError(f"Unknown modality {modality!r}.")
    if anatomy is not None and anatomy not in ANATOMY:
        raise OntologyError(f"Unknown anatomy {anatomy!r}.")
    lat = (laterality or "C").upper()
    if lat not in LATERALITIES:
        raise OntologyError(f"Unknown laterality {laterality!r}.")
    return f"{modality}:{anatomy or 'na'}:{lat}"


def canonical_label(modality: str, anatomy: str | None, laterality: str | None) -> str:
    """A human-readable clinical name for a canonical mapping."""
    parts: list[str] = []
    lat = (laterality or "C").upper()
    if lat in ("L", "R"):
        parts.append(LATERALITY_LABELS[lat])
    if anatomy and anatomy in ANATOMY:
        parts.append(ANATOMY[anatomy]["label"])
    parts.append(MODALITIES[modality]["label"])
    return " ".join(parts)


def build_mapping(modality: str, anatomy: str | None, laterality: str | None) -> dict[str, Any]:
    """A validated canonical mapping object (pure; raises on invalid input)."""
    code = canonical_code(modality, anatomy, laterality)
    lat = (laterality or "C").upper()
    return {
        "canonical_code": code,
        "canonical_label": canonical_label(modality, anatomy, laterality),
        "modality": modality,
        "modality_label": MODALITIES[modality]["label"],
        "anatomy": anatomy,
        "anatomy_label": ANATOMY[anatomy]["label"] if anatomy and anatomy in ANATOMY else None,
        "laterality": lat,
        "laterality_label": LATERALITY_LABELS[lat],
        "alert_family": MODALITIES[modality]["alert_family"],
    }


def detect_laterality(text: str) -> str | None:
    """Best-effort laterality from a normalised label (None if ambiguous)."""
    padded = f" {text} "
    left = any(token in padded for token in _LEFT_TOKENS)
    right = any(token in padded for token in _RIGHT_TOKENS)
    if left and not right:
        return "L"
    if right and not left:
        return "R"
    return None


def _score_modality(text: str) -> tuple[str | None, float]:
    padded = f" {text} "
    best: str | None = None
    best_hits = 0
    for modality, tokens in _MODALITY_TOKENS.items():
        hits = sum(1 for token in tokens if token in padded)
        if hits > best_hits:
            best_hits = hits
            best = modality
    if best is None:
        return None, 0.0
    return best, min(0.45, 0.18 * best_hits + 0.12)


def _score_anatomy(text: str) -> tuple[str | None, float]:
    padded = f" {text} "
    best: str | None = None
    best_len = 0
    for code, meta in ANATOMY.items():
        for token in meta["tokens"]:
            token_norm = token.strip()
            if token_norm and token_norm in padded and len(token_norm) > best_len:
                best = code
                best_len = len(token_norm)
    if best is None:
        return None, 0.0
    # Longer, more specific token matches are more trustworthy.
    return best, min(0.4, 0.16 + 0.03 * best_len)


def propose_mapping(raw_label: str) -> dict[str, Any]:
    """Deterministic heuristic proposal for a raw vendor label.

    This is the PROPOSE step: advisory only. It returns the best-guess canonical
    mapping (or as much of it as it can infer) plus a confidence in [0, 0.92] and
    the reasoning tokens. It never persists anything and is never authoritative.
    """
    text = normalize_label(raw_label)
    if not text:
        raise OntologyError("Channel label is empty.")

    modality, modality_conf = _score_modality(text)
    anatomy, anatomy_conf = _score_anatomy(text)

    # If the modality is unclear but the anatomy carries a strong modality hint,
    # adopt the hint (e.g. "Wave V" → baep) — still advisory, still below learned.
    if modality is None and anatomy is not None:
        modality = ANATOMY[anatomy]["modality_hint"]
        modality_conf = 0.2
    laterality = detect_laterality(text)
    lat_conf = 0.15 if laterality else 0.0

    confidence = round(min(HEURISTIC_MAX_CONFIDENCE, modality_conf + anatomy_conf + lat_conf), 3)
    reasoning = {
        "normalized": text,
        "modality_signal": modality_conf,
        "anatomy_signal": anatomy_conf,
        "laterality_signal": lat_conf,
    }

    if modality is None:
        return {
            "source": "heuristic",
            "resolved": False,
            "confidence": 0.0,
            "mapping": None,
            "reasoning": reasoning,
        }

    mapping = build_mapping(modality, anatomy, laterality)
    return {
        "source": "heuristic",
        "resolved": anatomy is not None,
        "confidence": confidence,
        "mapping": mapping,
        "reasoning": reasoning,
    }


# --------------------------------------------------------------------------- #
# Document factories (pure)
# --------------------------------------------------------------------------- #
def new_alias_document(
    *,
    raw_label: str,
    mapping: dict[str, Any],
    confirmed_by: dict[str, Any],
    tenant_id: str = DEFAULT_TENANT,
    scope: str = "tenant",
) -> dict[str, Any]:
    """A learned alias: an exact normalised label → confirmed canonical mapping."""
    now = datetime.now(UTC)
    return {
        "alias_id": str(uuid4()),
        "tenant_id": tenant_id,
        "alias_key": normalize_label(raw_label),
        "raw_label": raw_label.strip(),
        "scope": scope,  # "tenant" (default) or "global" (Admin-promoted standard)
        "canonical_code": mapping["canonical_code"],
        "mapping": mapping,
        "confirmations": 1,
        "confirmed_by": confirmed_by.get("username") or confirmed_by.get("user_id"),
        "confirmed_role": confirmed_by.get("role"),
        "created_at": now,
        "updated_at": now,
    }


def new_audit_document(
    *,
    action: str,
    raw_label: str,
    mapping: dict[str, Any] | None,
    actor: dict[str, Any],
    tenant_id: str = DEFAULT_TENANT,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An immutable audit row for every ontology decision (propose/confirm/promote)."""
    row = {
        "audit_id": str(uuid4()),
        "tenant_id": tenant_id,
        "action": action,
        "raw_label": raw_label,
        "alias_key": normalize_label(raw_label),
        "canonical_code": mapping["canonical_code"] if mapping else None,
        "actor": actor.get("username") or actor.get("user_id"),
        "actor_role": actor.get("role"),
        "created_at": datetime.now(UTC),
    }
    if extra:
        row["detail"] = extra
    return row


def public_alias(document: dict[str, Any]) -> dict[str, Any]:
    """Strip Mongo internals for an alias row."""
    clean = {key: value for key, value in document.items() if key != "_id"}
    return clean


def vocabulary_snapshot() -> dict[str, Any]:
    """The fixed canonical vocabulary, for the mapping UI dropdowns."""
    return {
        "modalities": [
            {"code": code, "label": meta["label"], "alert_family": meta["alert_family"], "lateralised": meta["lateralised"]}
            for code, meta in MODALITIES.items()
        ],
        "anatomy": [
            {"code": code, "label": meta["label"], "modality_hint": meta["modality_hint"]}
            for code, meta in ANATOMY.items()
        ],
        "lateralities": [{"code": code, "label": LATERALITY_LABELS[code]} for code in LATERALITIES],
    }


# --------------------------------------------------------------------------- #
# Thin async MongoDB IO
# --------------------------------------------------------------------------- #
async def ensure_ontology_indexes(database: AsyncIOMotorDatabase) -> None:
    """Indexes for the learned alias map and the audit trail."""
    await database[ALIAS_COLLECTION].create_index([("tenant_id", 1), ("scope", 1), ("alias_key", 1)], unique=True, name="alias_unique")
    await database[ALIAS_COLLECTION].create_index([("canonical_code", 1)], name="alias_canonical")
    await database[ONTOLOGY_AUDIT_COLLECTION].create_index([("tenant_id", 1), ("created_at", -1)], name="ontology_audit_recent")
    await database[ONTOLOGY_AUDIT_COLLECTION].create_index([("alias_key", 1)], name="ontology_audit_alias")


async def lookup_alias(database: AsyncIOMotorDatabase, raw_label: str, *, tenant_id: str = DEFAULT_TENANT) -> dict[str, Any] | None:
    """The deterministic side: an exact, already-learned mapping for this label.

    A tenant-scoped alias wins over a global one (local clinical convention beats
    the published default). Returns None when the label has never been confirmed.
    """
    key = normalize_label(raw_label)
    if not key:
        return None
    tenant_hit = await database[ALIAS_COLLECTION].find_one({"tenant_id": tenant_id, "scope": "tenant", "alias_key": key})
    if tenant_hit:
        return tenant_hit
    return await database[ALIAS_COLLECTION].find_one({"scope": "global", "alias_key": key})


async def resolve_label(database: AsyncIOMotorDatabase, raw_label: str, *, tenant_id: str = DEFAULT_TENANT) -> dict[str, Any]:
    """Full resolution: deterministic learned alias first, heuristic proposal second.

    A learned alias is returned as ``source="learned"`` with confidence 1.0 and is
    authoritative. Otherwise a heuristic ``source="heuristic"`` proposal is returned
    for human confirmation. The two are never mixed.
    """
    alias = await lookup_alias(database, raw_label, tenant_id=tenant_id)
    if alias:
        return {
            "raw_label": raw_label,
            "source": "learned",
            "resolved": True,
            "confidence": LEARNED_CONFIDENCE,
            "mapping": alias["mapping"],
            "alias_id": alias["alias_id"],
            "scope": alias["scope"],
        }
    proposal = propose_mapping(raw_label)
    proposal["raw_label"] = raw_label
    return proposal


async def confirm_mapping(
    database: AsyncIOMotorDatabase,
    *,
    raw_label: str,
    modality: str,
    anatomy: str | None,
    laterality: str | None,
    actor: dict[str, Any],
    tenant_id: str = DEFAULT_TENANT,
    promote_global: bool = False,
) -> dict[str, Any]:
    """The CONFIRM step: persist a human-validated mapping into the learned map.

    This is the only path that writes the authoritative ontology. The validated
    mapping is upserted as a tenant alias (and, when an Admin promotes it, also as
    a global standard). Each confirmation increments the confirmation counter that
    represents accumulated validation weight. Every call is audited.
    """
    mapping = build_mapping(modality, anatomy, laterality)
    scope = "tenant"
    alias = await _upsert_alias(database, raw_label=raw_label, mapping=mapping, actor=actor, tenant_id=tenant_id, scope=scope)
    await database[ONTOLOGY_AUDIT_COLLECTION].insert_one(
        new_audit_document(action="confirm", raw_label=raw_label, mapping=mapping, actor=actor, tenant_id=tenant_id)
    )

    promoted = None
    if promote_global:
        if str(actor.get("role")) != "admin":
            raise OntologyError("Only an administrator may promote a mapping to the global standard.")
        promoted = await _upsert_alias(database, raw_label=raw_label, mapping=mapping, actor=actor, tenant_id=tenant_id, scope="global")
        await database[ONTOLOGY_AUDIT_COLLECTION].insert_one(
            new_audit_document(action="promote_global", raw_label=raw_label, mapping=mapping, actor=actor, tenant_id=tenant_id)
        )

    return {
        "mapping": mapping,
        "alias": public_alias(alias),
        "promoted_global": public_alias(promoted) if promoted else None,
    }


async def _upsert_alias(
    database: AsyncIOMotorDatabase,
    *,
    raw_label: str,
    mapping: dict[str, Any],
    actor: dict[str, Any],
    tenant_id: str,
    scope: str,
) -> dict[str, Any]:
    key = normalize_label(raw_label)
    now = datetime.now(UTC)
    existing = await database[ALIAS_COLLECTION].find_one({"tenant_id": tenant_id, "scope": scope, "alias_key": key})
    if existing:
        await database[ALIAS_COLLECTION].update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "mapping": mapping,
                    "canonical_code": mapping["canonical_code"],
                    "raw_label": raw_label.strip(),
                    "confirmed_by": actor.get("username") or actor.get("user_id"),
                    "confirmed_role": actor.get("role"),
                    "updated_at": now,
                },
                "$inc": {"confirmations": 1},
            },
        )
        return await database[ALIAS_COLLECTION].find_one({"_id": existing["_id"]})  # type: ignore[return-value]

    document = new_alias_document(raw_label=raw_label, mapping=mapping, confirmed_by=actor, tenant_id=tenant_id, scope=scope)
    await database[ALIAS_COLLECTION].insert_one(document)
    return document


async def list_aliases(
    database: AsyncIOMotorDatabase,
    *,
    tenant_id: str = DEFAULT_TENANT,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Every learned alias visible to a tenant (its own + the global standard)."""
    cursor = (
        database[ALIAS_COLLECTION]
        .find({"$or": [{"tenant_id": tenant_id, "scope": "tenant"}, {"scope": "global"}]})
        .sort("updated_at", -1)
    )
    rows = await cursor.to_list(length=limit)
    return [public_alias(row) for row in rows]


async def resolve_channels(
    database: AsyncIOMotorDatabase,
    channels: list[dict[str, Any]],
    *,
    tenant_id: str = DEFAULT_TENANT,
) -> list[dict[str, Any]]:
    """Resolve a whole imported-recording channel list to canonical identities.

    Each item carries the original channel index/label plus the resolution
    (learned or heuristic). The UI renders the heuristic ones for confirmation and
    the learned ones as already-canonical.
    """
    results: list[dict[str, Any]] = []
    for channel in channels:
        label = str(channel.get("label") or "").strip()
        resolution = await resolve_label(database, label, tenant_id=tenant_id) if label else {
            "raw_label": label,
            "source": "heuristic",
            "resolved": False,
            "confidence": 0.0,
            "mapping": None,
        }
        results.append(
            {
                "index": channel.get("index"),
                "raw_label": label,
                "baseline_pp_amplitude_uv": channel.get("baseline_pp_amplitude_uv"),
                "latest_pp_amplitude_uv": channel.get("latest_pp_amplitude_uv"),
                "resolution": resolution,
            }
        )
    return results
