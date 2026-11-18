"""BirdNET display names plus local additions; original predictions stay unchanged."""
import json
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from storage import columns, connect


@lru_cache(maxsize=1)
def bundled_names():
    return json.loads((Path(__file__).parent / "data/species.json").read_text(encoding="utf-8"))["species"]


@lru_cache(maxsize=1)
def additional_names():
    return json.loads((Path(__file__).parent / 'data/species-extra.json').read_text(encoding='utf-8'))['species']


@lru_cache(maxsize=1)
def birdnet_species_mapping():
    data = json.loads((Path(__file__).parent / 'data/japan-birds.json').read_text(encoding='utf-8'))
    return {row['birdnet_scientific_name']: row['perch_scientific_name'] for row in data['mappings']
            if row.get('birdnet_scientific_name')}


def canonical_scientific_name(engine, label):
    scientific = str(label).partition('_')[0]
    return birdnet_species_mapping().get(scientific) if engine == 'birdnet' else scientific


@lru_cache(maxsize=1)
def ebird_codes():
    # Built by scripts/build_ebird_codes.py from the eBird taxonomy; may be empty until then.
    path = Path(__file__).parent / 'data/ebird-codes.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


@lru_cache(maxsize=1)
def aliases():
    """Other scientific names of a Japan checklist species -> the name the AIs' detections are filed under.

    Taxonomies of different years move genera (Accipiter / Astur gentilis) or split species (Parus minor /
    cinereus), so one bird can have two names. The checklist mapping (data/japan-birds.json) ties the checklist's
    and BirdNET's names to the Perch name, which the detections already use, so that name is kept.
    """
    data = json.loads((Path(__file__).parent / 'data/japan-birds.json').read_text(encoding='utf-8'))
    return {other: row['perch_scientific_name'] for row in data['mappings']
            for other in (row['scientific_name'], row.get('birdnet_scientific_name'))
            if other and other != row['perch_scientific_name']}


@lru_cache(maxsize=1)
def japan_names():
    """Scientific names of the Japan checklist birds, as the app files them."""
    data = json.loads((Path(__file__).parent / 'data/japan-birds.json').read_text(encoding='utf-8'))
    return frozenset(row['perch_scientific_name'] for row in data['mappings'])


# Tables that point at a species; an alias's rows move to the species it names.
SPECIES_COLUMNS = (('labels', 'taxon_id'), ('validation_sessions', 'species_id'), ('presence_misses', 'species_id'),
                   ('custom_models', 'species_id'), ('quiz_clips', 'species_id'), ('search_runs', 'species_id'))


def initialize_species(root):
    with connect(root) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS species (
            id TEXT PRIMARY KEY, scientific_name TEXT NOT NULL UNIQUE,
            names TEXT NOT NULL DEFAULT '{}')""")
        if 'accepted_id' not in {r['name'] for r in db.execute('PRAGMA table_info(species)')}:
            # The species this row is another name of; NULL for a species in its own right.
            db.execute('ALTER TABLE species ADD COLUMN accepted_id TEXT REFERENCES species')
        existing = {r[0] for r in db.execute("SELECT scientific_name FROM species")}
        db.executemany("INSERT INTO species(id,scientific_name) VALUES(?,?)",
                       ((str(uuid4()), name) for name in bundled_names() if name not in existing))
        ids = {r['scientific_name']: r['id'] for r in db.execute('SELECT id,scientific_name FROM species')}
        for alias, accepted in aliases().items():
            if alias not in ids or accepted not in ids:
                continue
            old, new = ids[alias], ids[accepted]
            db.execute('UPDATE species SET accepted_id=? WHERE id=? AND accepted_id IS NOT ?', (new, old, new))
            for table, column in SPECIES_COLUMNS:
                if columns(db, table):
                    db.execute(f'UPDATE {table} SET {column}=? WHERE {column}=?', (new, old))
            # Names the user gave the alias go to the species unless it already has its own.
            row = db.execute('SELECT names FROM species WHERE id=?', (old,)).fetchone()
            if row['names'] != '{}':
                merged = {**json.loads(row['names']), **json.loads(db.execute('SELECT names FROM species WHERE id=?', (new,)).fetchone()[0])}
                db.execute('UPDATE species SET names=? WHERE id=?', (json.dumps(merged, ensure_ascii=False), new))
                db.execute("UPDATE species SET names='{}' WHERE id=?", (old,))


def accepted(db, species_id):
    """The species a row stands for: itself, or the species it is another name of."""
    row = db.execute('SELECT COALESCE(accepted_id,id) FROM species WHERE id=?', (species_id,)).fetchone()
    return row[0] if row else species_id


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
        rows = db.execute("SELECT * FROM species ORDER BY scientific_name").fetchall()
        by_id = {row['id']: row for row in rows}
        best = {}
        for row in rows:
            # Another name is found by its own spelling but listed once, as the species it names.
            target = by_id.get(row['accepted_id']) or row
            names = [text.casefold() for text in [row['scientific_name'], *names_for(row)['common_names'].values()]]
            ranks = [(0 if text == query else 1 if text.startswith(query) else 2, len(text))
                     for text in names if query in text]
            if ranks and (target['id'] not in best or min(ranks) < best[target['id']][0]):
                best[target['id']] = (min(ranks), target)
        found = [(rank, names_for(row)) for rank, row in best.values()]
        # Rank before limiting so an exact match cannot be hidden by the first 50 substrings. Where two species share a
        # name (カルガモ: Anas zonorhyncha here, A. poecilorhyncha in South Asia), the Japan checklist bird comes first.
        return [record for _, record in sorted(found, key=lambda item: (
            item[0], item[1]["scientific_name"] not in japan_names(), item[1]["scientific_name"]))[:50]]


def save_species(root, scientific_name, names):
    with connect(root) as db:
        # Naming another name of a species names the species.
        alias = db.execute('SELECT s.scientific_name FROM species a JOIN species s ON s.id=a.accepted_id WHERE a.scientific_name=?',
                           (scientific_name,)).fetchone()
        scientific_name = alias[0] if alias else scientific_name
        # Only the given languages change; an empty name removes that local override and restores the bundled name.
        row = db.execute("SELECT names FROM species WHERE scientific_name=?", (scientific_name,)).fetchone()
        custom = {lang: name for lang, name in {**(json.loads(row["names"]) if row else {}), **names}.items() if name}
        db.execute("""INSERT INTO species(id,scientific_name,names) VALUES(?,?,?)
            ON CONFLICT(scientific_name) DO UPDATE SET names=excluded.names""",
                   (str(uuid4()), scientific_name, json.dumps(custom, ensure_ascii=False)))
        return names_for(db.execute("SELECT * FROM species WHERE scientific_name=?", (scientific_name,)).fetchone())


def decorate_candidates(db, engine, candidates):
    keys = {canonical_scientific_name(engine, row["label"]) for row in candidates}
    keys.discard(None)
    if not keys:
        return candidates
    placeholders = ",".join("?" for _ in keys)
    # A detection filed under another name of a species shows as that species.
    records = {row["name"]: names_for(row) for row in db.execute(
        f"""SELECT a.scientific_name AS name,s.* FROM species a JOIN species s ON s.id=COALESCE(a.accepted_id,a.id)
        WHERE a.scientific_name IN ({placeholders})""", tuple(keys))}
    for row in candidates:
        record = records.get(canonical_scientific_name(engine, row["label"]))
        row["species_id"] = record["id"] if record else None
        row["scientific_name"] = record["scientific_name"] if record else None
        row["common_names"] = record["common_names"] if record else {}
        row["name_sources"] = record["name_sources"] if record else {}
    return candidates
