from collections import defaultdict
from typing import Any


def build_clinical_plan(
    *,
    node: dict[str, Any],
    child_count: int,
    linked_roots: list[str],
    linked_cns: list[str],
    linked_muscles: list[str],
    muscles: list[dict[str, Any]],
    coordinates: list[dict[str, Any]],
    protocols: list[dict[str, Any]],
    nerve_roots: list[dict[str, Any]],
    cranial_nerves: list[dict[str, Any]],
) -> dict[str, Any]:
    muscle_lookup = {muscle.get("lat_name"): muscle for muscle in muscles}
    coordinates_lookup = {item.get("lat_name"): item for item in coordinates}
    root_to_muscles = _build_root_to_muscles(linked_roots, nerve_roots, linked_muscles)
    cn_to_muscles = _build_cn_to_muscles(linked_cns, cranial_nerves, linked_muscles)
    muscle_to_roots = _invert_mapping(root_to_muscles)
    muscle_to_cns = _invert_mapping(cn_to_muscles)

    channel_plan = []
    mapped_muscles = []
    unmapped_muscles = []
    bilateral_muscles = []
    all_sources = set(node.get("source_ids", []))

    for muscle_name in linked_muscles:
        muscle = muscle_lookup.get(muscle_name, {"lat_name": muscle_name})
        coordinate_doc = coordinates_lookup.get(muscle_name, {})
        pins = coordinate_doc.get("coordinates", [])
        sides = _unique(pin.get("side") for pin in pins)
        views = _unique(pin.get("view") for pin in pins)
        pin_count = len(pins)
        coordinate_status = _coordinate_status(sides, pin_count)

        if pin_count:
            mapped_muscles.append(muscle_name)
        else:
            unmapped_muscles.append(muscle_name)
        if set(sides) >= {"L", "R"}:
            bilateral_muscles.append(muscle_name)

        _collect_source(all_sources, muscle.get("source"))
        _collect_source(all_sources, coordinate_doc.get("source"))

        channel_plan.append(
            {
                "lat_name": muscle_name,
                "name_el": muscle.get("name_el") or muscle_name,
                "name_en": muscle.get("name_en"),
                "placement": muscle.get("placement"),
                "placement_en": muscle.get("placement_en"),
                "action": muscle.get("action"),
                "action_en": muscle.get("action_en"),
                "roots": muscle_to_roots.get(muscle_name, []),
                "cranial_nerves": muscle_to_cns.get(muscle_name, []),
                "sides": sides,
                "views": views,
                "pin_count": pin_count,
                "coordinate_status": coordinate_status,
            }
        )

    for protocol in protocols:
        _collect_source(all_sources, protocol.get("source"))
    for root_doc in nerve_roots:
        _collect_source(all_sources, root_doc.get("source"))
    for cn_doc in cranial_nerves:
        _collect_source(all_sources, cn_doc.get("source"))

    gaps = _knowledge_gaps(
        node=node,
        child_count=child_count,
        linked_muscles=linked_muscles,
        unmapped_muscles=unmapped_muscles,
        protocols=protocols,
    )
    readiness = _readiness_status(
        child_count=child_count,
        linked_muscles=linked_muscles,
        protocols=protocols,
        gaps=gaps,
    )

    return {
        "readiness": readiness,
        "coverage": {
            "roots": [
                {
                    "root": root,
                    "muscles": root_to_muscles.get(root, []),
                    "muscle_count": len(root_to_muscles.get(root, [])),
                    "status": "covered" if root_to_muscles.get(root) else "gap",
                }
                for root in linked_roots
            ],
            "cranial_nerves": [
                {
                    "cranial_nerve": cranial_nerve,
                    "muscles": cn_to_muscles.get(cranial_nerve, []),
                    "muscle_count": len(cn_to_muscles.get(cranial_nerve, [])),
                    "status": "covered" if cn_to_muscles.get(cranial_nerve) else "gap",
                }
                for cranial_nerve in linked_cns
            ],
            "muscle_count": len(linked_muscles),
            "mapped_muscle_count": len(mapped_muscles),
            "bilateral_muscle_count": len(bilateral_muscles),
            "pin_count": sum(item["pin_count"] for item in channel_plan),
            "protocol_count": len(protocols),
        },
        "channel_plan": channel_plan,
        "ssep_plan": build_ssep_plan(node),
        "gaps": gaps,
        "traceability": {
            "sources": sorted(source for source in all_sources if source),
            "derived_from": ["knowledge_nodes", "nerve_roots", "cranial_nerves", "muscles", "electrode_coordinates", "protocols"],
        },
    }


