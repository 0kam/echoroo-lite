"""numpy-only PaCMAP (Wang et al. 2021) vs PCA-2D vs openTSNE on PCA-50 embeddings; 10-NN label purity in 2D."""
import json, os, sqlite3, sys, tempfile, time
from collections import Counter
from pathlib import Path
import numpy as np
WHAT = sys.argv[1] if len(sys.argv) > 1 else 'all'  # fuji / user / scale / all

sys.path.insert(0, str(Path(__file__).parent))
import hierarchy_proto as proto
from proto import ROOT, pca
import explore

SEED = 20261101
DATA = Path(os.environ.get('ECHOROO_PROTO_DATA', ''))  # an Echoroo Lite data folder with built sound maps
OUT = Path(__file__).parent


def knn(X, k, block=1024):
    X = np.asarray(X, dtype=np.float32)
    n = len(X); norms = np.einsum('ij,ij->i', X, X)
    idx = np.empty((n, k), np.int32); dist = np.empty((n, k), np.float32)
    for s in range(0, n, block):
        e = min(s + block, n)
        D = norms[s:e, None] + norms[None, :] - 2 * (X[s:e] @ X.T)
        D[np.arange(e - s), np.arange(s, e)] = np.inf
        part = np.argpartition(D, k, axis=1)[:, :k]
        d = np.take_along_axis(D, part, 1)
        order = np.argsort(d, axis=1)
        idx[s:e] = np.take_along_axis(part, order, 1)
        dist[s:e] = np.sqrt(np.maximum(np.take_along_axis(d, order, 1), 0))
    return idx, dist


def pacmap(X, n_neighbors=10, mn_ratio=0.5, fp_ratio=2.0, iters=450, seed=SEED, init=None):
    X = np.asarray(X, dtype=np.float32); n = len(X)
    rng = np.random.default_rng(seed)
    k_cand = min(n_neighbors + 50, n - 1)
    idx, dist = knn(X, k_cand)
    sig = dist[:, 3:6].mean(axis=1) + 1e-10
    scaled = dist ** 2 / (sig[:, None] * sig[idx])
    order = np.argsort(scaled, axis=1)[:, :n_neighbors]
    nb_j = np.take_along_axis(idx, order, 1)
    n_mn, n_fp = int(round(n_neighbors * mn_ratio)), int(round(n_neighbors * fp_ratio))
    cand = rng.integers(0, n, size=(n, n_mn, 6))
    mn_j = np.empty((n, n_mn), np.int64)
    for s in range(0, n, 4096):  # second-closest of six random points, measured in the input space
        e = min(s + 4096, n)
        d = ((X[s:e, None, None, :] - X[cand[s:e]]) ** 2).sum(-1)
        second = np.argsort(d, axis=2)[:, :, 1]
        mn_j[s:e] = np.take_along_axis(cand[s:e], second[..., None], 2)[..., 0]
    fp_j = rng.integers(0, n, size=(n, n_fp))
    pairs = [(np.repeat(np.arange(n), n_neighbors), nb_j.ravel()),
             (np.repeat(np.arange(n), n_mn), mn_j.ravel()),
             (np.repeat(np.arange(n), n_fp), fp_j.ravel())]
    if init is None:
        Xc = X - X.mean(axis=0)
        _, _, Vt = np.linalg.svd(Xc[rng.choice(n, min(n, 5000), replace=False)], full_matrices=False)
        init = Xc @ Vt[:2].T
    Y = np.asarray(init, dtype=np.float64) * 0.01
    m = np.zeros_like(Y); v = np.zeros_like(Y)
    b1, b2, lr, eps = 0.9, 0.999, 1.0, 1e-7
    for it in range(iters):
        if it < 100:
            w_mn, w_nb, w_fp = 1000 * (1 - it / 100) + 3 * it / 100, 2.0, 1.0
        elif it < 200:
            w_mn, w_nb, w_fp = 3.0, 3.0, 1.0
        else:
            w_mn, w_nb, w_fp = 0.0, 1.0, 1.0
        grad = np.zeros_like(Y)
        for (i, j), weight, kind in zip(pairs, (w_nb, w_mn, w_fp), ('nb', 'mn', 'fp')):
            if not weight:
                continue
            diff = Y[i] - Y[j]
            d = 1 + (diff ** 2).sum(axis=1)
            if kind == 'nb':
                w = weight * 20 / (10 + d) ** 2
            elif kind == 'mn':
                w = weight * 20000 / (10000 + d) ** 2
            else:
                w = -weight * 2 / (1 + d) ** 2
            g = w[:, None] * diff
            for c in (0, 1):
                grad[:, c] += np.bincount(i, g[:, c], n) - np.bincount(j, g[:, c], n)
        m = b1 * m + (1 - b1) * grad
        v = b2 * v + (1 - b2) * grad ** 2
        Y -= lr * (m / (1 - b1 ** (it + 1))) / (np.sqrt(v / (1 - b2 ** (it + 1))) + eps)
    return Y


