"""One saved multi-reference search per species and site; deleting it removes what was built on it."""
import json
import secrets
from datetime import date

import numpy as np

from analysis import load_perch, load_onnx
from models import main_engine, main_identity
from pathlib import Path
from audio import source
from search import search_context, query_embeddings
from species import names_for
from storage import CatalogError, checked_source, connect


def site_run(db, site_id, species_id):
    root = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
    engine, sha = main_identity(root)
    return db.execute('''SELECT r.* FROM search_runs r JOIN datasets d ON d.id=r.dataset_id
        WHERE d.site_id=? AND r.species_id=? AND COALESCE(json_extract(r.snapshot,'$.engine'),'perch')=?
        AND (? IS NULL OR json_extract(r.snapshot,'$.model_sha256')=?) ORDER BY r.id DESC''',
        (site_id, species_id, engine, sha, sha)).fetchone()


def create_run(root, models, body, cancel=None):
    engine = main_engine(root)
    loaded = load_perch(models) if engine == 'perch' else load_onnx(models, engine)
    with connect(root) as db:
        dataset = db.execute('SELECT * FROM datasets WHERE id=?', (body.dataset_id,)).fetchone()
        species = db.execute('SELECT * FROM species WHERE id=?', (body.species_id,)).fetchone()
        if dataset is None or species is None:
            raise CatalogError('not_found')
        old = site_run(db, dataset['site_id'], body.species_id)
        if old and not body.replace:
            raise CatalogError('search_exists')
        # A search made again (with more examples) covers the days the earlier one did, whatever the page shows now.
        first, last = [date.fromisoformat(day) if day else None for day in (json.loads(old['snapshot']).get(key) for key in ('start_date', 'end_date'))] \
            if old else (body.start_date, body.end_date)
        site = db.execute('SELECT * FROM sites WHERE id=?', (dataset['site_id'],)).fetchone()
        compatible, total = search_context(db, body.dataset_id, loaded[1], first, last, engine)
        if not compatible:
            raise CatalogError('no_search_embeddings')
        for row in compatible:
            checked_source(row)
        # A temporary table avoids SQLite's host-parameter limit on large catalogs.
        db.execute('CREATE TEMP TABLE target_analyses(id INTEGER PRIMARY KEY)')
        db.executemany('INSERT INTO target_analyses VALUES(?)', ((r['analysis_id'],) for r in compatible))
        coverage = {'total_recordings': total, 'searched_recordings': len(compatible),
                    'unsearched_recordings': total - len(compatible),
                    'searched_segments': db.execute('SELECT COUNT(*) FROM segments WHERE analysis_id IN target_analyses').fetchone()[0]}
        references, vectors, owners = [], [], []
        for index, clip in enumerate(body.clips):
            if cancel and cancel.is_set():
                raise CatalogError('cancelled')
            row = source(root, clip.source_kind, clip.source_id)
            embedded, query = query_embeddings(models, row, clip.start_s, clip.end_s, loaded, cancel, engine)
            references.append({'source': row, 'query': query})
            vectors.extend(embedded)
            owners.extend((index, window) for window in range(len(embedded)))
        queries = np.stack(vectors)
        queries /= np.linalg.norm(queries, axis=1, keepdims=True)
        snapshot = {'group': secrets.token_hex(16), 'species': names_for(species), 'dataset': dict(dataset), 'site': dict(site),
                    'start_date': first.isoformat() if first else None,
                    'end_date': last.isoformat() if last else None, 'date_scope': 'recording_start_local',
                    'engine': engine, 'model_sha256': loaded[1], 'aggregation': 'maximum_cosine', 'references': references,
                    'analysis_ids': [r['analysis_id'] for r in compatible], **coverage}
        run_id = db.execute('INSERT INTO search_runs(dataset_id,species_id,snapshot) VALUES(?,?,?)',
                            (body.dataset_id, body.species_id, json.dumps(snapshot, ensure_ascii=False))).lastrowid
        rows = db.execute('''SELECT s.*, r.path, v.embedding FROM segments s
            JOIN vectors v ON v.rowid=s.id JOIN analyses a ON a.id=s.analysis_id
            JOIN recordings r ON r.id=a.recording_id WHERE s.analysis_id IN target_analyses ORDER BY s.id''')
        # ponytail: scan in 256-vector blocks; add a partitioned vector index if annual-scale search needs it.
        while batch := rows.fetchmany(256):
            if cancel and cancel.is_set():
                raise CatalogError('cancelled')
            matrix = np.stack([np.frombuffer(r['embedding'], dtype='<f4') for r in batch])
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            if not np.isfinite(matrix).all() or np.any(norms == 0):
                raise CatalogError('invalid_scores')
            matrix /= norms
            similarities = np.clip(matrix @ queries.T, -1, 1)
            winners = similarities.argmax(axis=1)
            db.executemany('INSERT INTO search_hits VALUES(?,?,?,?,?)', [
                (run_id, row['id'], float(similarities[i, winners[i]]), *owners[int(winners[i])]) for i, row in enumerate(batch)
                if not any(row['path'] == ref['source']['path'] and row['start_s'] < ref['query']['end_s']
                           and row['end_s'] > ref['query']['start_s'] for ref in references)])
        for row in compatible:
            checked_source(row)
        for ref in references:
            checked_source(ref['source'])
        if old:
            # What was built on the earlier search moves to this one (its marks, the species AI last made with it),
            # so no answer is lost; then the earlier search goes. A stopped or failed search leaves everything as it was.
            db.execute("""UPDATE labels SET source_ref=json_set(source_ref,'$.run_id',?) WHERE source='species_mark'
                AND json_extract(source_ref,'$.run_id')=?""", (run_id, old['id']))
            db.execute("UPDATE custom_models SET run_id=?,snapshot=json_set(snapshot,'$.run_id',?) WHERE run_id=?", (run_id, run_id, old['id']))
            db.execute('DELETE FROM search_hits WHERE run_id=?', (old['id'],))
            db.execute('DELETE FROM search_runs WHERE id=?', (old['id'],))
            db.execute('DELETE FROM workflow_cache')
    return get_run(root, run_id)