SSEP_GENERATORS: dict[str, str] = {
    "N9": "peripheral",
    "EP": "peripheral",
    "EPI": "peripheral",
    "N8": "peripheral",
    "PF": "peripheral",
    "POP": "peripheral",
    "N13": "subcortical",
    "P14": "subcortical",
    "N18": "subcortical",
    "N22": "subcortical",
    "P31": "subcortical",
    "N34": "subcortical",
    "LP": "subcortical",
    "N20": "cortical",
    "P22": "cortical",
    "P37": "cortical",
    "N45": "cortical",
}


def classify_ssep_channel(name: str | None) -> str:
    """Map an SSEP channel to its neural generator level deterministically.

    N9/EP = peripheral (Erb / popliteal), N13/P14/N18/N22/P31/N34 = subcortical
    (cervical cord, brainstem, lumbar/spinal lemniscal generators), N20/P37/N45 =
    cortical primary somatosensory cortex. This is fixed neurophysiology encoded
    as a deterministic lookup, not a heuristic re-interpretation of the montage.
    """
    if not name:
        return "unclassified"
    head = name.strip().upper().split("(")[0].split(" ")[0].split("–")[0].split("-")[0].strip()
    return SSEP_GENERATORS.get(head, "unclassified")


def build_ssep_plan(node: dict[str, Any]) -> dict[str, Any] | None:
    """Surface the SSEP montage stored on a knowledge node into the clinical plan.

    Splits the recorded potentials into peripheral / subcortical / cortical
    generators so the planner exposes the full cortical-and-subcortical SSEP
    picture (N9 -> N13 -> N20 for upper limb, popliteal -> N22 -> P37 for lower
    limb) instead of leaving the montage buried in node metadata.
    """
    metadata = node.get("metadata") or {}
    montage = metadata.get("ssep_montage")
    if not isinstance(montage, dict):
        return None
    montage_en = metadata.get("ssep_montage_en") if isinstance(metadata.get("ssep_montage_en"), dict) else {}

    generators: dict[str, list[str]] = {"peripheral": [], "subcortical": [], "cortical": [], "unclassified": []}
    classified_channels = []
    for channel in montage.get("channels") or []:
        name = channel.get("name")
        generator = classify_ssep_channel(name)
        generators.setdefault(generator, []).append(name)
        classified_channels.append({**channel, "generator": generator})

    return {
        "potential": montage.get("potential"),
        "indication": montage.get("indication"),
        "indication_en": montage_en.get("indication"),
        "stimulation": montage.get("stimulation"),
        "channels": classified_channels,
        "channels_en": montage_en.get("channels") or [],
        "scalp_positions": montage.get("scalp_positions") or [],
        "ground": montage.get("ground"),
        "anesthesia": montage.get("anesthesia"),
        "warning": montage.get("warning"),
        "generators": {
            "peripheral": generators["peripheral"],
            "subcortical": generators["subcortical"],
            "cortical": generators["cortical"],
        },
        "has_cortical": bool(generators["cortical"]),
        "has_subcortical": bool(generators["subcortical"]),
        "has_peripheral": bool(generators["peripheral"]),
    }


