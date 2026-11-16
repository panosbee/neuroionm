"""Model routing for the unified clinical copilot (#13).

The platform can reason with two interchangeable, OpenAI-compatible chat models:

* **DeepSeek** — the external high-capability API used for general,
  non-identifiable clinical reasoning.
* **Local EU model** — an in-region (Hetzner/EU) OpenAI-compatible endpoint
  (e.g. a TinyLlama/BioBERT or Llama service). When configured, it keeps any
  request that carries patient-identifiable context (PHI) inside the EU data
  boundary instead of sending it to the external API.

This module is the single deterministic decision point. It is pure (no network,
no I/O) so the routing policy is fully unit-testable. The standing product rule
is unchanged: every model is advisory only — it proposes, the human confirms and
seals, and numbers come solely from the deterministic engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.core.config import settings


# Focus modes for the unified copilot. Each assembles a different slice of
# context and steers the model with a focused directive, but all share the same
# advisory-only guardrails.
FOCUS_MODES: tuple[str, ...] = ("setup", "signal", "evidence")

FOCUS_DIRECTIVES: dict[str, str] = {
    "setup": (
        "FOCUS — PRE-INCISION SETUP. Concentrate on montage completeness, electrode "
        "placement, stimulation parameters, the order of baseline acquisition and "
        "protocol selection for the selected procedure. Surface missing setup steps "
        "before they become intra-operative gaps."
    ),
    "signal": (
        "FOCUS — INTRA-OPERATIVE SIGNAL. Concentrate on interpreting amplitude and "
        "latency trends against the confirmed baseline, distinguishing artifact from "
        "true change, and the timing and wording of surgeon communication. Never state "
        "a numeric threshold the deterministic engine has not already computed."
    ),
    "evidence": (
        "FOCUS — EVIDENCE. Concentrate on the retrieved knowledge-base and PubMed "
        "evidence provided. Weigh the strength of each source, cite the most relevant "
        "titles briefly, and explicitly flag where the evidence is thin or missing."
    ),
}

# Context keys whose presence means the payload may carry patient-identifiable
# information and must therefore stay in-region when a local model is available.
PHI_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"case_id", "case", "patient", "patient_id", "mrn", "timeline", "case_timeline", "events"}
)

PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_LOCAL_EU = "local-eu"


class ModelRoutingError(RuntimeError):
    """Raised when a request cannot be routed without violating the PHI policy."""


@dataclass(frozen=True)
class ModelRoute:
    """A resolved target for one chat completion.

    `provider` identifies which backend was chosen, `reason` records *why* so the
    decision is auditable, and `base_url`/`model`/`api_key` are the concrete
    OpenAI-compatible connection parameters the client will use.
    """

    provider: str
    base_url: str
    model: str
    api_key: str | None
    contains_phi: bool
    reason: str

    @property
    def is_local(self) -> bool:
        return self.provider == PROVIDER_LOCAL_EU

    def public(self) -> dict[str, object]:
        """Audit-safe view (never exposes the API key)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "contains_phi": self.contains_phi,
            "reason": self.reason,
        }


def normalize_focus(focus: str | None) -> str:
    """Coerce an incoming focus value to a known mode (defaults to ``setup``)."""
    value = (focus or "setup").strip().lower()
    if value not in FOCUS_MODES:
        raise ModelRoutingError(f"Unknown focus mode: {focus!r}. Expected one of {', '.join(FOCUS_MODES)}.")
    return value


def focus_directive(focus: str | None) -> str:
    """Return the system-prompt directive for a focus mode."""
    return FOCUS_DIRECTIVES[normalize_focus(focus)]


def context_contains_phi(context: object) -> bool:
    """Heuristic-free structural check: does this context carry PHI-bearing keys?

    We do not scan free text with regex/keywords; we look only at the structural
    presence of known patient-identifiable context keys (e.g. an attached case
    timeline). A non-empty value under any such key marks the request as PHI.
    """
    if not isinstance(context, dict):
        return False
    for key in PHI_CONTEXT_KEYS:
        value = context.get(key)
        if value not in (None, "", [], {}):
            return True
    return False


def _local_available() -> bool:
    return bool(settings.local_model_enabled and settings.local_model_base_url)


def _local_route(*, contains_phi: bool, reason: str) -> ModelRoute:
    return ModelRoute(
        provider=PROVIDER_LOCAL_EU,
        base_url=str(settings.local_model_base_url).rstrip("/"),
        model=settings.local_model_name,
        api_key=settings.local_model_api_key,
        contains_phi=contains_phi,
        reason=reason,
    )


def _deepseek_route(*, contains_phi: bool, reason: str) -> ModelRoute:
    return ModelRoute(
        provider=PROVIDER_DEEPSEEK,
        base_url=settings.deepseek_base_url.rstrip("/"),
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        contains_phi=contains_phi,
        reason=reason,
    )


def select_route(*, contains_phi: bool) -> ModelRoute:
    """Pick the model backend for a request, deterministically.

    Policy:
      1. PHI present and a local EU model is available → local model (PHI stays
         in-region).
      2. PHI present, no local model, and ``IONM_PHI_ROUTING_REQUIRED`` is set →
         refuse (do not send PHI to the external API).
      3. PHI present, no local model, requirement not set → external API, flagged.
      4. No PHI → external API (best general reasoning).
    """
    if contains_phi:
        if _local_available():
            return _local_route(contains_phi=True, reason="phi_kept_in_region")
        if settings.phi_routing_required:
            raise ModelRoutingError(
                "PHI present but no in-region model is configured and PHI routing is "
                "required; refusing to route patient-identifiable context to the "
                "external API."
            )
        return _deepseek_route(contains_phi=True, reason="phi_no_local_model_available")
    return _deepseek_route(contains_phi=False, reason="no_phi_external_ok")


def route_for_context(context: object) -> ModelRoute:
    """Convenience: derive PHI from the context structure and select a route."""
    return select_route(contains_phi=context_contains_phi(context))
