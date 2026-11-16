import os

from pymongo import MongoClient


def main() -> None:
    uri = os.environ.get("IONM_MONGO_URI") or os.environ.get("MONGO_URI") or "mongodb://127.0.0.1:27017"
    db_name = os.environ.get("IONM_MONGO_DB") or os.environ.get("MONGO_DB") or "neuroionm_platform"
    db = MongoClient(uri)[db_name]

    ssep = db.knowledge_nodes.count_documents({"metadata.ssep_montage": {"$exists": True}})
    screw = db.knowledge_nodes.count_documents({"metadata.screw_mapping": {"$exists": True}})
    l4 = db.knowledge_nodes.find_one({"key": "live.pedicle.l4-screw"}) or {}
    median = db.knowledge_nodes.find_one({"key": "live.ssep.median-nerve"}) or {}

    print("DB:", db_name)
    print("ssep_montage nodes:", ssep)
    print("screw_mapping nodes:", screw)
    print("L4 roots:", l4.get("linked_roots"), "| muscles:", l4.get("linked_muscles"))
    print("median montage potential:", (median.get("metadata", {}).get("ssep_montage", {}) or {}).get("potential"))


if __name__ == "__main__":
    main()
