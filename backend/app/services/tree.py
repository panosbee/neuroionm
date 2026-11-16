from typing import Any


def to_public_document(document: dict[str, Any]) -> dict[str, Any]:
    clean = dict(document)
    clean.pop("_id", None)
    return clean


def build_tree(flat_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = [to_public_document(node) for node in flat_nodes]
    by_key = {node["key"]: {**node, "children": []} for node in nodes}
    roots: list[dict[str, Any]] = []

    for node in sorted(by_key.values(), key=lambda item: (item.get("sort_order", 0), item.get("title_el", ""))):
        parent_key = node.get("parent_key")
        if parent_key and parent_key in by_key:
            by_key[parent_key]["children"].append(node)
        else:
            roots.append(node)

    def sort_children(item: dict[str, Any]) -> None:
        item["children"].sort(key=lambda child: (child.get("sort_order", 0), child.get("title_el", "")))
        for child in item["children"]:
            sort_children(child)

    for root in roots:
        sort_children(root)
    return roots
