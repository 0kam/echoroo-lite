"""BirdNET display names plus local additions; original predictions stay unchanged."""
import json
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from storage import connect


@lru_cache(maxsize=1)
def bundled_names():
    return json.loads((Path(__file__).parent / "data/species.json").read_text(encoding="utf-8"))["species"]


@lru_cache(maxsize=1)
def additional_names():
    return json.loads((Path(__file__).parent / 'data/species-extra.json').read_text(encoding='utf-8'))['species']


@lru_cache(maxsize=1)
def ebird_codes():
    # Built by scripts/build_ebird_codes.py from the eBird taxonomy; may be empty until then.
    path = Path(__file__).parent / 'data/ebird-codes.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def initialize_species(root):
    with connect(root) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS species (
            id TEXT PRIMARY KEY, scientific_name TEXT NOT NULL UNIQUE,
            names TEXT NOT NULL DEFAULT '{}')""")
        existing = {r[0] for r in db.execute("SELECT scientific_name FROM species")}
        db.executemany("INSERT INTO species(id,scientific_name) VALUES(?,?)",
                       ((str(uuid4()), name) for name in bundled_names() if name not in existing))


def names_for(row):
    custom = json.loads(row["names"])
    bundled = bundled_names().get(row["scientific_name"], {})
    defaults = {**additional_names().get(row['scientific_name'], {}), **bundled}
    return {"id": row["id"], "scientific_name": row["scientific_name"],
            "common_names": {**defaults, **custom}, "custom_names": custom,
            "name_sources": {lang: "user" if lang in custom else "bundled" if lang in bundled else 'quiz' for lang in defaults.keys() | custom.keys()}}


def list_species(root, query):
    query = query.strip().casefold()
    if not query:
        return []
    # ponytail: a 16k-name in-memory scan; add an FTS index if the catalog grows substantially.
    with connect(root) as db:
        found = []
        for row in db.execute("SELECT * FROM species ORDER BY scientific_name"):
            record = names_for(row)
            names = [text.casefold() for text in [record["scientific_name"], *record["common_names"].values()]]
            ranks = [(0 if text == query else 1 if text.startswith(query) else 2, len(text))
                     for text in names if query in text]
            if ranks:
                found.append((min(ranks), record))
        # Rank before limiting so an exact match cannot be hidden by the first 50 substrings.
        return [record for _, record in sorted(found, key=lambda item: (item[0], item[1]["scientific_name"]))[:50]]


def save_species(root, scientific_name, names):
    with connect(root) as db:
        # Empty fields remove local overrides and restore the bundled name.
        custom = {lang: name for lang, name in names.items() if name}
        db.execute("""INSERT INTO species(id,scientific_name,names) VALUES(?,?,?)
            ON CONFLICT(scientific_name) DO UPDATE SET names=excluded.names""",
                   (str(uuid4()), scientific_name, json.dumps(custom, ensure_ascii=False)))
        return names_for(db.execute("SELECT * FROM species WHERE scientific_name=?", (scientific_name,)).fetchone())


def decorate_candidates(db, engine, candidates):
    def scientific(label):
        return label.partition("_")[0] if engine == "birdnet" else label

    keys = {scientific(row["label"]) for row in candidates}
    if not keys:
        return candidates
    placeholders = ",".join("?" for _ in keys)
    records = {row["scientific_name"]: names_for(row) for row in db.execute(
        f"SELECT * FROM species WHERE scientific_name IN ({placeholders})", tuple(keys))}
    for row in candidates:
        record = records.get(scientific(row["label"]))
        row["species_id"] = record["id"] if record else None
        row["scientific_name"] = record["scientific_name"] if record else None
        row["common_names"] = record["common_names"] if record else {}
        row["name_sources"] = record["name_sources"] if record else {}
    return candidates
