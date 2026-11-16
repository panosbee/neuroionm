import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from backend.app.core.config import settings
from backend.app.db.mongo import mongo_store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export curated body-map coordinates from MongoDB to JSON.")
    parser.add_argument(
        "--output",
        default="ionm-coords-curated.json",
        help="Output JSON path. Use ionm-coords.json only after clinical review is complete.",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation level.")
    return parser


async def export_coordinates(output_path: Path, indent: int) -> dict[str, Any]:
    await mongo_store.connect()
    if not mongo_store.connected:
        raise RuntimeError(mongo_store.last_error or "MongoDB is not connected")
    try:
        database = mongo_store.db()
        records = await database.electrode_coordinates.find({}).sort("lat_name", 1).to_list(length=None)
        payload = {record["lat_name"]: record.get("coordinates", []) for record in records if record.get("lat_name")}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
        curated_count = sum(1 for record in records if record.get("curation_status") in {"reviewed", "production_curated"})
        return {"records": len(payload), "curated_records": curated_count, "output": str(output_path)}
    finally:
        await mongo_store.close()


def resolve_output(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else settings.project_root / path


async def main() -> None:
    args = build_parser().parse_args()
    summary = await export_coordinates(resolve_output(args.output), args.indent)
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
