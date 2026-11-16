r"""Translate Greek clinical content in the seed JSON into English (`_en` mirrors).

This script reads ``backend/app/seed_data/ionm_seed.json``, finds every Greek
clinical string that does not yet have an English counterpart, and asks DeepSeek
to translate it with strict preservation of numbers, units, electrode/montage
codes and Latin anatomical names. The English translations are written back into
the seed as sibling ``*_en`` fields (for scalars/lists) or parallel ``*_en``
mirror objects (for nested montage/protocol structures).

Design notes
------------
* **Auto-detection** — only strings that actually contain Greek characters are
  translated, so collections that are already English (or use bare anatomical
  codes) are left untouched.
* **Idempotent** — a document/field is skipped when its English target already
  exists, so the script can be re-run safely to retry failures.
* **Safe** — the original seed is copied to ``ionm_seed.json.bak`` before the
  first write, and the seed is version-controlled, so every change is reversible.

Run with::

    .\.venv\Scripts\python.exe -m backend.app.scripts.translate_seed_en
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from backend.app.core.config import settings
from backend.app.services.ai import DeepSeekClient, parse_json_object


# Greek + Greek Extended Unicode ranges.
GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")

# Maximum number of concurrent DeepSeek requests.
CONCURRENCY = 5

# Per-document retry attempts for transient provider failures.
MAX_ATTEMPTS = 3


# Top-level scalar / list-of-string fields to translate, per collection.
SCALAR_FIELDS: dict[str, list[str]] = {
    "knowledge_nodes": ["summary_el"],
    "procedures": ["title_el", "note"],
    "cranial_nerves": ["name_el"],
    "muscles": ["name_el", "placement", "action", "placements", "actions"],
    "protocols": ["title_el"],
    "ssep_protocols": ["indication", "stim", "notes"],
    "pedicle_roots": ["record"],
    "spinal_discs": ["disc"],
}

# Nested dict fields that are displayed as a unit and mirrored into ``*_en``.
# Each entry is ``(parent_key_or_None, field_name)``.
MIRROR_FIELDS: dict[str, list[tuple[str | None, str]]] = {
    "knowledge_nodes": [("metadata", "ssep_montage"), ("metadata", "screw_mapping")],
    "muscles": [(None, "electrode_montage")],
    "protocols": [(None, "content")],
}


SYSTEM_PROMPT = (
    "You are a senior bilingual (Greek<->English) clinical neurophysiology translator specialising "
    "in Intraoperative Neuromonitoring (IONM). Translate each Greek string into precise, professional "
    "clinical English exactly as used in operating-room neuromonitoring practice.\n"
    "STRICT RULES:\n"
    "1. Preserve EXACTLY every number, range, decimal and unit (mA, Hz, ms, us, MAC, %, cm, mm).\n"
    "2. Preserve EXACTLY every electrode / montage / potential code and symbol "
    "(e.g. CPi, CPc, Cz', Fz, Fpz, N9, N13, N20, N22, P37, P27, EPi-EPc, CV5, C5S, T12S, IC, PFi-PFc).\n"
    "3. Preserve EXACTLY Latin anatomical names, nerve and root/level labels "
    "(e.g. C8-T1, L4-L5-S1, S1-S2, Iliopsoas, palmaris longus) and abbreviations "
    "(TIVA, NMB, EMG, MEP, SSEP, BAEP, ICU, TOF, t-EMG).\n"
    "4. Preserve EXACTLY typographic symbols already present (>=, <=, ->, ., -, comma, the subscript in N2O).\n"
    "5. Do NOT translate, transliterate, reorder or alter any token that is already English or Latin.\n"
    "6. Keep the clinical meaning faithful: never add, omit, summarise or invent content.\n"
    "7. Return ONLY one valid JSON object that maps each input key to its English translation string. "
    "No markdown, no comments, no extra keys."
)


def has_greek(value: Any) -> bool:
    return isinstance(value, str) and bool(GREEK_RE.search(value))


def en_key(field: str) -> str:
    """English sibling key for a scalar/list field."""
    return field[:-3] + "_en" if field.endswith("_el") else field + "_en"


def walk_greek_leaves(node: Any, path: tuple = ()) -> list[tuple[tuple, str]]:
    """Return ``(path, text)`` for every Greek string leaf in a nested structure."""
    leaves: list[tuple[tuple, str]] = []
    if isinstance(node, str):
        if has_greek(node):
            leaves.append((path, node))
    elif isinstance(node, dict):
        for key, value in node.items():
            leaves.extend(walk_greek_leaves(value, path + (key,)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            leaves.extend(walk_greek_leaves(value, path + (index,)))
    return leaves


def set_by_path(root: Any, path: tuple, value: Any) -> None:
    cursor = root
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value


Writer = Callable[[dict[str, str]], None]


def build_job(doc: dict[str, Any], collection: str) -> tuple[dict[str, str], Callable[[dict[str, str]], None]] | None:
    """Collect the Greek strings of one document and a function to apply translations.

    Returns ``(tokens, apply_fn)`` or ``None`` when nothing needs translating.
    ``tokens`` maps a synthetic token id (``t0``, ``t1`` ...) to the Greek source.
    ``apply_fn`` receives the ``{token: english}`` map and mutates ``doc`` in place.
    """
    tokens: dict[str, str] = {}
    writers: list[Writer] = []
    counter = 0

    def new_token(text: str) -> str:
        nonlocal counter
        token = f"t{counter}"
        counter += 1
        tokens[token] = text
        return token

    # Scalar and list-of-string fields.
    for field in SCALAR_FIELDS.get(collection, []):
        if field not in doc:
            continue
        target = en_key(field)
        if doc.get(target):
            continue  # already translated
        value = doc.get(field)
        if isinstance(value, str) and has_greek(value):
            token = new_token(value)
            writers.append(lambda em, d=doc, t=target, k=token: d.__setitem__(t, em[k]))
        elif isinstance(value, list) and any(has_greek(item) for item in value):
            positions = [(index, new_token(item)) for index, item in enumerate(value) if has_greek(item)]

            def make_list_writer(d=doc, t=target, src=value, locs=positions) -> Writer:
                def writer(em: dict[str, str]) -> None:
                    out = list(src)
                    for index, token in locs:
                        out[index] = em[token]
                    d[t] = out

                return writer

            writers.append(make_list_writer())

    # Nested dict mirrors.
    for parent, field in MIRROR_FIELDS.get(collection, []):
        container = doc.get(parent) if parent else doc
        if not isinstance(container, dict):
            continue
        source = container.get(field)
        target = field + "_en"
        if container.get(target):
            continue  # already translated
        if not isinstance(source, dict):
            continue
        leaves = walk_greek_leaves(source)
        if not leaves:
            continue
        path_tokens = [(path, new_token(text)) for path, text in leaves]

        def make_mirror_writer(c=container, t=target, src=source, pts=path_tokens) -> Writer:
            def writer(em: dict[str, str]) -> None:
                mirror = copy.deepcopy(src)
                for path, token in pts:
                    set_by_path(mirror, path, em[token])
                c[t] = mirror

            return writer

        writers.append(make_mirror_writer())

    if not tokens:
        return None

    def apply_fn(em: dict[str, str]) -> None:
        for writer in writers:
            writer(em)

    return tokens, apply_fn


async def translate_tokens(client: DeepSeekClient, tokens: dict[str, str]) -> dict[str, str]:
    user_prompt = {"translate_each_value_to_english": tokens}
    data = await client.chat_completion(SYSTEM_PROMPT, user_prompt, temperature=0.0)
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    result = parse_json_object(content)
    missing = [key for key in tokens if key not in result]
    if missing:
        raise ValueError(f"missing {len(missing)} of {len(tokens)} translations")
    return {key: str(result[key]) for key in tokens}


async def main() -> None:
    seed_path: Path = settings.seed_path
    backup_path = seed_path.with_name(seed_path.name + ".bak")
    if not backup_path.exists():
        shutil.copy2(seed_path, backup_path)
        print(f"Backup written: {backup_path}")

    seed: dict[str, Any] = json.loads(seed_path.read_text(encoding="utf-8"))

    client = DeepSeekClient()
    client.ensure_configured()

    jobs: list[tuple[str, str, dict[str, str], Callable[[dict[str, str]], None]]] = []
    for collection in sorted(set(SCALAR_FIELDS) | set(MIRROR_FIELDS)):
        for doc in seed.get(collection, []):
            if not isinstance(doc, dict):
                continue
            job = build_job(doc, collection)
            if job is None:
                continue
            tokens, apply_fn = job
            key = doc.get("key") or doc.get("lat_name") or doc.get("module_key") or "?"
            jobs.append((collection, key, tokens, apply_fn))

    total_strings = sum(len(tokens) for _, _, tokens, _ in jobs)
    print(f"Documents needing translation: {len(jobs)} | strings: {total_strings}")
    if not jobs:
        print("Nothing to translate. Seed is already fully bilingual.")
        return

    semaphore = asyncio.Semaphore(CONCURRENCY)
    results: dict[int, dict[str, str]] = {}
    errors: list[tuple[str, str, str]] = []

    async def worker(index: int, collection: str, key: str, tokens: dict[str, str]) -> None:
        async with semaphore:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    results[index] = await translate_tokens(client, tokens)
                    print(f"  ok  {collection}/{key} ({len(tokens)})")
                    return
                except Exception as error:  # noqa: BLE001 - report and retry
                    if attempt < MAX_ATTEMPTS:
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    errors.append((collection, key, str(error)))
                    print(f"  FAIL {collection}/{key}: {error}")

    await asyncio.gather(
        *(worker(index, collection, key, tokens) for index, (collection, key, tokens, _) in enumerate(jobs))
    )

    applied = 0
    for index, (_, _, _, apply_fn) in enumerate(jobs):
        translations = results.get(index)
        if translations is not None:
            apply_fn(translations)
            applied += 1

    seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Applied {applied}/{len(jobs)} documents | errors: {len(errors)} | saved: {seed_path}")
    if errors:
        print("Some documents failed; re-run the script to retry them (idempotent).")


if __name__ == "__main__":
    asyncio.run(main())
