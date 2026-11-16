from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from backend.app.core.config import settings


CURRENT_COORDINATE_SYSTEM = "percentage_of_full_image"
LEGACY_COORDINATE_SYSTEM = "percentage"
ADMIN_COORDINATE_SOURCE = "admin_bodymap_curation"
LEGACY_COORDINATE_SOURCE = "ionm-coords.json"

DEFAULT_CURRENT_VIEW_BOUNDS = {
    "anterior": {"x1": 31.0, "y1": 2.0, "x2": 65.0, "y2": 47.0},
    "posterior": {"x1": 31.0, "y1": 52.0, "x2": 66.0, "y2": 96.0},
}

DEFAULT_LEGACY_VIEW_BOUNDS = {
    "anterior": {"x1": 6.0, "y1": 3.0, "x2": 48.5, "y2": 97.0},
    "posterior": {"x1": 52.0, "y1": 3.0, "x2": 90.0, "y2": 97.0},
}


def load_bodymap_asset() -> dict[str, Any]:
    path = settings.project_root / "bodymap-asset.json"
    if not path.exists():
        return {"status": "missing", "asset": "body.jpg", "view_bounds": DEFAULT_CURRENT_VIEW_BOUNDS}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_seed_coordinates() -> dict[str, list[dict[str, Any]]]:
    path = settings.project_root / "ionm-coords.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_seed_coordinate_map(seed_coordinates: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, list[dict[str, Any]]]:
    coordinates = seed_coordinates if seed_coordinates is not None else load_seed_coordinates()
    normalized: dict[str, list[dict[str, Any]]] = {}
    for lat_name, pins in coordinates.items():
        document = {
            "lat_name": lat_name,
            "coordinates": pins,
            "coordinate_system": LEGACY_COORDINATE_SYSTEM,
            "source": LEGACY_COORDINATE_SOURCE,
        }
        normalized[lat_name] = normalize_coordinate_pins(document)
    return normalized


def normalize_coordinate_document(document: dict[str, Any]) -> dict[str, Any]:
    public_document = deepcopy(document)
    public_document["coordinates"] = normalize_coordinate_pins(document)
    public_document["coordinate_system"] = CURRENT_COORDINATE_SYSTEM
    if is_legacy_coordinate_document(document):
        public_document["source"] = f"{document.get('source') or LEGACY_COORDINATE_SOURCE}:normalized_to_bodymap_asset"
        public_document["normalized_from_coordinate_system"] = document.get("coordinate_system") or LEGACY_COORDINATE_SYSTEM
    return public_document


def normalize_coordinate_pins(document: dict[str, Any]) -> list[dict[str, Any]]:
    pins = document.get("coordinates") or []
    if not is_legacy_coordinate_document(document):
        return [normalize_number_fields(pin) for pin in pins]
    return [transform_legacy_pin_to_current_asset(pin) for pin in pins]


def is_legacy_coordinate_document(document: dict[str, Any]) -> bool:
    system = document.get("coordinate_system")
    source = document.get("source") or document.get("coordinate_source")
    if system == CURRENT_COORDINATE_SYSTEM or source == ADMIN_COORDINATE_SOURCE:
        return False
    return system in {None, LEGACY_COORDINATE_SYSTEM} or source == LEGACY_COORDINATE_SOURCE


def transform_legacy_pin_to_current_asset(pin: dict[str, Any]) -> dict[str, Any]:
    view = pin.get("view")
    if view not in {"anterior", "posterior"}:
        return normalize_number_fields(pin)
    legacy_bounds = legacy_view_bounds().get(view, DEFAULT_LEGACY_VIEW_BOUNDS[view])
    current_bounds = current_view_bounds().get(view, DEFAULT_CURRENT_VIEW_BOUNDS[view])
    x = transform_axis(float(pin.get("x", 0)), legacy_bounds["x1"], legacy_bounds["x2"], current_bounds["x1"], current_bounds["x2"])
    y = transform_axis(float(pin.get("y", 0)), legacy_bounds["y1"], legacy_bounds["y2"], current_bounds["y1"], current_bounds["y2"])
    transformed = {**pin, "x": round(clamp_percentage(x), 2), "y": round(clamp_percentage(y), 2)}
    transformed["normalized_from"] = LEGACY_COORDINATE_SYSTEM
    return transformed


def transform_axis(value: float, old_min: float, old_max: float, new_min: float, new_max: float) -> float:
    if old_max == old_min:
        return new_min
    fraction = (value - old_min) / (old_max - old_min)
    return new_min + fraction * (new_max - new_min)


def current_view_bounds() -> dict[str, dict[str, float]]:
    asset = load_bodymap_asset()
    bounds = asset.get("view_bounds") or {}
    return {
        "anterior": coerce_bounds(bounds.get("anterior"), DEFAULT_CURRENT_VIEW_BOUNDS["anterior"]),
        "posterior": coerce_bounds(bounds.get("posterior"), DEFAULT_CURRENT_VIEW_BOUNDS["posterior"]),
    }


def legacy_view_bounds() -> dict[str, dict[str, float]]:
    asset = load_bodymap_asset()
    bounds = asset.get("legacy_view_bounds") or {}
    return {
        "anterior": coerce_bounds(bounds.get("anterior"), DEFAULT_LEGACY_VIEW_BOUNDS["anterior"]),
        "posterior": coerce_bounds(bounds.get("posterior"), DEFAULT_LEGACY_VIEW_BOUNDS["posterior"]),
    }


def coerce_bounds(value: dict[str, Any] | None, fallback: dict[str, float]) -> dict[str, float]:
    if not value:
        return fallback
    try:
        return {key: float(value[key]) for key in ("x1", "y1", "x2", "y2")}
    except (KeyError, TypeError, ValueError):
        return fallback


def normalize_number_fields(pin: dict[str, Any]) -> dict[str, Any]:
    normalized = {**pin}
    if "x" in normalized:
        normalized["x"] = round(float(normalized["x"]), 2)
    if "y" in normalized:
        normalized["y"] = round(float(normalized["y"]), 2)
    return normalized


def clamp_percentage(value: float) -> float:
    return max(0.0, min(100.0, value))