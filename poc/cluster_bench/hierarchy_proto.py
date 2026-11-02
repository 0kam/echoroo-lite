"""Prototype of the hierarchical sound map: leaf k-means + Ward tree, flattened for drill-down.

Usage (from the repository root, with the cluster-bench venv):
  hierarchy_proto.py fuji                      # Fuji annotations (needs the 2026-11-01 bench cache)
  hierarchy_proto.py user --data DIR --datasets 3 2   # an Echoroo Lite data folder with built sound maps
  hierarchy_proto.py scale                     # timing on the synthetic 210,000-segment matrix
Metric: per species, the best single node's F1 (purity x recall), over all clusters (flat) or all nodes (tree).
"""
import argparse
import heapq, json, sqlite3, sys, tempfile, time
from collections import Counter
from pathlib import Path
import numpy as np
from scipy.cluster.hierarchy import linkage, to_tree

ROOT = Path('/Users/okamoto/NIES/Ecoacoustics/echoroo-lite')
sys.path.insert(0, str(ROOT / 'apps/sidecar'))
import explore  # noqa: E402

TAU = 0.6
MAX_CHILDREN = 8
FIT_MAX = 20_000


def leaf_count(n):
    return int(np.clip(n // 40, 8, 1000))


def pca(matrix, tmp):
    projected, info = explore.exact_pca(matrix, Path(tmp) / 'pca50.f32')
    return np.asarray(projected, dtype=np.float32)


def fit_leaves(X, k):
    rng = np.random.default_rng(explore.SEED)
    fit = X if len(X) <= FIT_MAX else X[np.sort(rng.choice(len(X), FIT_MAX, replace=False))]
    centers, _, _, iterations = explore.lloyd(fit, k)
    labels, squared = explore._nearest(X, centers)
    used, labels = np.unique(labels, return_inverse=True)
    centers = np.stack([X[labels == i].mean(axis=0) for i in range(len(used))])
    return centers.astype(np.float32), labels.astype(np.int32), np.sqrt(squared), iterations


def flatten(node, tau):
    """Children of a dendrogram node: maximal subtrees whose merge height < tau * node height, capped."""
    heap, counter = [], 0
    for child in (node.left, node.right):
        heap.append((-child.dist, counter, child)); counter += 1
    heapq.heapify(heap)
    while len(heap) < MAX_CHILDREN:
        d, _, top = heap[0]
        if top.is_leaf() or top.dist < tau * node.dist:
            break
        heapq.heappop(heap)
        for child in (top.left, top.right):
            heapq.heappush(heap, (-child.dist, counter, child)); counter += 1
    return [item[2] for item in sorted(heap)]


def build_tree(centers, sizes, tau):
    Z = linkage(centers, 'ward')
    root = to_tree(Z)
    nodes = []

    def visit(node, parent, depth):
        leaves = node.pre_order(lambda leaf: leaf.id)
        record = {'id': len(nodes), 'parent': parent, 'depth': depth, 'height': float(node.dist),
                  'leaves': leaves, 'size': int(sum(sizes[i] for i in leaves)), 'children': []}
        nodes.append(record)
        if not node.is_leaf():
            for child in flatten(node, tau):
                record['children'].append(visit(child, record['id'], depth + 1))
        return record['id']
    visit(root, None, 0)
    return nodes


def masks_for(nodes, leaf_labels):
    return [np.isin(leaf_labels, node['leaves']) for node in nodes]


def best_f1(masks, positives):
    out = {}
    for name, p in positives.items():
        best = (0.0, None)
        total = p.sum()
        for i, m in enumerate(masks):
            tp = int((m & p).sum())
            if not tp:
                continue
            prec, rec = tp / m.sum(), tp / total
            f1 = 2 * prec * rec / (prec + rec)
            if f1 > best[0]:
                best = (f1, i)
        out[name] = best
    return out


def summarize_f1(tag, result, nodes=None):
    values = np.array([v[0] for v in result.values()])
    line = f'{tag:34s} best-F1 median {np.median(values):.3f} mean {values.mean():.3f} ' \
           f'>=0.5: {int((values >= .5).sum())}/{len(values)}'
    if nodes is not None:
        depths = [nodes[v[1]]['depth'] for v in result.values() if v[1] is not None]
        line += f'  depth median {np.median(depths):.0f} max {max(depths)}'
    print(line)
    return {'median': float(np.median(values)), 'mean': float(values.mean()),
            'ge_half': int((values >= .5).sum()), 'n': len(values)}


def run_fuji():
    import pandas as pd
    cache = ROOT / '.local-data/cluster-bench-20261101'
    matrix = np.load(cache / 'fuji-embeddings.npy', mmap_mode='r')
    frame = pd.read_parquet(cache / 'fuji-labels.parquet')
    truth = [set(json.loads(v)) for v in frame['labels']]
    unknown = frame['unknown'].to_numpy(dtype=bool)
    background = np.array([not t for t in truth]) & ~unknown
    counts = Counter(n for t in truth for n in t)
    positives = {s: np.array([s in t for t in truth]) for s, c in counts.items() if c >= 5}
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    n = len(X)
    print(f'\n=== Fuji: n={n}, species>=5 windows: {len(positives)} ===')
    out = {}
    for k in (15, 25, leaf_count(n)):
        centers, labels, _, _ = fit_leaves(X, k)
        masks = [labels == i for i in range(len(centers))]
        out[f'flat_k{k}'] = summarize_f1(f'flat k-means k={k}', best_f1(masks, positives))
    t0 = time.perf_counter()
    centers, labels, dist, _ = fit_leaves(X, leaf_count(n))
    sizes = np.bincount(labels)
    for tau in (TAU, 0.0):
        nodes = build_tree(centers, sizes, tau)
        masks = masks_for(nodes, labels)
        tag = f'tree leaves={len(centers)} tau={tau}' + (' (binary)' if tau == 0 else '')
        out[f'tree_tau{tau}'] = summarize_f1(tag, best_f1(masks, positives), nodes)
        internal = [len(nd['children']) for nd in nodes if nd['children']]
        print(f'   nodes {len(nodes)}, internal {len(internal)}, children/node median {np.median(internal):.0f} '
              f'max {max(internal)}, depth max {max(nd["depth"] for nd in nodes)}')
        if tau == TAU:
            root = nodes[0]
            print('   root children (size, background%, top labels):')
            for cid in root['children']:
                m = masks[cid]
                lab = Counter(nm for i in np.flatnonzero(m) for nm in truth[i])
                print(f"     node {cid}: size {m.sum():4d}, background {100*background[m].mean():4.0f}%, "
                      f"{lab.most_common(3)}")
    print(f'   tree build (leaf fit + ward + flatten) {time.perf_counter()-t0:.1f}s')
    return out


def ai_hints(db, run_params, index, engine):
    maximum = np.empty(index['rows'], dtype=np.float32)
    names = np.empty(index['rows'], dtype=object)
    for analysis_id, entry in index['analyses'].items():
        row = db.execute('SELECT scores_path FROM analyses WHERE id=?', (int(analysis_id),)).fetchone()
        scores, winners = explore._score_rows(row[0], engine, entry['end'] - entry['start'])
        maximum[entry['start']:entry['end']] = scores
        names[entry['start']:entry['end']] = winners
    return maximum, names


def describe(mask, maximum, names, indent):
    q = mask & (maximum >= explore.SCORE_THRESHOLD)
    top = Counter(names[q]).most_common(2) if q.any() else []
    top = ', '.join(f'{n.split()[-1] if n else "?"} {100*c/max(q.sum(),1):.0f}%' for n, c in top)
    print(f'{indent}size {mask.sum():6d}  AI-hint rate {100*q.sum()/mask.sum():5.1f}%  mean score {maximum[mask].mean():.3f}  {top}')


def run_user(data, dataset_id):
    data = Path(data)
    db = sqlite3.connect(f'file:{data / "echoroo.sqlite"}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    run = db.execute('SELECT r.* FROM explore_runs r JOIN datasets d ON d.site_id=r.site_id WHERE d.id=?', (dataset_id,)).fetchone()
    params = json.loads(run['params'])
    index = json.loads((data / 'explore' / params['cache_index']).read_text())
    matrix = np.memmap(data / 'explore' / params['cache_index'].replace('.json', '.f32'), mode='r', dtype='<f4',
                       shape=(index['rows'], 1536))
    maximum, names = ai_hints(db, params, index, run['engine'])
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    t1 = time.perf_counter()
    k = leaf_count(len(X))
    centers, labels, dist, iterations = fit_leaves(X, k)
    t2 = time.perf_counter()
    nodes = build_tree(centers, np.bincount(labels), TAU)
    t3 = time.perf_counter()
    masks = masks_for(nodes, labels)
    print(f'\n=== dataset {dataset_id} ({run["engine"]}): n={len(X)}, leaves {len(centers)} '
          f'(app flat k={params["cluster_count"]}) | pca {t1-t0:.1f}s, leaves {t2-t1:.1f}s ({iterations} it), tree {t3-t2:.2f}s ===')
    internal = [len(nd['children']) for nd in nodes if nd['children']]
    print(f'   nodes {len(nodes)}, children/node median {np.median(internal):.0f} max {max(internal)}, depth max {max(nd["depth"] for nd in nodes)}')
    describe(np.ones(len(X), bool), maximum, names, '   all: ')

    def show(node_id, indent, depth_left):
        for cid in nodes[node_id]['children']:
            print(f'{indent}node {cid} (depth {nodes[cid]["depth"]}, {len(nodes[cid]["children"])} children):', end=' ')
            describe(masks[cid], maximum, names, '')
            if depth_left and nodes[cid]['children']:
                show(cid, indent + '    ', depth_left - 1)
    show(0, '   ', 1)
    # how many species does the AI see, and how pure can the tree get for them?
    q = maximum >= explore.SCORE_THRESHOLD
    counts = Counter(names[q])
    positives = {s: (names == s) & q for s, c in counts.items() if c >= 20}
    flat_masks = None
    out = {}
    for kk in (params['cluster_count'],):
        c2, l2, _, _ = fit_leaves(X, kk)
        flat_masks = [l2 == i for i in range(len(c2))]
    out['flat'] = summarize_f1(f'flat k={params["cluster_count"]} vs AI proxy ({len(positives)} spp)', best_f1(flat_masks, positives))
    out['tree'] = summarize_f1(f'tree tau={TAU} vs AI proxy', best_f1(masks, positives), nodes)
    return out


def run_scale():
    import resource
    matrix = np.load(ROOT / '.local-data/cluster-bench-20261101/scale-210000.npy', mmap_mode='r')
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        X = pca(matrix, tmp)
    t1 = time.perf_counter()
    centers, labels, _, iterations = fit_leaves(X, leaf_count(len(X)))
    t2 = time.perf_counter()
    nodes = build_tree(centers, np.bincount(labels), TAU)
    t3 = time.perf_counter()
    internal = [len(nd['children']) for nd in nodes if nd['children']]
    print(f'\n=== scale: n={len(X)} leaves={len(centers)} iterations={iterations} | pca {t1-t0:.1f}s, '
          f'leaf fit+assign {t2-t1:.1f}s, ward+flatten {t3-t2:.2f}s | nodes {len(nodes)}, children/node median '
          f'{np.median(internal):.0f} max {max(internal)}, depth max {max(nd["depth"] for nd in nodes)} | '
          f'peak rss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20:.0f} MiB ===')
    return {'pca_s': t1 - t0, 'leaves_s': t2 - t1, 'tree_s': t3 - t2, 'nodes': len(nodes)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('what', choices=('fuji', 'user', 'scale', 'all'))
    parser.add_argument('--tau', type=float, default=TAU)
    parser.add_argument('--data', help='Echoroo Lite data folder (for "user")')
    parser.add_argument('--datasets', type=int, nargs='*', default=[])
    args = parser.parse_args()
    TAU = args.tau
    results = {}
    if args.what in ('all', 'fuji'):
        results['fuji'] = run_fuji()
    if args.what in ('all', 'user') and args.data:
        for d in args.datasets:
            results[f'dataset{d}'] = run_user(args.data, d)
    if args.what in ('all', 'scale'):
        results['scale'] = run_scale()
    print(json.dumps(results, indent=1))
