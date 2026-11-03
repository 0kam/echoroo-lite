"""Exact Ward on all points vs k-means leaves + Ward; complete trees down to single sounds."""
import json, os, sqlite3, sys, tempfile, time
from collections import Counter
from pathlib import Path
import numpy as np
from scipy.cluster.hierarchy import linkage, to_tree
WHAT = sys.argv[1] if len(sys.argv) > 1 else 'all'  # fuji / user / scale / all

sys.path.insert(0, str(Path(__file__).parent))
import hierarchy_proto as proto
from proto import ROOT, TAU, pca, fit_leaves, build_tree, leaf_count, summarize_f1
import explore

DATA = Path(os.environ.get('ECHOROO_PROTO_DATA', ''))  # an Echoroo Lite data folder with built sound maps


def node_members(nodes, leaf_labels):
    by_leaf = [np.flatnonzero(leaf_labels == l) for l in range(leaf_labels.max() + 1)]
    return [np.concatenate([by_leaf[l] for l in node['leaves']]) for node in nodes]


def best_f1_members(members, positives):
    out = {}
    for name, p in positives.items():
        total = p.sum(); best = (0.0, None)
        for i, m in enumerate(members):
            tp = int(p[m].sum())
            if not tp:
                continue
            prec, rec = tp / len(m), tp / total
            f1 = 2 * prec * rec / (prec + rec)
            if f1 > best[0]:
                best = (f1, i)
        out[name] = best
    return out


def report(tag, nodes, members, positives, seconds):
    res = best_f1_members(members, positives)
    summary = summarize_f1(tag, res, nodes)
    internal = [len(n['children']) for n in nodes if n['children']]
    print(f'   nodes {len(nodes)}, children/node median {np.median(internal):.0f} max {max(internal)}, '
          f'depth max {max(n["depth"] for n in nodes)}, build {seconds:.1f}s')
    return summary


def exact(X):
    t = time.perf_counter()
    nodes = build_tree(X, np.ones(len(X), int), TAU)
    return nodes, np.arange(len(X)), time.perf_counter() - t


def leaves_then_ward(X, k):
    t = time.perf_counter()
    centers, labels, _, _ = fit_leaves(X, k)
    nodes = build_tree(centers, np.bincount(labels), TAU)
    return nodes, labels, time.perf_counter() - t


def within_leaf_trees(X, labels):
    """Exact Ward inside every leaf, so the tree continues down to single sounds."""
    t = time.perf_counter(); count = 0
    for l in range(labels.max() + 1):
        members = np.flatnonzero(labels == l)
        if len(members) > 2:
            linkage(X[members], 'ward'); count += 1
    return time.perf_counter() - t, count


def fuji():
    import pandas as pd
    cache = ROOT / '.local-data/cluster-bench-20261101'
    matrix = np.load(cache / 'fuji-embeddings.npy', mmap_mode='r')
    frame = pd.read_parquet(cache / 'fuji-labels.parquet')
    truth = [set(json.loads(v)) for v in frame['labels']]
    counts = Counter(n for t in truth for n in t)
    positives = {s: np.array([s in t for t in truth]) for s, c in counts.items() if c >= 5}
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    print(f'\n=== Fuji n={len(X)} ===')
    out = {}
    for tag, (nodes, labels, sec) in {
            'exact Ward, all points': exact(X),
            f'leaves {leaf_count(len(X))} + Ward': leaves_then_ward(X, leaf_count(len(X))),
            'leaves 500 + Ward': leaves_then_ward(X, 500)}.items():
        out[tag] = report(tag, nodes, node_members(nodes, labels), positives, sec)
    return out


def user(dataset_id, exact_too=True):
    db = sqlite3.connect(f'file:{DATA / "echoroo.sqlite"}?mode=ro', uri=True); db.row_factory = sqlite3.Row
    run = db.execute('SELECT r.* FROM explore_runs r JOIN datasets d ON d.site_id=r.site_id WHERE d.id=?', (dataset_id,)).fetchone()
    params = json.loads(run['params'])
    index = json.loads((DATA / 'explore' / params['cache_index']).read_text())
    matrix = np.memmap(DATA / 'explore' / params['cache_index'].replace('.json', '.f32'), mode='r', dtype='<f4', shape=(index['rows'], 1536))
    maximum, names = proto.ai_hints(db, params, index, run['engine'])
    q = maximum >= explore.SCORE_THRESHOLD
    counts = Counter(names[q])
    positives = {s: (names == s) & q for s, c in counts.items() if c >= 20}
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    print(f'\n=== dataset {dataset_id} n={len(X)} ({len(positives)} AI-proxy spp) ===')
    out = {}
    variants = {}
    if exact_too:
        variants['exact Ward, all points'] = exact(X)
    variants[f'leaves {leaf_count(len(X))} + Ward'] = leaves_then_ward(X, leaf_count(len(X)))
    variants['leaves 2000 + Ward'] = leaves_then_ward(X, min(2000, len(X)))
    for tag, (nodes, labels, sec) in variants.items():
        out[tag] = report(tag, nodes, node_members(nodes, labels), positives, sec)
        if tag.startswith('leaves 2000'):
            sec2, count = within_leaf_trees(X, labels)
            print(f'   + exact Ward inside each of {count} leaves: {sec2:.1f}s')
    return out


def scale():
    matrix = np.load(ROOT / '.local-data/cluster-bench-20261101/scale-210000.npy', mmap_mode='r')
    with tempfile.TemporaryDirectory() as tmp:
        t = time.perf_counter(); X = pca(matrix, tmp); tp = time.perf_counter() - t
    print(f'\n=== scale n={len(X)} (pca {tp:.1f}s) ===')
    for k in (1000, 2000, 4000):
        t = time.perf_counter(); centers, labels, _, it = fit_leaves(X, k); t1 = time.perf_counter() - t
        t = time.perf_counter(); nodes = build_tree(centers, np.bincount(labels), TAU); t2 = time.perf_counter() - t
        t3, count = within_leaf_trees(X, labels)
        print(f'   leaves {k}: fit+assign {t1:.1f}s ({it} it), Ward on centers {t2:.2f}s, within-leaf trees {t3:.1f}s '
              f'(largest leaf {np.bincount(labels).max()}), nodes above leaves {len(nodes)}')


if __name__ == '__main__':
    what = WHAT
    if what in ('all', 'fuji'): fuji()
    if what in ('all', 'user'):
        user(3); user(2, exact_too=True)
    if what in ('all', 'scale'): scale()