def examples(root, dataset_id, species_id):
    """The examples of the site's current search for a species, as the search page lists chosen examples, so a
    search made again can keep them. `missing`: examples left out because their file is gone or has changed."""
    with connect(root) as db:
        dataset = db.execute('SELECT site_id FROM datasets WHERE id=?', (dataset_id,)).fetchone()
        if dataset is None:
            raise CatalogError('not_found')
        run = site_run(db, dataset[0], species_id)
    clips, missing = [], 0
    for ref in json.loads(run['snapshot'])['references'] if run else []:
        try:
            row = source(root, ref['source']['kind'], ref['source']['id'])
            checked_source(row)
        except CatalogError:
            missing += 1
            continue
        clips.append({'source': row, 'start_s': ref['query']['start_s'], 'end_s': ref['query']['end_s']})
    return {'exists': run is not None, 'clips': clips, 'missing': missing}


def get_run(root, run_id):
    with connect(root) as db:
        run = db.execute('SELECT * FROM search_runs WHERE id=?', (run_id,)).fetchone()
        if run is None:
            raise CatalogError('not_found')
        snap = json.loads(run['snapshot'])
        engine, sha = main_identity(root)
        if snap.get('engine', 'perch') != engine or sha is not None and snap['model_sha256'] != sha:
            raise CatalogError('not_found')
        return {**dict(run), 'snapshot': snap}


def top_hits(db, run_id, limit, offset=0, segments=None):
    """Hits by similarity; `segments` keeps only those segment ids."""
    return [dict(r) for r in db.execute('''SELECT h.segment_id,h.similarity,s.start_s,s.end_s,
        r.id AS recording_id,r.path,r.seconds,r.recorded_at,r.timezone
        FROM search_hits h JOIN segments s ON s.id=h.segment_id JOIN analyses a ON a.id=s.analysis_id
        JOIN recordings r ON r.id=a.recording_id WHERE h.run_id=?
          AND (? IS NULL OR h.segment_id IN (SELECT value FROM json_each(?)))
        ORDER BY h.similarity DESC,h.segment_id LIMIT ? OFFSET ?''',
        (run_id, *[None if segments is None else json.dumps(segments)] * 2, limit, offset))]


def deletion(db, run_id):
    run = db.execute('SELECT r.*,d.site_id FROM search_runs r JOIN datasets d ON d.id=r.dataset_id WHERE r.id=?', (run_id,)).fetchone()
    if run is None:
        raise CatalogError('not_found')
    models = [r[0] for r in db.execute('SELECT id FROM custom_models WHERE run_id=? ORDER BY id', (run_id,))]
    return run, {'marks': db.execute("""SELECT COUNT(*) FROM labels WHERE source='species_mark'
                     AND json_extract(source_ref,'$.run_id')=?""", (run_id,)).fetchone()[0],
                 'custom_models': len(models), 'model_ids': models,
                 'checks': db.execute('SELECT COUNT(*) FROM validation_sessions WHERE model IN (SELECT value FROM json_each(?))',
                                      (json.dumps([f'custom:{m}' for m in models]),)).fetchone()[0]}


def delete_preview(root, run_id):
    with connect(root) as db:
        return deletion(db, run_id)[1]


def delete_run(root, run_id):
    """The search, its presence marks, not-found records, and species AIs last made with it.
    Voice / no-voice labels belong to the species, not the search, and stay."""
    from custom_models import delete_model
    with connect(root) as db:
        run, plan = deletion(db, run_id)
    for model_id in plan['model_ids']:
        delete_model(root, model_id)
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM search_hits WHERE run_id=?', (run_id,))
        db.execute("""DELETE FROM labels WHERE source='species_mark' AND json_extract(source_ref,'$.run_id')=?""", (run_id,))
        db.execute('''DELETE FROM presence_misses WHERE source='search' AND species_id=?
            AND dataset_id IN (SELECT id FROM datasets WHERE site_id=?)''', (run['species_id'], run['site_id']))
        db.execute('DELETE FROM search_runs WHERE id=?', (run_id,))
        db.execute('DELETE FROM workflow_cache')
    return plan
