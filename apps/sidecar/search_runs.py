"""One saved multi-reference search per species and site; deleting it removes what was built on it."""
import json
import secrets

import numpy as np

from analysis import load_perch
from audio import source
from search import search_context, query_embeddings
from species import names_for
from storage import CatalogError, checked_source, connect


def site_run(db, site_id, species_id):
    return db.execute('''SELECT r.* FROM search_runs r JOIN datasets d ON d.id=r.dataset_id
        WHERE d.site_id=? AND r.species_id=? ORDER BY r.id DESC''', (site_id, species_id)).fetchone()


def create_run(root, models, body, cancel=None):
    loaded = load_perch(models)
    with connect(root) as db:
        dataset = db.execute('SELECT * FROM datasets WHERE id=?', (body.dataset_id,)).fetchone()
        species = db.execute('SELECT * FROM species WHERE id=?', (body.species_id,)).fetchone()
        if dataset is None or species is None:
            raise CatalogError('not_found')
        if site_run(db, dataset['site_id'], body.species_id):
            raise CatalogError('search_exists')
        site = db.execute('SELECT * FROM sites WHERE id=?', (dataset['site_id'],)).fetchone()
        compatible, total = search_context(db, body.dataset_id, loaded[1], body.start_date, body.end_date)
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
            embedded, query = query_embeddings(models, row, clip.start_s, clip.end_s, loaded, cancel)
            references.append({'source': row, 'query': query})
            vectors.extend(embedded)
            owners.extend((index, window) for window in range(len(embedded)))
        queries = np.stack(vectors)
        queries /= np.linalg.norm(queries, axis=1, keepdims=True)
        snapshot = {'group': secrets.token_hex(16), 'species': names_for(species), 'dataset': dict(dataset), 'site': dict(site),
                    'start_date': body.start_date.isoformat() if body.start_date else None,
                    'end_date': body.end_date.isoformat() if body.end_date else None, 'date_scope': 'recording_start_local',
                    'model_sha256': loaded[1], 'aggregation': 'maximum_cosine', 'references': references,
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
    return get_run(root, run_id)


def get_run(root, run_id):
    with connect(root) as db:
        run = db.execute('SELECT * FROM search_runs WHERE id=?', (run_id,)).fetchone()
        if run is None:
            raise CatalogError('not_found')
        return {**dict(run), 'snapshot': json.loads(run['snapshot'])}


def top_hits(db, run_id, limit, offset=0):
    return [dict(r) for r in db.execute('''SELECT h.segment_id,h.similarity,s.start_s,s.end_s,
        r.id AS recording_id,r.path,r.seconds,r.recorded_at,r.timezone
        FROM search_hits h JOIN segments s ON s.id=h.segment_id JOIN analyses a ON a.id=s.analysis_id
        JOIN recordings r ON r.id=a.recording_id WHERE h.run_id=? ORDER BY h.similarity DESC,h.segment_id LIMIT ? OFFSET ?''',
        (run_id, limit, offset))]


def deletion(db, run_id):
    run = db.execute('SELECT r.*,d.site_id FROM search_runs r JOIN datasets d ON d.id=r.dataset_id WHERE r.id=?', (run_id,)).fetchone()
    if run is None:
        raise CatalogError('not_found')
    models = [r[0] for r in db.execute('SELECT id FROM custom_models WHERE run_id=? ORDER BY id', (run_id,))]
    return run, {'marks': db.execute('SELECT COUNT(*) FROM presence_marks WHERE run_id=?', (run_id,)).fetchone()[0],
                 'labels': db.execute('SELECT COUNT(*) FROM model_labels WHERE run_id=?', (run_id,)).fetchone()[0],
                 'custom_models': len(models), 'model_ids': models,
                 'checks': db.execute('SELECT COUNT(*) FROM validation_sessions WHERE model IN (SELECT value FROM json_each(?))',
                                      (json.dumps([f'custom:{m}' for m in models]),)).fetchone()[0]}


def delete_preview(root, run_id):
    with connect(root) as db:
        return deletion(db, run_id)[1]


def delete_run(root, run_id):
    """The search, its presence marks, not-found records, model labels, and custom models built from it."""
    from custom_models import delete_model
    with connect(root) as db:
        run, plan = deletion(db, run_id)
    for model_id in plan['model_ids']:
        delete_model(root, model_id)
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        for table in ('presence_marks', 'model_labels', 'search_hits'):
            db.execute(f'DELETE FROM {table} WHERE run_id=?', (run_id,))
        db.execute('''DELETE FROM presence_misses WHERE source='search' AND species_id=?
            AND dataset_id IN (SELECT id FROM datasets WHERE site_id=?)''', (run['species_id'], run['site_id']))
        db.execute('DELETE FROM search_runs WHERE id=?', (run_id,))
        db.execute('DELETE FROM workflow_cache')
    return plan
