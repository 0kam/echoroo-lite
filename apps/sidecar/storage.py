"""Local catalog. Source audio is referenced, never modified."""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import soundfile as sf
import sqlite_vec

import labels


# Increment whenever the SQLite schema changes.
SCHEMA_VERSION = 8
MIGRATABLE_VERSIONS = (2, 3, 4, 5, 6, 7)


class CatalogError(Exception):
    pass


@contextmanager
def connect(root):
    db = sqlite3.connect(Path(root) / "echoroo.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    # Each vector read walks its 6 MiB vec0 chunk page by page; a 2 MiB default cache rereads it
    # from the OS every time. Pages are only allocated when read and freed on close.
    db.execute("PRAGMA cache_size=-65536")
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    stale_explore_files = []
    with connect(root) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        has_schema = db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') LIMIT 1").fetchone()
        supported = (version == SCHEMA_VERSION or version in MIGRATABLE_VERSIONS and has_schema
                     or version == 0 and not has_schema)
        if not supported:
            return False
        db.execute("PRAGMA journal_mode=WAL")
        if version in MIGRATABLE_VERSIONS and version < 5:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='explore_runs'").fetchone():
                for row in db.execute('SELECT params FROM explore_runs'):
                    try:
                        params = json.loads(row['params'])
                    except (TypeError, ValueError):
                        continue
                    for key in ('points_file', 'pca_file'):
                        name = params.get(key)
                        if isinstance(name, str) and Path(name).name == name:
                            stale_explore_files.append(name)
            db.executescript('''
                DROP TABLE IF EXISTS explore_clusters;
                DROP TABLE IF EXISTS explore_nodes;
                DROP TABLE IF EXISTS explore_points;
                DROP TABLE IF EXISTS explore_runs;
            ''')
        # Schema 7: labels belong to a species instead of a search, and a species AI needs no search.
        # The old tables are renamed, recreated below and copied back, so a 0.2.0 catalogue keeps its rows.
        if 'run_id' in columns(db, 'model_labels'):
            db.execute('ALTER TABLE model_labels RENAME TO model_labels_v6')
        # Schema 8: the sound map's species move to labels; the pick keeps only what its export needs.
        if 'species' in columns(db, 'picked_sounds'):
            db.execute('ALTER TABLE picked_sounds RENAME TO picked_sounds_v7')
        if columns(db, 'custom_models').get('run_id'):
            db.execute('ALTER TABLE custom_models RENAME TO custom_models_v6')
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL,
                latitude REAL NOT NULL, longitude REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS datasets (
                id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites,
                name TEXT NOT NULL, folder TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recordings (
                id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets,
                path TEXT NOT NULL UNIQUE, original_time TEXT NOT NULL,
                timezone TEXT NOT NULL, recorded_at TEXT NOT NULL,
                seconds REAL NOT NULL, sample_rate INTEGER NOT NULL,
                size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL);
            -- A sound picked on the sound map, with what only its export needs; its species are the answers recorded on it (labels).
            CREATE TABLE IF NOT EXISTS picked_sounds (
                recording_id INTEGER NOT NULL REFERENCES recordings ON DELETE CASCADE,
                start_s REAL NOT NULL, end_s REAL NOT NULL,
                sound_type TEXT NOT NULL DEFAULT '' CHECK(sound_type IN ('','song','call')),
                quality TEXT NOT NULL DEFAULT '' CHECK(quality IN ('','A','B','C','D','E')),
                others INTEGER NOT NULL DEFAULT 0, exported_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(recording_id,start_s,end_s));
            CREATE VIEW IF NOT EXISTS site_recordings AS
                SELECT anchor.id AS scope_id, r.* FROM datasets anchor
                JOIN datasets member ON member.site_id=anchor.site_id
                JOIN recordings r ON r.dataset_id=member.id;
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets,
                status TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL, stage TEXT NOT NULL DEFAULT 'queued',
                error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs,
                recording_id INTEGER NOT NULL REFERENCES recordings,
                engine TEXT NOT NULL, metadata TEXT NOT NULL, scores_path TEXT NOT NULL,
                UNIQUE(job_id, recording_id, engine));
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY, analysis_id INTEGER NOT NULL REFERENCES analyses,
                start_s REAL NOT NULL, end_s REAL NOT NULL,
                label TEXT NOT NULL, score REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS candidate_analysis ON candidates(analysis_id, score DESC);
            CREATE TABLE IF NOT EXISTS audio_references (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                seconds REAL NOT NULL, sample_rate INTEGER NOT NULL, channels INTEGER NOT NULL,
                size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS xc_recordings (
                id INTEGER PRIMARY KEY, metadata TEXT NOT NULL,
                reference_id INTEGER UNIQUE REFERENCES audio_references);
            CREATE TABLE IF NOT EXISTS search_runs (
                id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets,
                species_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS search_hits (
                run_id INTEGER NOT NULL REFERENCES search_runs,
                segment_id INTEGER NOT NULL REFERENCES segments,
                similarity REAL NOT NULL, reference_index INTEGER NOT NULL,
                query_window INTEGER NOT NULL,
                PRIMARY KEY(run_id, segment_id));
            CREATE INDEX IF NOT EXISTS search_ranking ON search_hits(run_id, similarity DESC);
            CREATE TABLE IF NOT EXISTS explore_runs (
                id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites ON DELETE CASCADE,
                engine TEXT NOT NULL, model_sha TEXT NOT NULL, params TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(site_id,engine,model_sha));
            CREATE TABLE IF NOT EXISTS explore_points (
                run_id INTEGER NOT NULL REFERENCES explore_runs ON DELETE CASCADE,
                position INTEGER NOT NULL,
                segment_id INTEGER NOT NULL REFERENCES segments ON DELETE CASCADE,
                x REAL NOT NULL, y REAL NOT NULL, candidates TEXT NOT NULL,
                PRIMARY KEY(run_id,position));
            CREATE TABLE IF NOT EXISTS explore_nodes (
                run_id INTEGER NOT NULL REFERENCES explore_runs ON DELETE CASCADE,
                node INTEGER NOT NULL, parent INTEGER, name TEXT NOT NULL, depth INTEGER NOT NULL,
                lo INTEGER NOT NULL, hi INTEGER NOT NULL,
                rep_segment_id INTEGER NOT NULL REFERENCES segments ON DELETE CASCADE,
                hint_rate REAL NOT NULL, top_species TEXT NOT NULL,
                PRIMARY KEY(run_id,node));
            CREATE INDEX IF NOT EXISTS explore_node_parent ON explore_nodes(run_id,parent);
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets,
                kind TEXT NOT NULL CHECK(kind='detection'),
                snapshot TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS workflow_cache (
                cache_key TEXT PRIMARY KEY, snapshot BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS validation_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id INTEGER NOT NULL REFERENCES datasets,
                species_id TEXT NOT NULL REFERENCES species, model TEXT NOT NULL, fingerprint TEXT NOT NULL,
                snapshot TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS validation_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES validation_sessions,
                phase TEXT NOT NULL CHECK(phase IN ('sample','extension')),
                position INTEGER NOT NULL, recording_id INTEGER NOT NULL REFERENCES recordings,
                start_s REAL NOT NULL, end_s REAL NOT NULL, stratum TEXT NOT NULL, scores TEXT NOT NULL,
                UNIQUE(session_id,phase,position), UNIQUE(session_id,recording_id,start_s,end_s));
            -- "Listened to the top N without confirming": kept even when a later mark confirms the species.
            CREATE TABLE IF NOT EXISTS presence_misses (
                id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id INTEGER NOT NULL REFERENCES datasets,
                species_id TEXT NOT NULL REFERENCES species, source TEXT NOT NULL CHECK(source IN ('ai','search')),
                listened INTEGER NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            -- One species AI per site, species and period; run_id is the search it was last made with, if any.
            CREATE TABLE IF NOT EXISTS custom_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL REFERENCES datasets,
                species_id TEXT NOT NULL REFERENCES species,
                run_id INTEGER REFERENCES search_runs,
                snapshot TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                scores_path TEXT NOT NULL,
                scores_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS workflow_exports (
                id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets,
                snapshot TEXT NOT NULL, path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS quiz_clips (
                id INTEGER PRIMARY KEY, species_id TEXT NOT NULL REFERENCES species,
                reference_id INTEGER NOT NULL REFERENCES audio_references,
                bundled_key TEXT UNIQUE, metadata TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS quiz_sets (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, clip_ids TEXT NOT NULL,
                bundled_key TEXT UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS quiz_sessions (
                id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL, answers TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY, analysis_id INTEGER NOT NULL REFERENCES analyses,
                start_s REAL NOT NULL, end_s REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS recordings_dataset ON recordings(dataset_id,recorded_at);
            CREATE INDEX IF NOT EXISTS datasets_site ON datasets(site_id);
            CREATE INDEX IF NOT EXISTS analyses_recording ON analyses(recording_id,engine,id);
            CREATE INDEX IF NOT EXISTS segment_analysis ON segments(analysis_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS vectors USING vec0(
                embedding float[1536] distance_metric=cosine);
        """)
        db.executescript(labels.SCHEMA)
        labels.upgrade(db)
        import rollups
        db.executescript(rollups.SCHEMA)
        if 'basis' not in columns(db, 'rollup_scans'):
            # What the roll-ups were found on (rollups.scan); NULL: an earlier version's, found again once.
            db.execute('ALTER TABLE rollup_scans ADD COLUMN basis TEXT')
        import test_sounds
        db.executescript(test_sounds.SCHEMA)
        if 'fixed_at' not in columns(db, 'test_phases'):
            # When the test sounds were frozen (test_sounds.sounds); NULL: frozen by an earlier version.
            db.execute('ALTER TABLE test_phases ADD COLUMN fixed_at TEXT')
        import sharing
        db.executescript(sharing.SCHEMA)
        merge_labels(db)
        if columns(db, 'custom_models_v6'):
            fields = 'id,dataset_id,species_id,run_id,snapshot,artifact_path,artifact_sha256,scores_path,scores_sha256,created_at'
            db.execute(f'INSERT OR IGNORE INTO custom_models({fields}) SELECT {fields} FROM custom_models_v6')
            # Keep AUTOINCREMENT's high-water mark so a deleted model's number is never reused.
            last = max(r[0] for r in db.execute('''SELECT COALESCE(MAX(seq),0) FROM sqlite_sequence WHERE name IN ('custom_models','custom_models_v6')
                UNION ALL SELECT COALESCE(MAX(id),0) FROM custom_models'''))
            db.execute("DELETE FROM sqlite_sequence WHERE name='custom_models'")
            db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('custom_models',?)", (last,))
            db.execute('DROP TABLE custom_models_v6')
        db.execute('CREATE TABLE IF NOT EXISTS changed_locations (site_id INTEGER PRIMARY KEY REFERENCES sites)')
        db.execute('CREATE TABLE IF NOT EXISTS result_revision (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO result_revision VALUES(1,0)')
        for table in ('sites', 'datasets', 'recordings', 'analyses'):
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS result_{table}_{operation} AFTER {operation} ON {table}
                    BEGIN UPDATE result_revision SET version=version+1 WHERE id=1; END''')
        db.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('queued', 'running')")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    monotonic_ids(root)
    for name in stale_explore_files:
        try:
            (Path(root) / 'explore' / name).unlink(missing_ok=True)
        except OSError:
            pass
    return True


def merge_labels(db):
    """Copy the four label tables of schema 7 and earlier into `labels`, then drop them.

    Order sets which answer is current when two screens answered the same interval: checks, then "this is its call"
    marks, then sound-map picks, then species-AI page labels, which the species AI already preferred.
    """
    answer = "CASE {0} WHEN 'target' THEN 'present' WHEN 'other' THEN 'absent' ELSE 'unknown' END"
    if columns(db, 'window_answers'):
        db.execute(f'''INSERT INTO labels(recording_id,start_s,end_s,taxon_id,verdict,source,source_ref,created_at)
            SELECT recording_id,start_s,end_s,species_id,{answer.format('verdict')},'validation',
              json_object('session_id',session_id,'quiz_snapshot',json(quiz_snapshot),'examples_snapshot',json(examples_snapshot)),created_at
            FROM window_answers ORDER BY id''')
        db.execute('DROP TABLE window_answers')
    if columns(db, 'presence_marks'):
        db.execute('''INSERT INTO labels(recording_id,start_s,end_s,taxon_id,verdict,source,source_ref,created_at)
            SELECT recording_id,start_s,end_s,species_id,'present','species_mark',json_object('run_id',run_id),created_at
            FROM presence_marks ORDER BY id''')
        db.execute('DROP TABLE presence_marks')
    if columns(db, 'picked_sounds_v7'):
        rows = db.execute('SELECT * FROM picked_sounds_v7 ORDER BY created_at,recording_id,start_s').fetchall()
        db.execute('''INSERT INTO picked_sounds(recording_id,start_s,end_s,sound_type,quality,others,exported_at,created_at,updated_at)
            SELECT recording_id,start_s,end_s,sound_type,quality,others,exported_at,created_at,updated_at FROM picked_sounds_v7''')
        for row in rows:
            names = ([row['species']] if row['species'] else []) + json.loads(row['background'])
            for index, name in enumerate(names):
                taxon = species_id(db, name)
                labels.put(db, taxon, row['recording_id'], row['start_s'], row['end_s'], 'present', 'sound_map',
                           {'background': index > 0 or not row['species']}, history=True)
        db.execute('DROP TABLE picked_sounds_v7')
    if columns(db, 'model_labels_v6'):
        # A label given to the same piece in two searches keeps the newer answer.
        db.execute(f'''INSERT INTO labels(recording_id,start_s,end_s,taxon_id,verdict,source)
            SELECT a.recording_id,s.start_s,s.end_s,r.species_id,{answer.format('l.label')},'training'
            FROM model_labels_v6 l JOIN search_runs r ON r.id=l.run_id JOIN segments s ON s.id=l.segment_id
            JOIN analyses a ON a.id=s.analysis_id ORDER BY l.id''')
        db.execute('DROP TABLE model_labels_v6')
    if columns(db, 'model_labels'):
        db.execute(f'''INSERT INTO labels(recording_id,start_s,end_s,taxon_id,verdict,source)
            SELECT a.recording_id,s.start_s,s.end_s,l.species_id,{answer.format('l.label')},'training'
            FROM model_labels l JOIN segments s ON s.id=l.segment_id JOIN analyses a ON a.id=s.analysis_id ORDER BY l.id''')
        db.execute('DROP TABLE model_labels')


def species_id(db, scientific_name):
    """The species row for a typed scientific name (any case), added as a local species when missing."""
    accepted = 'COALESCE(accepted_id,id)' if 'accepted_id' in columns(db, 'species') else 'id'
    row = db.execute(f'SELECT {accepted} FROM species WHERE lower(scientific_name)=lower(?)', (scientific_name,)).fetchone()
    if row:
        return row[0]
    taxon = str(uuid4())
    db.execute('INSERT INTO species(id,scientific_name) VALUES(?,?)', (taxon, scientific_name))
    return taxon


def columns(db, table):
    """Column name -> NOT NULL flag; empty when the table does not exist."""
    return {row['name']: row['notnull'] for row in db.execute(f'PRAGMA table_info({table})')}


def monotonic_ids(root):
    """Upgrade ordinary row IDs once so deleted links can never target a later import."""
    tables = ('sites', 'datasets', 'recordings', 'jobs', 'analyses', 'candidates', 'segments',
              'search_runs', 'explore_runs', 'reports', 'workflow_exports')
    with connect(root) as db:
        pending = [(table, db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]) for table in tables]
        pending = [(table, sql) for table, sql in pending if 'AUTOINCREMENT' not in sql]
        if not pending:
            return
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('PRAGMA legacy_alter_table=ON')
        db.execute('BEGIN IMMEDIATE')
        for table, sql in pending:
            extras = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,))]
            temporary = table + '_monotonic'
            definition = sql[sql.index('('):].replace('id INTEGER PRIMARY KEY', 'id INTEGER PRIMARY KEY AUTOINCREMENT', 1)
            db.execute(f'CREATE TABLE {temporary} {definition}')
            db.execute(f'INSERT INTO {temporary} SELECT * FROM {table}')
            db.execute(f'DROP TABLE {table}')
            db.execute(f'ALTER TABLE {temporary} RENAME TO {table}')
            for statement in extras:
                db.execute(statement)
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise CatalogError('catalog_migration_failed')


# YYYYMMDD or YYYY-MM-DD, optional _ - T or space, then HHMMSS or HH-MM-SS.
FILENAME_TIME = re.compile(r"(?<!\d)\d{4}(-?)\d{2}\1\d{2}[_\-T ]?\d{2}(-?)\d{2}\2\d{2}(?!\d)")


def recording_time(path, zone_name):
    matches = list(FILENAME_TIME.finditer(Path(path).stem))
    if not matches:
        raise CatalogError("filename_time")
    try:
        naive = datetime.strptime(re.sub(r"\D", "", matches[-1][0]), "%Y%m%d%H%M%S")
        zone = ZoneInfo(zone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise CatalogError("filename_time") from None
    instants = set()
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            instants.add(utc)
    if len(instants) != 1:
        raise CatalogError("ambiguous_time" if instants else "nonexistent_time")
    return naive.isoformat(), instants.pop().isoformat()


def inspect_audio(path, zone):
    try:
        info = sf.info(path)
        stat = path.stat()
    except (OSError, RuntimeError):
        raise CatalogError("unreadable_audio") from None
    if info.channels != 1 or info.frames <= 0:
        raise CatalogError("mono_required")
    original, utc = recording_time(path, zone)
    return (str(path), original, zone, utc, info.duration, info.samplerate,
            stat.st_size, stat.st_mtime_ns)


def preview_folder(root, folder, zone, cancel=None):
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise CatalogError("folder_missing")
    try:
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise CatalogError("invalid_timezone") from None
    with connect(root) as db:
        known = {r[0] for r in db.execute("SELECT path FROM recordings")}
    records, skipped = [], []
    for path in sorted(folder.rglob("*")):
        if cancel and cancel.is_set():
            raise CatalogError('cancelled')
        if path.suffix.lower() not in {".wav", ".flac", ".mp3"} or not path.is_file():
            continue
        path = path.resolve()
        try:
            if str(path) in known:
                raise CatalogError("duplicate")
            records.append(inspect_audio(path, zone))
            known.add(str(path))
        except CatalogError as error:
            skipped.append({"path": str(path), "code": str(error)})
    return records, skipped


def checked_source(row):
    path = Path(row["path"])
    try:
        stat = path.stat()
    except OSError:
        raise CatalogError("source_missing") from None
    if (stat.st_size, stat.st_mtime_ns) != (row["size"], row["mtime_ns"]):
        raise CatalogError("source_changed")
    return path
