"""Local catalog. Source audio is referenced, never modified."""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import soundfile as sf
import sqlite_vec


class CatalogError(Exception):
    pass


@contextmanager
def connect(root):
    db = sqlite3.connect(Path(root) / "echoroo.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
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
    with connect(root) as db:
        db.execute("PRAGMA journal_mode=WAL")
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
            CREATE TABLE IF NOT EXISTS search_samples (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES search_runs,
                low REAL NOT NULL, high REAL NOT NULL, population INTEGER NOT NULL,
                requested_count INTEGER NOT NULL, seed INTEGER NOT NULL,
                method TEXT NOT NULL DEFAULT 'uniform_without_replacement_v1',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS search_sample_items (
                sample_id INTEGER NOT NULL REFERENCES search_samples,
                segment_id INTEGER NOT NULL REFERENCES segments, position INTEGER NOT NULL,
                PRIMARY KEY(sample_id, segment_id));
            CREATE TABLE IF NOT EXISTS search_judgements (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES search_runs,
                species_id TEXT NOT NULL, segment_id INTEGER NOT NULL REFERENCES segments,
                verdict TEXT NOT NULL CHECK(verdict IN ('target','other','unknown')),
                note TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS search_judgement_target ON search_judgements(species_id, segment_id, id DESC);
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets,
                kind TEXT NOT NULL CHECK(kind IN ('detection','search')),
                snapshot TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS workflow_cache (
                cache_key TEXT PRIMARY KEY, snapshot BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS validation_sessions (
                id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets,
                species_id TEXT NOT NULL REFERENCES species, fingerprint TEXT NOT NULL,
                snapshot TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS validation_items (
                id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES validation_sessions,
                phase TEXT NOT NULL CHECK(phase IN ('calibration','evaluation')),
                position INTEGER NOT NULL, recording_id INTEGER NOT NULL REFERENCES recordings,
                start_s REAL NOT NULL, stratum TEXT NOT NULL, scores TEXT NOT NULL,
                UNIQUE(session_id,phase,position), UNIQUE(session_id,recording_id,start_s));
            CREATE TABLE IF NOT EXISTS validation_answers (
                id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES validation_items,
                verdict TEXT NOT NULL CHECK(verdict IN ('target','other','unknown')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS validation_answer_item ON validation_answers(item_id,id DESC);
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
        db.execute('CREATE TABLE IF NOT EXISTS changed_locations (site_id INTEGER PRIMARY KEY REFERENCES sites)')
        db.execute('CREATE TABLE IF NOT EXISTS result_revision (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO result_revision VALUES(1,0)')
        for table in ('sites', 'datasets', 'recordings', 'analyses'):
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS result_{table}_{operation} AFTER {operation} ON {table}
                    BEGIN UPDATE result_revision SET version=version+1 WHERE id=1; END''')
        db.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('queued', 'running')")
    monotonic_ids(root)


def monotonic_ids(root):
    """Upgrade ordinary row IDs once so deleted links can never target a later import."""
    tables = ('sites', 'datasets', 'recordings', 'jobs', 'analyses', 'candidates', 'segments',
              'search_runs', 'search_samples', 'search_judgements', 'reports',
              'validation_sessions', 'validation_items', 'validation_answers', 'workflow_exports')
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


def recording_time(path, zone_name):
    match = re.search(r"(\d{8}_\d{6})$", Path(path).stem)
    if not match:
        raise CatalogError("filename_time")
    try:
        naive = datetime.strptime(match[1], "%Y%m%d_%H%M%S")
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


def preview_folder(root, folder, zone):
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
        if path.suffix.lower() not in {".wav", ".flac"} or not path.is_file():
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