def pca2(X):
    Xc = X - X.mean(axis=0)
    _, _, Vt = np.linalg.svd(Xc, full_matrices=False)
    return Xc @ Vt[:2].T


def tsne(X):
    from openTSNE import TSNE
    return np.asarray(TSNE(perplexity=30, initialization='pca', random_state=SEED, n_jobs=4, verbose=False).fit(np.asarray(X, dtype=np.float64)))


def purity(Y, labels, mask, k=10):
    """Among labeled points, the share of each point's k nearest neighbours (in Y) that carry the same label."""
    P = np.asarray(Y[mask], dtype=np.float32); L = labels[mask]
    idx, _ = knn(P, min(k, len(P) - 1))
    return float((L[idx] == L[:, None]).mean())


def evaluate(name, X, labels, mask, save):
    print(f'\n=== {name}: n={len(X)}, labelled {mask.sum()}, classes {len(set(labels[mask]))} ===')
    results = {'50d (reference)': (X, 0.0)}
    for method, fn in (('PCA 2D', pca2), ('PaCMAP (numpy)', pacmap), ('openTSNE', tsne)):
        t = time.perf_counter(); Y = fn(X); results[method] = (Y, time.perf_counter() - t)
    out = {}
    for method, (Y, sec) in results.items():
        p = purity(Y, labels, mask)
        print(f'   {method:18s} 10-NN same-label {p:.3f}   {sec:5.1f}s')
        out[method] = {'purity': p, 'seconds': sec}
        if Y.shape[1] == 2:
            save[method] = np.asarray(Y, dtype=np.float32).round(3).tolist()
    return out


def fuji():
    import pandas as pd
    cache = ROOT / '.local-data/cluster-bench-20261101'
    matrix = np.load(cache / 'fuji-embeddings.npy', mmap_mode='r')
    frame = pd.read_parquet(cache / 'fuji-labels.parquet')
    truth = [json.loads(v) for v in frame['labels']]
    unknown = frame['unknown'].to_numpy(dtype=bool)
    labels = np.array([t[0] if len(t) == 1 else ('' if not t else '+'.join(t)) for t in truth], dtype=object)
    single = np.array([len(t) == 1 for t in truth]) & ~unknown
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    save = {'labels': labels.tolist(), 'unknown': unknown.tolist()}
    out = evaluate('Fuji (single-species windows)', X, labels, single, save)
    (OUT / 'embed-fuji.json').write_text(json.dumps(save))
    return out


def user(dataset_id):
    db = sqlite3.connect(f'file:{DATA / "echoroo.sqlite"}?mode=ro', uri=True); db.row_factory = sqlite3.Row
    run = db.execute('SELECT r.* FROM explore_runs r JOIN datasets d ON d.site_id=r.site_id WHERE d.id=?', (dataset_id,)).fetchone()
    params = json.loads(run['params'])
    index = json.loads((DATA / 'explore' / params['cache_index']).read_text())
    matrix = np.memmap(DATA / 'explore' / params['cache_index'].replace('.json', '.f32'), mode='r', dtype='<f4', shape=(index['rows'], 1536))
    maximum, names = proto.ai_hints(db, params, index, run['engine'])
    q = maximum >= explore.SCORE_THRESHOLD
    counts = Counter(names[q])
    mask = q & np.isin(names, [s for s, c in counts.items() if c >= 20])
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    save = {'labels': [n if ok else '' for n, ok in zip(names, mask)], 'score': maximum.round(3).tolist()}
    out = evaluate(f'dataset {dataset_id} (AI proxy, score>=0.1)', X, names, mask, save)
    (OUT / f'embed-dataset{dataset_id}.json').write_text(json.dumps(save))
    return out


if __name__ == '__main__':
    what = WHAT
    if what in ('all', 'fuji'): fuji()
    if what in ('all', 'user'):
        user(3); user(2)
