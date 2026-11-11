"""Bake the hierarchy mock: exact Ward trees + numpy PaCMAP coordinates for Fuji and the user's 8,700-segment set."""
import json, os, sqlite3, sys, tempfile
from collections import deque
from pathlib import Path
import numpy as np
S = Path(__file__).parent

sys.path.insert(0, str(S))
import hierarchy_proto as proto, hierarchy_exact as hier2, embedding_proto as embed_proto
from proto import ROOT, pca, build_tree
import explore


def tree_json(nodes, leaf_labels):
    members = hier2.node_members(nodes, leaf_labels)
    for n in nodes:
        n['children'].sort(key=lambda c: -nodes[c]['size'])
    names = {0: ''}
    queue = deque([0])
    while queue:
        i = queue.popleft()
        for pos, c in enumerate(nodes[i]['children'], 1):
            names[c] = f'{names[i]}-{pos}' if names[i] else str(pos)
            queue.append(c)
    return [{'id': n['id'], 'parent': n['parent'], 'name': names[n['id']], 'children': n['children'],
             'members': members[n['id']].tolist()} for n in nodes]


def fuji():
    import pandas as pd
    cache = ROOT / '.local-data/cluster-bench-20261101'
    matrix = np.load(cache / 'fuji-embeddings.npy', mmap_mode='r')
    frame = pd.read_parquet(cache / 'fuji-labels.parquet')
    truth = [json.loads(v) for v in frame['labels']]
    labels = ['+'.join(t) + ('?' if u else '') if t else ('?' if u else '') for t, u in zip(truth, frame['unknown'])]
    names = [f'{c} {s:.0f}秒' for c, s in zip(frame['clip'], frame['start_seconds'])]
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    nodes = build_tree(X, np.ones(len(X), int), proto.TAU)
    Y = embed_proto.pacmap(X)
    return {'title': '富士（60 クリップ 2,160 窓、注釈あり）', 'labelKind': '注釈', 'x': Y[:, 0].round(3).tolist(),
            'y': Y[:, 1].round(3).tolist(), 'labels': labels, 'names': names, 'nodes': tree_json(nodes, np.arange(len(X)))}


def user(dataset_id):
    DATA = embed_proto.DATA
    db = sqlite3.connect(f'file:{DATA / "echoroo.sqlite"}?mode=ro', uri=True); db.row_factory = sqlite3.Row
    run = db.execute('SELECT r.* FROM explore_runs r JOIN datasets d ON d.site_id=r.site_id WHERE d.id=?', (dataset_id,)).fetchone()
    params = json.loads(run['params']); index = json.loads((DATA / 'explore' / params['cache_index']).read_text())
    matrix = np.memmap(DATA / 'explore' / params['cache_index'].replace('.json', '.f32'), mode='r', dtype='<f4', shape=(index['rows'], 1536))
    ids = np.memmap(DATA / 'explore' / params['cache_index'].replace('.json', '.i64'), mode='r', dtype='<i8', shape=(index['rows'],))
    maximum, sci = proto.ai_hints(db, params, index, run['engine'])
    columns = [r[1] for r in db.execute('PRAGMA table_info(species)')]
    ja = {}
    if 'japanese_name' in columns:
        ja = {r[0]: r[1] for r in db.execute('SELECT scientific_name, japanese_name FROM species')}
    labels = [(ja.get(n) or n) if m >= explore.SCORE_THRESHOLD else '' for n, m in zip(sci, maximum)]
    rows = {r['id']: r for r in db.execute('''SELECT s.id, r.path, r.recorded_at, s.start_s, s.end_s FROM segments s
        JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
        WHERE s.id IN (SELECT value FROM json_each(?))''', (json.dumps([int(i) for i in ids]),))}
    names = [f"{Path(rows[int(i)]['path']).name} {rows[int(i)]['start_s']:.0f}–{rows[int(i)]['end_s']:.0f}秒" for i in ids]
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    nodes = build_tree(X, np.ones(len(X), int), proto.TAU)
    Y = embed_proto.pacmap(X)
    return {'title': f'実演用（{len(X):,} 区間、AI の候補を目安に表示）', 'labelKind': 'AI の候補', 'x': Y[:, 0].round(3).tolist(),
            'y': Y[:, 1].round(3).tolist(), 'labels': labels, 'names': names, 'nodes': tree_json(nodes, np.arange(len(X)))}


data = {'fuji': fuji(), 'demo': user(3)}
payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
html = (S / 'hierarchy_mock_template.html').read_text().replace('/*DATA*/', payload)
out = ROOT / 'docs/prototypes/explore-hierarchy-mock.html'
out.write_text(html)
print('written', out, f'{out.stat().st_size/1e6:.1f} MB', {k: (len(v['x']), len(v['nodes'])) for k, v in data.items()})
