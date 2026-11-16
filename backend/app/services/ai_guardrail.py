"""Hard numeric guardrail for AI output (item #14).

Architectural rule: every clinical NUMBER the platform shows — amplitude (µV),
latency (ms), stimulation threshold (mA / V), amplitude-drop (%), and the
severity verdict — originates ONLY from the deterministic alert engine (or from
the read-only evidence/context handed to the model). The language model
*explains, prioritises and retrieves*; it must never compute or invent a value.

This module enforces that boundary after the model answers. It scans the model's
free text for clinically-dimensioned quantities and verifies each one is
*grounded* — i.e. it matches a number the deterministic engine produced or a
number already present in the read-only context the model was given. Any
clinical number that appears nowhere upstream is a fabrication and the answer is
rejected.

This is a numeric safety net, not semantic reasoning: it compares magnitudes, it
does not interpret meaning. It deliberately errs toward permissiveness on units
(matching by value) so it only fires on genuinely invented figures.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


class GuardrailViolation(ValueError):
    """Raised when AI output contains a clinical number with no upstream source."""

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        preview = "; ".join(f"{v['value']}{v['unit']}" for v in violations[:5])
        super().__init__(
            "AI response introduced clinical numbers not produced by the deterministic engine "
            f"or present in the provided context: {preview}. Rejected to preserve the "
            "engine-as-sole-source-of-truth guarantee."
        )


# A clinical magnitude is a number immediately followed by a clinical unit. Units
# that are NOT clinical values (minutes, seconds, counts, mm of position) are
# intentionally excluded so the model stays free to talk about time and anatomy.
_CLINICAL_NUMBER = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>µV|μV|uV|mV|msec|milliseconds?|ms|mA|%|volts?|V)(?!\w)",
    re.IGNORECASE,
)

# Tolerance when matching a quoted number to a grounded value. Models routinely
# round (84.7% -> "85%"), which is faithful, not fabricated.
_REL_TOLERANCE = 0.06
_ABS_TOLERANCE = 0.6


def _normalise_unit(unit: str) -> str:
    low = unit.lower()
    if low in {"µv", "μv", "uv"}:
        return "uV"
    if low == "mv":
        return "mV"
    if low in {"ms", "msec", "millisecond", "milliseconds"}:
        return "ms"
    if low == "ma":
        return "mA"
    if low in {"v", "volt", "volts"}:
        return "V"
    return unit


def _numbers_in_text(text: str) -> set[float]:
    found: set[float] = set()
    for match in re.finditer(r"-?\d+(?:\.\d+)?", text or ""):
        try:
            found.add(float(match.group(0)))
        except ValueError:
            continue
    return found


def _collect(value: Any, sink: set[float]) -> None:
    """Recursively harvest every numeric value out of an arbitrary structure."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        sink.add(float(value))
    elif isinstance(value, str):
        sink |= _numbers_in_text(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect(item, sink)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _collect(item, sink)


def grounded_values(alert: dict[str, Any] | None, *context_objects: Any) -> set[float]:
    """All numbers the model is allowed to quote: engine output + read-only context."""
    sink: set[float] = set()
    if alert is not None:
        _collect(alert.get("metrics"), sink)
        _collect(alert.get("criteria"), sink)
        _collect(alert.get("baseline"), sink)
        _collect(alert.get("current"), sink)
    for obj in context_objects:
        _collect(obj, sink)
    # Allow integer-rounded forms of every grounded value (e.g. 84.7 -> 85, 84).
    rounded: set[float] = set()
    for number in sink:
        rounded.add(round(number))
        rounded.add(float(int(number)))
    return sink | rounded


def _is_grounded(value: float, grounded: set[float]) -> bool:
    for reference in grounded:
        if abs(value - reference) <= max(_ABS_TOLERANCE, abs(reference) * _REL_TOLERANCE):
            return True
    return False


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def audit_numbers(output: Any, grounded: set[float]) -> list[dict[str, Any]]:
    """Return every clinical magnitude in `output` text not backed by `grounded`."""
    violations: list[dict[str, Any]] = []
    for text in _iter_strings(output):
        for match in _CLINICAL_NUMBER.finditer(text):
            value = float(match.group("num"))
            if _is_grounded(value, grounded):
                continue
            violations.append(
                {
                    "value": match.group("num"),
                    "unit": _normalise_unit(match.group("unit")),
                    "snippet": text[max(0, match.start() - 40): match.end() + 20].strip(),
                }
            )
    return violations


def enforce_numeric_guardrail(
    output: dict[str, Any],
    *,
    alert: dict[str, Any] | None,
    context_objects: Iterable[Any] = (),
) -> dict[str, Any]:
    """Reject AI output that quotes a clinical number with no upstream source.

    Only enforced when a deterministic `alert` is present (otherwise there is no
    engine ground truth to check against and the model is answering a general
    question from retrieved evidence). Returns `output` unchanged when clean.
    """
    if alert is None:
        return output
    grounded = grounded_values(alert, *context_objects)
    violations = audit_numbers(output, grounded)
    if violations:
        raise GuardrailViolation(violations)
    return output
