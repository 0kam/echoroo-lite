"""Read-only sampling experiment and local media for the annotation UX proposal.

This does not create validation answers or estimate classifier performance.
"""
import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from time import perf_counter
from zoneinfo import ZoneInfo

import numpy as np
import sqlite_vec

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'apps/sidecar'))
from reports import regional_list, read_classification_windows, window_key
from species import names_for


from sampling import strata_for, draw_plan


def load_scope(data, dataset_id):
    db = sqlite3.connect((data / 'echoroo.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute('PRAGMA query_only=ON')
    try:
        site = dict(db.execute('SELECT s.* FROM sites s JOIN datasets d ON d.site_id=s.id WHERE d.id=?', (dataset_id,)).fetchone())
        records = {r['id']: dict(r) for r in db.execute('''SELECT r.* FROM recordings r
            JOIN datasets d ON d.id=r.dataset_id WHERE d.site_id=? ORDER BY r.recorded_at,r.id''', (site['id'],))}
        items = [{'recording_id': rid, 'start_s': float(start), 'predictions': {}}
                 for rid, record in records.items() for start in range(0, int(record['seconds']) - 14, 15)]
        lookup = {(row['recording_id'], row['start_s']): i for i, row in enumerate(items)}
        vectors = np.zeros((len(items), 1536), dtype=np.float32)
        analyses = [dict(r) for r in db.execute('''SELECT a.* FROM analyses a JOIN recordings r ON r.id=a.recording_id
            JOIN datasets d ON d.id=r.dataset_id WHERE d.site_id=?
            AND a.id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)
            ORDER BY a.recording_id,a.engine''', (site['id'],))]
        hashes = defaultdict(set)
        palette = set()
        for analysis in analyses:
            record = records[analysis['recording_id']]
            allowed = regional_list(db, analysis)
            if allowed is not None:
                path = Path(analysis['scores_path'])
                stat = path.stat()
                signature = [stat.st_size, stat.st_mtime_ns, analysis['engine'], float(record['seconds']), tuple(sorted(allowed)), 1]
                key = hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:20]
                cached = path.with_name(path.name + '.' + key + '.windows.npz')
                if cached.exists():
                    with np.load(cached, allow_pickle=False) as saved:
                        labels, starts, scores = (saved[field] for field in ('labels', 'starts', 'scores'))
                else:
                    labels, starts, scores, _ = read_classification_windows(analysis, record['seconds'], allowed)
                for start, scores_row in zip(starts, scores):
                    index = lookup[(record['id'], float(start))]
                    predicted = frozenset(str(label) for label in labels[scores_row >= .1])
                    items[index]['predictions'][analysis['engine']] = predicted
                    palette.update(predicted)
            if analysis['engine'] == 'perch':
                hashes['embedding_model'].add(json.loads(analysis['metadata'])['model_sha256'])
                segments = {float(row['start_s']): np.frombuffer(row['embedding'], dtype='<f4') for row in db.execute(
                    '''SELECT s.start_s,v.embedding FROM segments s JOIN vectors v ON v.rowid=s.id
                    WHERE s.analysis_id=? AND ABS(s.end_s-s.start_s-5)<0.000001''', (analysis['id'],))}
                for start in range(0, int(record['seconds']) - 14, 15):
                    if not all(start + offset in segments for offset in (0, 5, 10)):
                        continue
                    matrix = np.stack([segments[start + offset] for offset in (0, 5, 10)])
                    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                    if not np.isfinite(matrix).all() or np.any(norms == 0):
                        continue
                    mean = (matrix / norms).mean(axis=0)
                    norm = np.linalg.norm(mean)
                    if norm > 0:
                        vectors[lookup[(record['id'], float(start))]] = mean / norm
            hashes[analysis['engine']].add(json.loads(analysis['metadata'])['model_sha256'])
        if len(hashes['embedding_model']) > 1:
            raise ValueError('The experiment requires one compatible embedding model.')
        taxa = [names_for(row) for row in db.execute('SELECT * FROM species ORDER BY scientific_name')]
        for row in items:
            predictions = row.pop('predictions')
            a, b = predictions.get('birdnet'), predictions.get('perch')
            row['group'] = 'unavailable' if a is None or b is None else 'low' if not a and not b else 'agreement' if a == b else 'difference'
        return site, records, items, vectors, [r for r in taxa if r['scientific_name'] in palette], taxa, {k: sorted(v) for k, v in hashes.items()}
    finally:
        db.close()


def check():
    groups = np.array(['low'] * 20 + ['difference'] * 80)
    vectors = np.array([[1., 0.]] * 10 + [[0., 1.]] * 10 + [[-1., 0.]] * 80, dtype=np.float32)
    vectors[-1] = 0
    strata, _ = strata_for(groups, vectors, 3)
    assert strata[-1] == 'missing-embedding'
    assert len(set(strata[:20])) == 2
    order, info = draw_plan(strata, 30, 4)
    assert len(order) == len(set(order)) == 30
    assert order == draw_plan(strata, 30, 4)[0]
    assert order[:10] == draw_plan(strata, 10, 4)[0]
    assert set(strata[order[:len(info)]]) == set(strata)
    assert abs(sum(r['sample'] * r['weight'] for r in info) - len(strata)) < 1e-9
    assert len(draw_plan(strata, 200, 4)[0]) == len(strata)
    # Verify a weighted total against known truth over repeated independent draws.
    truth = np.arange(100) % 7 == 0
    estimates = []
    for seed in range(600):
        chosen, rows = draw_plan(strata, 30, seed)
        weights = {r['stratum']: r['weight'] for r in rows}
        estimates.append(sum(weights[strata[i]] * truth[i] for i in chosen))
    assert abs(np.mean(estimates) - truth.sum()) < .6
    print(json.dumps({'checks': 'passed', 'true_total': int(truth.sum()), 'mean_estimated_total': float(np.mean(estimates))}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--data', type=Path)
    parser.add_argument('--dataset', type=int, default=1)
    parser.add_argument('--output', type=Path, default=REPO / '.local-data/annotation-design')
    parser.add_argument('--seed', type=int, default=20260915)
    args = parser.parse_args()
    if args.check:
        check()
        return
    if not args.data:
        parser.error('--data is required unless --check is used')
    started = perf_counter()
    site, records, items, vectors, palette, taxa, hashes = load_scope(args.data, args.dataset)
    loaded = perf_counter()
    groups = np.array([r['group'] for r in items])
    strata, fits = strata_for(groups, vectors, args.seed)
    clustered = perf_counter()
    order, totals = draw_plan(strata, 30, args.seed + 1)
    prepared = perf_counter()
    args.output.mkdir(parents=True, exist_ok=True)
    plan = {'purpose': 'design_experiment_not_a_performance_evaluation', 'method': 'model_groups_kmeans2_v1',
            'seed': args.seed, 'screening_threshold': .1, 'window_s': 15, 'model_hashes': hashes,
            'recordings': [{key: r[key] for key in ('id', 'path', 'recorded_at', 'timezone', 'seconds', 'size', 'mtime_ns')}
                           for r in records.values()],
            'population': len(items), 'groups': dict(Counter(groups)), 'strata': totals, 'fits': fits,
            'timings_s': {'load': loaded-started, 'cluster': clustered-loaded, 'sample': prepared-clustered},
            'population_items': [{**row, 'stratum': strata[i]} for i, row in enumerate(items)],
            'sample_indexes': order, 'checkpoints': {str(n): draw_plan(strata, n, args.seed + 1)[1] for n in (10, 20, 30)}}
    (args.output / 'sampling-plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    # Local media are outside version control; source audio and catalog remain read-only.
    from audio import read_interval, spectrogram
    from PIL import Image
    import soundfile as sf
    roseus = np.asarray(json.loads((REPO / 'apps/sidecar/data/report/roseus.json').read_text()), dtype=np.uint8).reshape(256, 3)
    previews = []
    zone = ZoneInfo(site['timezone'])
    for position, index in enumerate(order):
        item = items[index]
        record = records[item['recording_id']]
        start, end = item['start_s'], item['start_s'] + 15
        prefix = f'clip-{position + 1:02d}'
        audio, interval = read_interval(record, start, end, native_rate=True)
        sf.write(args.output / (prefix + '.wav'), audio, interval['sample_rate'], subtype='PCM_16')
        spec = spectrogram(record, start, end, max_columns=8000)
        pixels = np.frombuffer(base64.b64decode(spec['pixels']), dtype=np.uint8).reshape(spec['height'], spec['width'])
        Image.fromarray(roseus[pixels]).save(args.output / (prefix + '.png'))
        previews.append({'recording_id': record['id'], 'source_name': Path(record['path']).name,
                         'start_s': start, 'end_s': end, 'local_time': window_key(record, start, zone)[1],
                         'audio': prefix + '.wav', 'image': prefix + '.png',
                         'frequency_max_hz': spec['frequency_max_hz']})
    payload = {'site': site['name'], 'timezone': site['timezone'], 'window_s': 15,
               'palette': palette, 'catalog': taxa, 'clips': previews}
    (args.output / 'preview.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    summary = {k: plan[k] for k in ('method', 'population', 'groups', 'strata', 'timings_s')}
    summary.update(palette_species=len(palette), preview_media_s=perf_counter()-prepared)
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
