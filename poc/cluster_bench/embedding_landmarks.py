"""Landmark PaCMAP: lay out a subsample, place the rest at the inverse-distance mean of their nearest landmarks."""
import json, os, sqlite3, sys, tempfile, time
from collections import Counter
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import hierarchy_proto as proto, embedding_proto as embed_proto
from proto import ROOT, pca
import explore

SEED = 20261101


def nearest_landmarks(X, L, k=5, block=2048):
    X = np.asarray(X, np.float32); L = np.asarray(L, np.float32)
    ln = np.einsum('ij,ij->i', L, L); idx = np.empty((len(X), k), np.int64); dist = np.empty((len(X), k), np.float32)
    for s in range(0, len(X), block):
        e = min(s + block, len(X))
        D = np.einsum('ij,ij->i', X[s:e], X[s:e])[:, None] + ln[None, :] - 2 * (X[s:e] @ L.T)
        part = np.argpartition(D, k, axis=1)[:, :k]
        d = np.take_along_axis(D, part, 1); o = np.argsort(d, 1)
        idx[s:e] = np.take_along_axis(part, o, 1); dist[s:e] = np.sqrt(np.maximum(np.take_along_axis(d, o, 1), 0))
    return idx, dist


def landmark_layout(X, count, seed=SEED):
    rng = np.random.default_rng(seed)
    land = np.sort(rng.choice(len(X), count, replace=False))
    t = time.perf_counter(); Y_land = embed_proto.pacmap(X[land]); t1 = time.perf_counter() - t
    t = time.perf_counter(); idx, dist = nearest_landmarks(X, X[land])
    w = 1 / (dist + 1e-3); w /= w.sum(1, keepdims=True)
    Y = np.einsum('nk,nkd->nd', w, Y_land[idx]); Y[land] = Y_land; t2 = time.perf_counter() - t
    return Y, t1, t2


def dataset2():
    DATA = embed_proto.DATA
    db = sqlite3.connect(f'file:{DATA / "echoroo.sqlite"}?mode=ro', uri=True); db.row_factory = sqlite3.Row
    run = db.execute('SELECT r.* FROM explore_runs r JOIN datasets d ON d.site_id=r.site_id WHERE d.id=2').fetchone()
    params = json.loads(run['params']); index = json.loads((DATA / 'explore' / params['cache_index']).read_text())
    matrix = np.memmap(DATA / 'explore' / params['cache_index'].replace('.json', '.f32'), mode='r', dtype='<f4', shape=(index['rows'], 1536))
    maximum, names = proto.ai_hints(db, params, index, run['engine'])
    q = maximum >= explore.SCORE_THRESHOLD; counts = Counter(names[q])
    mask = q & np.isin(names, [s for s, c in counts.items() if c >= 20])
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    print(f'=== dataset 2 n={len(X)}: full PaCMAP purity was 0.845 ===')
    for count in (2000, 5000):
        Y, t1, t2 = landmark_layout(X, count)
        print(f'   landmarks {count}: purity {embed_proto.purity(Y, names, mask):.3f}  layout {t1:.1f}s + placement {t2:.1f}s')


def scale():
    matrix = np.load(ROOT / '.local-data/cluster-bench-20261101/scale-210000.npy', mmap_mode='r')
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    for count in (10000, 20000):
        Y, t1, t2 = landmark_layout(X, count)
        print(f'=== scale n={len(X)}: landmarks {count}: layout {t1:.1f}s + placement {t2:.1f}s ===')


dataset2(); scale()