def _build_root_to_muscles(
    linked_roots: list[str],
    nerve_roots: list[dict[str, Any]],
    linked_muscles: list[str],
) -> dict[str, list[str]]:
    linked_set = set(linked_muscles)
    mapping: dict[str, list[str]] = {}
    root_docs = {item.get("root"): item for item in nerve_roots}
    for root in linked_roots:
        root_muscles = root_docs.get(root, {}).get("muscles", [])
        mapping[root] = [muscle for muscle in root_muscles if muscle in linked_set]
    return mapping


def _build_cn_to_muscles(
    linked_cns: list[str],
    cranial_nerves: list[dict[str, Any]],
    linked_muscles: list[str],
) -> dict[str, list[str]]:
    linked_set = set(linked_muscles)
    mapping: dict[str, list[str]] = {}
    cn_docs = {item.get("key"): item for item in cranial_nerves}
    for cranial_nerve in linked_cns:
        cn_muscles = cn_docs.get(cranial_nerve, {}).get("muscles", [])
        mapping[cranial_nerve] = [muscle for muscle in cn_muscles if muscle in linked_set]
    return mapping


def _invert_mapping(mapping: dict[str, list[str]]) -> dict[str, list[str]]:
    inverted: dict[str, list[str]] = defaultdict(list)
    for key, values in mapping.items():
        for value in values:
            inverted[value].append(key)
    return dict(inverted)


def _coordinate_status(sides: list[str], pin_count: int) -> str:
    if not pin_count:
        return "missing"
    if set(sides) >= {"L", "R"}:
        return "bilateral"
    if sides == ["C"]:
        return "central"
    return "partial"


def _knowledge_gaps(
    *,
    node: dict[str, Any],
    child_count: int,
    linked_muscles: list[str],
    unmapped_muscles: list[str],
    protocols: list[dict[str, Any]],
) -> list[dict[str, str]]:
    gaps = []
    if linked_muscles and unmapped_muscles:
        gaps.append(
            {
                "severity": "action",
                "title": "Missing body-map coordinates",
                "detail": ", ".join(unmapped_muscles),
            }
        )
    if _expects_protocol(node, child_count) and not protocols:
        gaps.append(
            {
                "severity": "review",
                "title": "No protocol document linked",
                "detail": "This node has anatomy/channel data but no dedicated protocol entry in the current knowledge base.",
            }
        )
    if not child_count and not linked_muscles and not protocols:
        gaps.append(
            {
                "severity": "review",
                "title": "Sparse terminal node",
                "detail": "No child nodes, channel plan, or protocol document are linked in the current knowledge base.",
            }
        )
    return gaps


def _expects_protocol(node: dict[str, Any], child_count: int) -> bool:
    if child_count:
        return False
    return bool(node.get("linked_roots") or node.get("linked_cns") or node.get("linked_muscles"))


def _readiness_status(
    *,
    child_count: int,
    linked_muscles: list[str],
    protocols: list[dict[str, Any]],
    gaps: list[dict[str, str]],
) -> dict[str, str]:
    if child_count and not linked_muscles and not protocols:
        return {
            "state": "navigation",
            "label": "Navigation node",
            "detail": "Use the child nodes to reach a procedure, root, nerve, or protocol-level plan.",
        }
    if any(gap["severity"] == "action" for gap in gaps):
        return {
            "state": "action_needed",
            "label": "Action needed",
            "detail": "The plan has linked data, but a required mapping is incomplete.",
        }
    if gaps:
        return {
            "state": "review",
            "label": "Clinical review",
            "detail": "The node is usable, but the knowledge base exposes items that should be reviewed before deployment use.",
        }
    return {
        "state": "ready",
        "label": "Setup-ready",
        "detail": "Linked anatomy, channels, coordinates, and protocols are consistent for the current knowledge base.",
    }


def _unique(values: Any) -> list[str]:
    output = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _collect_source(sources: set[str], source: str | None) -> None:
    if source:
        sources.add(source)