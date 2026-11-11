"""Bake mock v2.1 (accordion rows + sound card): adds per-sound AI candidates (Japan list, top 3) for the demo set; annotations for Fuji."""
import json, sqlite3, sys, tempfile
from pathlib import Path
import numpy as np
S = Path(__file__).parent

sys.path.insert(0, str(S))
import hierarchy_proto as proto, hierarchy_exact as hier2, embedding_proto as embed_proto, hierarchy_mock as mockdata
from proto import ROOT, pca, build_tree
from scipy.special import softmax

def fuji():
    d = mockdata.fuji()
    d['cands'] = [[] if not l or l == '?' else [x for x in l.rstrip('?').split('+')] for l in d['labels']]
    d['title'] = '富士（60 クリップ 2,160 窓、注釈あり）'; d['labelKind'] = '注釈'
    return d

def user(dataset_id):
    DATA = embed_proto.DATA
    db = sqlite3.connect(f'file:{DATA / "echoroo.sqlite"}?mode=ro', uri=True); db.row_factory = sqlite3.Row
    run = db.execute('SELECT r.* FROM explore_runs r JOIN datasets d ON d.site_id=r.site_id WHERE d.id=?', (dataset_id,)).fetchone()
    params = json.loads(run['params']); index = json.loads((DATA / 'explore' / params['cache_index']).read_text())
    rows = index['rows']
    matrix = np.memmap(DATA / 'explore' / params['cache_index'].replace('.json', '.f32'), mode='r', dtype='<f4', shape=(rows, 1536))
    ids = np.memmap(DATA / 'explore' / params['cache_index'].replace('.json', '.i64'), mode='r', dtype='<i8', shape=(rows,))
    ja = {}
    bundled = json.load(open(ROOT / 'apps/sidecar/data/species.json'))['species']
    for scientific, rec in bundled.items():
        names = rec.get('common_names') or rec.get('names') or rec
        if isinstance(names, dict) and names.get('ja'):
            ja[scientific] = names['ja']
    cands = [[] for _ in range(rows)]
    for aid, entry in index['analyses'].items():
        row = db.execute('SELECT scores_path, metadata FROM analyses WHERE id=?', (int(aid),)).fetchone()
        allowed = set((json.loads(row['metadata']).get('candidate_filter') or {}).get('species', []))
        with np.load(row['scores_path'], allow_pickle=False) as z:
            labels = z['labels'].astype(str); key = 'logits' if 'logits' in z else 'scores'; values = z[key]
            scores = softmax(values.astype(np.float64), axis=1) if key == 'logits' else values.astype(np.float64)
        keep = np.flatnonzero(np.isin(labels, list(allowed))) if allowed else np.arange(len(labels))
        top = np.argsort(-scores[:, keep], axis=1)[:, :3]
        for w in range(entry['end'] - entry['start']):
            out = []
            for j in top[w]:
                i = keep[j]; s = scores[w, i]
                if s < 0.05: break
                out.append(f"{ja.get(labels[i]) or labels[i]} {round(100 * s)}%")
            cands[entry['start'] + w] = out
    seg = {r['id']: r for r in db.execute('''SELECT s.id, r.path, s.start_s, s.end_s FROM segments s
        JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id WHERE s.id IN (SELECT value FROM json_each(?))''', (json.dumps([int(i) for i in ids]),))}
    names = [f"{Path(seg[int(i)]['path']).name} {seg[int(i)]['start_s']:.0f}–{seg[int(i)]['end_s']:.0f}秒" for i in ids]
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    nodes = build_tree(X, np.ones(len(X), int), proto.TAU)
    Y = embed_proto.pacmap(X)
    hinted = [bool(c) and int(c[0].rsplit(' ', 1)[1].rstrip('%')) >= 10 for c in cands]
    labels = [c[0].rsplit(' ', 1)[0] if h else '' for c, h in zip(cands, hinted)]
    return {'title': f'実演用（{len(X):,} 区間、AI の候補は日本の鳥に絞って上位 3）', 'labelKind': 'AI の候補', 'x': Y[:, 0].round(3).tolist(),
            'y': Y[:, 1].round(3).tolist(), 'labels': labels, 'cands': cands, 'names': names, 'nodes': mockdata.tree_json(nodes, np.arange(len(X)))}

data = {'demo': user(3), 'fuji': fuji()}
payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
out = ROOT / 'docs/prototypes/explore-hierarchy-mock.html'
out.write_text((S / 'hierarchy_mock_v21_template.html').read_text().replace('/*DATA*/', payload))
print('written', out, f'{out.stat().st_size/1e6:.1f} MB', {k: (len(v['x']), len(v['nodes']), sum(1 for c in v['cands'] if c)) for k, v in data.items()})
