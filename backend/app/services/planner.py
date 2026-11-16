from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.services.bodymap_coordinates import CURRENT_COORDINATE_SYSTEM, normalize_coordinate_pins
from backend.app.services.clinical_planner import build_clinical_plan
from backend.app.services.tree import to_public_document


class KnowledgeNodeNotFound(LookupError):
    pass


async def build_planner(database: AsyncIOMotorDatabase, node_key: str) -> dict[str, Any]:
    node = await database.knowledge_nodes.find_one({"key": node_key})
    if not node:
        raise KnowledgeNodeNotFound(node_key)

    linked_muscles = node.get("linked_muscles", [])
    linked_roots = node.get("linked_roots", [])
    linked_cns = node.get("linked_cns", [])
    child_count = await database.knowledge_nodes.count_documents({"parent_key": node_key, "status": "active"})

    muscles = []
    if linked_muscles:
        muscle_documents = await database.muscles.find({"lat_name": {"$in": linked_muscles}}).to_list(length=None)
        muscle_lookup = {item["lat_name"]: item for item in muscle_documents}
        muscles = [muscle_lookup[muscle_name] for muscle_name in linked_muscles if muscle_name in muscle_lookup]

    coordinates = []
    if linked_muscles:
        coordinates = await database.electrode_coordinates.find({"lat_name": {"$in": linked_muscles}}).to_list(length=None)

    protocols = await resolve_protocols(database, node)

    nerve_roots = []
    if linked_roots:
        nerve_roots = await database.nerve_roots.find({"root": {"$in": linked_roots}}).to_list(length=None)

    cranial_nerves = []
    if linked_cns:
        cranial_nerves = await database.cranial_nerves.find({"key": {"$in": linked_cns}}).to_list(length=None)

    clinical_plan = build_clinical_plan(
        node=node,
        child_count=child_count,
        linked_roots=linked_roots,
        linked_cns=linked_cns,
        linked_muscles=linked_muscles,
        muscles=muscles,
        coordinates=coordinates,
        protocols=protocols,
        nerve_roots=nerve_roots,
        cranial_nerves=cranial_nerves,
    )

    pins = build_pins(node, muscles, coordinates, clinical_plan)

    return {
        "node": to_public_document(node),
        "roots": linked_roots,
        "cranial_nerves": linked_cns,
        "muscles": [to_public_document(item) for item in muscles],
        "pins": pins,
        "protocols": [to_public_document(item) for item in protocols],
        "clinical_plan": clinical_plan,
    }


async def resolve_protocols(database: AsyncIOMotorDatabase, node: dict[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_link(key: str, link_type: str, source_node: dict[str, Any] | None = None) -> None:
        if not key or key in seen:
            return
        seen.add(key)
        links.append(
            {
                "key": key,
                "link_type": link_type,
                "linked_from_node_key": source_node.get("key") if source_node else node.get("key"),
                "linked_from_title_el": source_node.get("title_el") if source_node else node.get("title_el"),
            }
        )

    for key in node.get("linked_protocols", []):
        add_link(key, "direct", node)

    parent_key = node.get("parent_key")
    while parent_key:
        parent = await database.knowledge_nodes.find_one({"key": parent_key})
        if not parent:
            break
        for key in parent.get("linked_protocols", []):
            add_link(key, "inherited", parent)
        parent_key = parent.get("parent_key")

    protocols: list[dict[str, Any]] = []
    if links:
        protocol_documents = await database.protocols.find({"key": {"$in": [link["key"] for link in links]}}).to_list(length=None)
        protocol_lookup = {item["key"]: item for item in protocol_documents}
        for link in links:
            protocol = protocol_lookup.get(link["key"])
            if protocol:
                protocols.append({**protocol, **link})

    domain = node.get("domain")
    node_type = node.get("node_type")
    if domain:
        default_protocols = await database.protocols.find({"default_for_domains": domain}).sort([("sort_order", 1), ("key", 1)]).to_list(length=None)
        for protocol in default_protocols:
            key = protocol.get("key")
            if not key or key in seen:
                continue
            default_node_types = protocol.get("default_for_node_types") or []
            if default_node_types and node_type not in default_node_types:
                continue
            excluded_node_types = protocol.get("excluded_node_types") or []
            if node_type in excluded_node_types:
                continue
            seen.add(key)
            protocols.append(
                {
                    **protocol,
                    "link_type": "domain_default",
                    "linked_from_node_key": node.get("key"),
                    "linked_from_title_el": node.get("title_el"),
                }
            )

    return protocols


def build_pins(
    node: dict[str, Any],
    muscles: list[dict[str, Any]],
    coordinates: list[dict[str, Any]],
    clinical_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    pins = []
    muscle_lookup = {item["lat_name"]: item for item in muscles}
    channel_lookup = {item["lat_name"]: item for item in clinical_plan.get("channel_plan", [])}
    for coordinate in coordinates:
        muscle = muscle_lookup.get(coordinate["lat_name"], {})
        channel = channel_lookup.get(coordinate["lat_name"], {})
        normalized_pins = normalize_coordinate_pins(coordinate)
        for pin in normalized_pins:
            pins.append(
                {
                    "target_id": build_target_id(coordinate["lat_name"], pin),
                    "lat_name": coordinate["lat_name"],
                    "name_el": muscle.get("name_el", coordinate["lat_name"]),
                    "name_en": muscle.get("name_en"),
                    "node_key": node.get("key"),
                    "node_title_el": node.get("title_el"),
                    "node_title_en": node.get("title_en"),
                    "node_domain": node.get("domain"),
                    "placement": muscle.get("placement"),
                    "placement_en": muscle.get("placement_en"),
                    "action": muscle.get("action"),
                    "action_en": muscle.get("action_en"),
                    "electrode_montage": muscle.get("electrode_montage"),
                    "electrode_montage_en": muscle.get("electrode_montage_en"),
                    "roots": channel.get("roots", []),
                    "cranial_nerves": channel.get("cranial_nerves", []),
                    "coordinate_status": channel.get("coordinate_status"),
                    "x": pin.get("x"),
                    "y": pin.get("y"),
                    "side": pin.get("side"),
                    "view": pin.get("view"),
                    "coordinate_system": CURRENT_COORDINATE_SYSTEM,
                    "coordinate_source": pin.get("normalized_from") and f"{coordinate.get('source', 'legacy')}:normalized" or coordinate.get("source") or coordinate.get("coordinate_source"),
                    "image": coordinate.get("image"),
                }
            )
    return pins


def build_target_id(lat_name: str, pin: dict[str, Any]) -> str:
    return f"{lat_name}|{pin.get('side')}|{pin.get('view')}"