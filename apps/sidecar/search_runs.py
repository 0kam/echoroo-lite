"""Immutable multi-reference searches, random review samples, and append-only judgements."""
import json
import random
import secrets

import numpy as np

from analysis import load_perch
from audio import source
from search import compatible_analyses, query_embeddings
from species import names_for
from storage import CatalogError, checked_source, connect


def create_runs(root, models, body):
    loaded = load_perch(models)
    with connect(root) as db:
        dataset = db.execute('SELECT * FROM datasets WHERE id=?', (body.dataset_id,)).fetchone()
        if dataset is None:
            raise CatalogError('not_found')
        site = db.execute('SELECT * FROM sites WHERE id=?', (dataset['site_id'],)).fetchone()
        total = db.execute('SELECT COUNT(*) FROM recordings WHERE dataset_id=?', (body.dataset_id,)).fetchone()[0]
        compatible = compatible_analyses(db, body.dataset_id, loaded[1])
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
        ids = []
        for target in body.targets:
            species = db.execute('SELECT * FROM species WHERE id=?', (target.species_id,)).fetchone()
            if species is None:
                raise CatalogError('not_found')
            references, vectors, owners = [], [], []
            for index, clip in enumerate(target.clips):
                row = source(root, clip.source_kind, clip.source_id)
                embedded, query = query_embeddings(models, row, clip.start_s, clip.end_s, loaded)
                references.append({'source': row, 'query': query})
                vectors.extend(embedded)
                owners.extend((index, window) for window in range(len(embedded)))
            queries = np.stack(vectors)
            queries /= np.linalg.norm(queries, axis=1, keepdims=True)
            snapshot = {'species': names_for(species), 'dataset': dict(dataset), 'site': dict(site),
                        'model_sha256': loaded[1], 'aggregation': 'maximum_cosine', 'references': references,
                        'analysis_ids': [r['analysis_id'] for r in compatible], **coverage}
            run_id = db.execute('INSERT INTO search_runs(dataset_id,species_id,snapshot) VALUES(?,?,?)',
                                (body.dataset_id, target.species_id, json.dumps(snapshot, ensure_ascii=False))).lastrowid
            rows = db.execute('''SELECT s.*, r.path, v.embedding FROM segments s
                JOIN vectors v ON v.rowid=s.id JOIN analyses a ON a.id=s.analysis_id
                JOIN recordings r ON r.id=a.recording_id WHERE s.analysis_id IN target_analyses ORDER BY s.id''')
            # ponytail: scan in 256-vector blocks; add a partitioned vector index if annual-scale search needs it.
            while batch := rows.fetchmany(256):
                matrix = np.stack([np.frombuffer(r['embedding'], dtype='<f4') for r in batch])
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                if not np.isfinite(matrix).all() or np.any(norms == 0):
                    raise CatalogError('invalid_scores')
                similarities = np.clip((matrix / norms) @ queries.T, -1, 1)
                winners = similarities.argmax(axis=1)
                hits = []
                for i, row in enumerate(batch):
                    if any(row['path'] == ref['source']['path'] and row['start_s'] < ref['query']['end_s']
                           and row['end_s'] > ref['query']['start_s'] for ref in references):
                        continue
                    winner = int(winners[i])
                    hits.append((run_id, row['id'], float(similarities[i, winner]), *owners[winner]))
                db.executemany('INSERT INTO search_hits VALUES(?,?,?,?,?)', hits)
            for row in compatible:
                checked_source(row)
            for ref in references:
                checked_source(ref['source'])
            ids.append(run_id)
    return [get_run(root, run_id) for run_id in ids]


def list_runs(root, dataset_id):
    with connect(root) as db:
        rows = db.execute('SELECT id,species_id,created_at FROM search_runs WHERE dataset_id=? ORDER BY id DESC', (dataset_id,)).fetchall()
        return [{**dict(r), 'species': names_for(db.execute('SELECT * FROM species WHERE id=?', (r['species_id'],)).fetchone())} for r in rows]


def get_run(root, run_id, sample_id=None, limit=30):
    with connect(root) as db:
        run = db.execute('SELECT * FROM search_runs WHERE id=?', (run_id,)).fetchone()
        if run is None:
            raise CatalogError('not_found')
        samples = [{**dict(r), 'seed': str(r['seed'])} for r in db.execute('''SELECT s.*, COUNT(i.segment_id) AS count FROM search_samples s
            LEFT JOIN search_sample_items i ON i.sample_id=s.id WHERE run_id=? GROUP BY s.id ORDER BY s.id DESC''', (run_id,))]
        if sample_id is not None and sample_id not in [s['id'] for s in samples]:
            raise CatalogError('not_found')
        hits = db.execute('''SELECT h.*, s.start_s,s.end_s,s.analysis_id,
            r.id AS recording_id,r.path,r.seconds,r.recorded_at,r.timezone,
            j.verdict,j.note,j.id AS judgement_id,j.created_at AS judged_at
            FROM search_hits h JOIN segments s ON s.id=h.segment_id
            JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
            LEFT JOIN search_judgements j ON j.id=(SELECT MAX(id) FROM search_judgements
                WHERE species_id=? AND segment_id=h.segment_id)
            WHERE h.run_id=? AND (? IS NULL OR h.segment_id IN
                (SELECT segment_id FROM search_sample_items WHERE sample_id=?))
            ORDER BY h.similarity DESC,h.segment_id LIMIT ?''',
                          (run['species_id'], run_id, sample_id, sample_id, limit if sample_id is None else 200)).fetchall()
        count = db.execute('SELECT COUNT(*) FROM search_hits WHERE run_id=?', (run_id,)).fetchone()[0]
        return {**dict(run), 'snapshot': json.loads(run['snapshot']), 'hits': [dict(h) for h in hits],
                'hit_count': count, 'samples': samples, 'sample_id': sample_id}


def draw_sample(root, run_id, low, high, count):
    if low > high:
        raise CatalogError('invalid_input')
    with connect(root) as db:
        if db.execute('SELECT 1 FROM search_runs WHERE id=?', (run_id,)).fetchone() is None:
            raise CatalogError('not_found')
        population = [r[0] for r in db.execute('SELECT segment_id FROM search_hits WHERE run_id=? AND similarity BETWEEN ? AND ? ORDER BY segment_id', (run_id, low, high))]
        if not population:
            raise CatalogError('empty_similarity_band')
        seed = secrets.randbits(63)
        chosen = random.Random(seed).sample(population, min(count, len(population)))
        sample = db.execute('INSERT INTO search_samples(run_id,low,high,population,requested_count,seed) VALUES(?,?,?,?,?,?)',
                            (run_id, low, high, len(population), count, seed)).lastrowid
        db.executemany('INSERT INTO search_sample_items VALUES(?,?,?)', ((sample, sid, i) for i, sid in enumerate(chosen)))
    return get_run(root, run_id, sample)


def judge(root, run_id, segment_id, verdict, note):
    with connect(root) as db:
        hit = db.execute('''SELECT r.species_id FROM search_hits h JOIN search_runs r ON r.id=h.run_id
            WHERE h.run_id=? AND h.segment_id=?''', (run_id, segment_id)).fetchone()
        if hit is None:
            raise CatalogError('not_found')
        jid = db.execute('INSERT INTO search_judgements(run_id,species_id,segment_id,verdict,note) VALUES(?,?,?,?,?)',
                         (run_id, hit['species_id'], segment_id, verdict, note)).lastrowid
        return dict(db.execute('SELECT * FROM search_judgements WHERE id=?', (jid,)).fetchone())
