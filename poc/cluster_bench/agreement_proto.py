"""Do the sound map's groups match what the map shows? Map agreement and species fidelity, per method.

Usage (repository root; the prototype venv, which has scikit-learn for M6 and Pillow for the figures):
  ECHOROO_PROTO_DATA=<an Echoroo Lite data folder with built sound maps> \
    poc/cluster_bench/.venv/bin/python poc/cluster_bench/agreement_proto.py fuji 3
Agreement: inside a shown group, the share of each dot's 10 nearest dots on its map that belong to the same smaller
group (the colours around a dot), averaged over dots at depth 0 (the whole map) and depth 1 (inside a top group).
The same measure in the 50-d PCA space shows how separable the groups are before any map is drawn.
It is also reported chance-corrected (as Cohen's kappa), since fewer or unequal groups agree more by chance.
Fidelity: per species, the best single node's F1 (as in hierarchy_exact.py).
Methods:
  M0 Ward on PCA-50, one global PaCMAP (the shipped v2.1 pipeline)
  M1 Ward on the global 2-D PaCMAP itself
  M2 Ward on a 10-D PaCMAP; map is the global 2-D PaCMAP
  M3 tree of M0; every group is shown on its own PaCMAP of its members (re-laid out when going inside)
  M4 tree of M0; global PaCMAP with extra attraction between dots of the same depth-2 group
  M5 Ward on a 20-D spectral embedding of the 15-NN graph; map is the global 2-D PaCMAP
  M6 Ward on PCA-50 restricted to merges between groups that touch on the map (10-NN graph of the map)
  M7 Ward on PCA-50 with the map coordinates appended, scaled to twice the PCA-50 spread (weight 2)
Map measures (also per depth): island purity, share of each group inside its largest island, badge accuracy.
"""
import json, os, sqlite3, sys, tempfile, time
from collections import Counter
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import eigsh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps/sidecar'))
import explore  # noqa: E402

DATA = Path(os.environ.get('ECHOROO_PROTO_DATA', ''))
OUT = Path(os.environ.get('ECHOROO_PROTO_OUT', ROOT / 'docs/benchmarks'))
K = 10
COLOURS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']


def pacmap(matrix, dims=2, extra=None, extra_weight=1.0):
    """explore.pacmap with a chosen output dimension and optional extra attractive pairs (source, target)."""
    matrix = np.asarray(matrix, dtype=np.float32)
    n = len(matrix)
    rng = np.random.default_rng(explore.SEED)
    neighbors = min(explore.PACMAP_NEIGHBORS, n - 1)
    candidates = min(neighbors + 50, n - 1)
    indices, distances = explore.knn(matrix, candidates)
    sigma = distances[:, 3:6].mean(axis=1) + 1e-10
    scaled = distances ** 2 / (sigma[:, None] * sigma[indices])
    nearest = np.take_along_axis(indices, np.argsort(scaled, axis=1)[:, :neighbors], axis=1)
    choices = rng.integers(0, n, size=(n, explore.PACMAP_MID_NEAR, 6))
    mid = np.empty((n, explore.PACMAP_MID_NEAR), dtype=np.int64)
    for start in range(0, n, 4096):
        end = min(start + 4096, n)
        squared = ((matrix[start:end, None, None, :] - matrix[choices[start:end]]) ** 2).sum(axis=-1)
        mid[start:end] = np.take_along_axis(choices[start:end], np.argsort(squared, axis=2)[:, :, 1][..., None], axis=2)[..., 0]
    further = rng.integers(0, n, size=(n, explore.PACMAP_FURTHER))
    pairs = [((np.repeat(np.arange(n), neighbors), nearest.ravel())), (np.repeat(np.arange(n), explore.PACMAP_MID_NEAR), mid.ravel()),
             (np.repeat(np.arange(n), explore.PACMAP_FURTHER), further.ravel())]
    kinds = ['near', 'mid', 'far']
    if extra is not None:
        pairs.append(extra); kinds.append('extra')
    layout = np.asarray(matrix[:, :dims], dtype=np.float64) * 0.01
    moment = np.zeros_like(layout); variance = np.zeros_like(layout)
    for iteration in range(explore.PACMAP_ITERATIONS):
        if iteration < 100:
            weights = [2.0, 1000 * (1 - iteration / 100) + 3 * iteration / 100, 1.0]
        elif iteration < 200:
            weights = [3.0, 3.0, 1.0]
        else:
            weights = [1.0, 0.0, 1.0]
        weights.append(extra_weight)
        gradient = np.zeros_like(layout)
        for (source, target), weight, kind in zip(pairs, weights, kinds):
            if not weight:
                continue
            difference = layout[source] - layout[target]
            distance = 1 + np.einsum('ij,ij->i', difference, difference)
            if kind in ('near', 'extra'):
                coefficient = weight * 20 / (10 + distance) ** 2
            elif kind == 'mid':
                coefficient = weight * 20000 / (10000 + distance) ** 2
            else:
                coefficient = -weight * 2 / (1 + distance) ** 2
            contribution = coefficient[:, None] * difference
            for column in range(dims):
                gradient[:, column] += np.bincount(source, contribution[:, column], n) - np.bincount(target, contribution[:, column], n)
        moment = 0.9 * moment + 0.1 * gradient
        variance = 0.999 * variance + 0.001 * gradient ** 2
        layout -= explore.PACMAP_LR * (moment / (1 - 0.9 ** (iteration + 1))) / (np.sqrt(variance / (1 - 0.999 ** (iteration + 1))) + 1e-7)
    return layout.astype(np.float32)


def layout(matrix, **options):
    """Global map as the app draws it: full PaCMAP up to LANDMARK_MAX points, landmarks above."""
    if len(matrix) <= explore.LANDMARK_MAX:
        return pacmap(matrix, **options)
    rng = np.random.default_rng(explore.SEED)
    marks = np.sort(rng.choice(len(matrix), explore.LANDMARK_MAX, replace=False))
    extra = options.pop('extra', None)
    if extra is not None:  # keep only pairs between landmarks, renumbered
        where = np.full(len(matrix), -1); where[marks] = np.arange(len(marks))
        keep = (where[extra[0]] >= 0) & (where[extra[1]] >= 0)
        options['extra'] = (where[extra[0][keep]], where[extra[1][keep]])
    placed = pacmap(matrix[marks], **options)
    indices, distances = explore.nearest_landmarks(matrix, matrix[marks])
    weights = 1 / (distances + 1e-3); weights /= weights.sum(axis=1, keepdims=True)
    out = np.einsum('nk,nkd->nd', weights, placed[indices]).astype(np.float32)
    out[marks] = placed
    return out


def spectral(matrix, dims=20, k=15):
    indices, distances = explore.knn(matrix, k)
    sigma = distances[:, -1] + 1e-10
    weights = np.exp(-distances ** 2 / (sigma[:, None] * sigma[indices]))
    n = len(matrix)
    graph = csr_matrix((weights.ravel(), (np.repeat(np.arange(n), k), indices.ravel())), shape=(n, n))
    graph = graph.maximum(graph.T)
    scale = 1 / np.sqrt(np.asarray(graph.sum(axis=1)).ravel())
    normal = graph.multiply(scale[:, None]).multiply(scale[None, :]).tocsr()
    values, vectors = eigsh(normal, k=dims + 1, which='LA')
    order = np.argsort(-values)[1:]
    return (vectors[:, order] * scale[:, None] * values[order]).astype(np.float32)


def tree(features):
    binary, root, _ = explore.binary_tree(features)
    nodes, order = explore.flatten_tree(binary, root, features)
    return nodes, order


def child_labels(nodes, node):
    labels = np.empty(node['hi'] - node['lo'], dtype=np.int32)
    for index, child in enumerate(node['children']):
        labels[nodes[child]['lo'] - node['lo']:nodes[child]['hi'] - node['lo']] = index
    return labels


def agreement(nodes, order, coordinates, depths=(0, 1)):
    """coordinates(node, rows) -> the map (or feature space) the node's dots are judged in."""
    out = {}
    for depth in depths:
        total = weight = chance = 0.0
        for node in nodes:
            if node['depth'] != depth or not node['children']:
                continue
            rows = order[node['lo']:node['hi']]
            labels = child_labels(nodes, node)
            indices, _ = explore.knn(coordinates(node, rows), min(K, len(rows) - 1))
            total += (labels[indices] == labels[:, None]).mean() * len(rows)
            chance += (np.bincount(labels) / len(rows)) @ np.bincount(labels)
            weight += len(rows)
        out[depth] = (total / weight, chance / weight)
    return out


def fidelity(nodes, order, positives):
    scores = []
    for name, positive in positives.items():
        cumulative = np.concatenate(([0], np.cumsum(positive[order])))
        total = positive.sum(); best = 0.0
        for node in nodes:
            tp = cumulative[node['hi']] - cumulative[node['lo']]
            if tp:
                precision, recall = tp / (node['hi'] - node['lo']), tp / total
                best = max(best, 2 * precision * recall / (precision + recall))
        scores.append(best)
    scores = np.array(scores)
    return float(np.median(scores)), int((scores >= .5).sum()), len(scores)


def same_group_pairs(nodes, order, per_point=5, depth=2):
    """For M4: each dot attracts a few random dots of its depth-2 group (or the deepest group above it)."""
    rng = np.random.default_rng(explore.SEED)
    group = np.zeros(len(order), dtype=np.int64)
    for node in nodes:  # deeper nodes overwrite, up to `depth`
        if node['depth'] <= depth:
            group[order[node['lo']:node['hi']]] = node['node']
    sources, targets = [], []
    for value in np.unique(group):
        members = np.flatnonzero(group == value)
        if len(members) < 2:
            continue
        sources.append(np.repeat(members, per_point)); targets.append(rng.choice(members, len(members) * per_point))
    return np.concatenate(sources), np.concatenate(targets)


def islands(points, k=K):
    """Visible islands of a map: dots joined to their 10 nearest dots when closer than 3x the median 10th-neighbour distance."""
    from scipy.sparse.csgraph import connected_components
    indices, distances = explore.knn(points, min(k, len(points) - 1))
    cut = 3 * np.median(distances[:, -1])
    keep = distances <= cut
    n = len(points)
    graph = csr_matrix((np.ones(keep.sum()), (np.repeat(np.arange(n), indices.shape[1])[keep.ravel()], indices[keep])), shape=(n, n))
    return connected_components(graph, directed=False)[1]


def map_measures(nodes, order, coordinates, depths=(0, 1)):
    """Per depth, size-weighted: island purity (an island shows one colour), group spread (a colour lies outside its
    largest island) and badge accuracy (a dot is nearest to its own group's numbered badge, drawn at the group's median)."""
    out = {}
    for depth in depths:
        sums = np.zeros(3); weight = 0
        for node in nodes:
            if node['depth'] != depth or not node['children']:
                continue
            rows = order[node['lo']:node['hi']]
            points = np.asarray(coordinates(node, rows), dtype=np.float32)
            labels = child_labels(nodes, node)
            component = islands(points)
            pure = sum(np.bincount(labels[component == c]).max() for c in np.unique(component))
            spread = sum((labels == l).sum() - np.bincount(component[labels == l]).max() for l in np.unique(labels))
            badges = np.stack([np.median(points[labels == l], axis=0) for l in range(labels.max() + 1)])
            nearest = np.argmin(((points[:, None, :] - badges[None]) ** 2).sum(-1), axis=1)
            sums += np.array([pure, len(rows) - spread, (nearest == labels).sum()])
            weight += len(rows)
        out[depth] = sums / weight
    return out


def constrained_ward(X, Y, k=K):
    """M6: Ward on PCA-50 where only groups that touch on the map may merge (scikit-learn, prototype venv only)."""
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.neighbors import kneighbors_graph
    model = AgglomerativeClustering(n_clusters=None, distance_threshold=0, linkage='ward', compute_full_tree=True,
                                    connectivity=kneighbors_graph(Y, k, include_self=False)).fit(X)
    n = len(X); counts = np.ones(2 * n - 1)
    heights = np.maximum.accumulate(model.distances_)  # connectivity can invert heights; keep the tree monotone
    Z = np.empty((n - 1, 4))
    for i, (a, b) in enumerate(model.children_):
        counts[n + i] = counts[a] + counts[b]
        Z[i] = (a, b, heights[i], counts[n + i])
    binary = ([-1] * n, [-1] * n, [0.0] * n, [1] * n)
    root = explore._append_linkage(binary, Z, range(n))
    binary = tuple(np.asarray(values) for values in binary)
    return explore.flatten_tree(binary, root, X)


def run(name, X, positives):
    print(f'\n=== {name}: n={len(X)}, species {len(positives)} ===', flush=True)
    seconds = {}

    def timed(key, function, *args, **options):
        clock = time.perf_counter(); value = function(*args, **options); seconds[key] = round(time.perf_counter() - clock, 1)
        return value
    Y = timed('global map', layout, X)
    trees = {'M0': timed('M0', tree, X), 'M1': timed('M1', tree, Y), 'M2': timed('M2', lambda: tree(layout(X, dims=10))),
             'M5': timed('M5', lambda: tree(spectral(X)))}
    trees['M6'] = timed('M6', constrained_ward, X, Y)
    # The 2-D map gets twice the total standard deviation of PCA-50 (weights 0.5-8 were swept on 2026-11-05; 2 is where agreement levels off).
    weight = 2 * np.sqrt(X.var(axis=0).sum() / Y.var(axis=0).sum())
    trees['M7'] = timed('M7', tree, np.hstack([X, weight * Y]).astype(np.float32))
    Y4 = timed('M4', layout, X, extra=same_group_pairs(*trees['M0']))
    local = {}

    def relaid(node, rows):  # M3: a group's own map; the whole map is the global one
        if node['depth'] == 0:
            return Y[rows]
        if node['node'] not in local:
            clock = time.perf_counter(); local[node['node']] = layout(X[rows])
            seconds['M3 group maps'] = round(seconds.get('M3 group maps', 0) + time.perf_counter() - clock, 1)
        return local[node['node']]
    shown = lambda n, r: Y[r]
    plan = {'M0': ('M0', shown), 'M1': ('M1', shown), 'M2': ('M2', shown), 'M3': ('M0', relaid),
            'M4': ('M0', lambda n, r: Y4[r]), 'M5': ('M5', shown), 'M6': ('M6', shown), 'M7': ('M7', shown)}
    result = {'n': len(X), 'species': len(positives), 'methods': {}}
    for method, (source, coordinates) in plan.items():
        nodes, order = trees[source]
        near = agreement(nodes, order, coordinates)
        measures = map_measures(nodes, order, coordinates)
        median, good, count = fidelity(nodes, order, positives)
        row = {'top_groups': len(nodes[0]['children']), 'nodes': len(nodes), 'f1_median': round(median, 3), 'f1_ge_half': good}
        for depth in (0, 1):
            agree, chance = near[depth]
            row[f'knn_kappa_d{depth}'] = round((agree - chance) / (1 - chance), 3)
            row[f'island_purity_d{depth}'], row[f'group_in_one_island_d{depth}'], row[f'badge_accuracy_d{depth}'] = (round(float(v), 3) for v in measures[depth])
        result['methods'][method] = row
        print(f"   {method}: islands one colour {row['island_purity_d0']:.3f}/{row['island_purity_d1']:.3f}  "
              f"group in one island {row['group_in_one_island_d0']:.3f}/{row['group_in_one_island_d1']:.3f}  "
              f"badge {row['badge_accuracy_d0']:.3f}/{row['badge_accuracy_d1']:.3f}  "
              f"kNN kappa {row['knn_kappa_d0']:.3f}/{row['knn_kappa_d1']:.3f}  "
              f"F1 median {median:.3f} >=0.5 {good}/{count}  top {row['top_groups']}", flush=True)
    result['seconds'] = seconds
    print('   seconds', seconds, flush=True)
    return result, {'Y': Y, 'Y4': Y4, 'trees': trees, 'local': local}


def fuji():
    import pandas as pd
    cache = ROOT / '.local-data/cluster-bench-20261101'
    matrix = np.load(cache / 'fuji-embeddings.npy', mmap_mode='r')
    truth = [set(json.loads(v)) for v in pd.read_parquet(cache / 'fuji-labels.parquet')['labels']]
    counts = Counter(name for labels in truth for name in labels)
    positives = {name: np.array([name in labels for labels in truth]) for name, c in counts.items() if c >= 5}
    with tempfile.TemporaryDirectory() as folder:
        X = np.asarray(explore.exact_pca(matrix, Path(folder) / 'pca.f32')[0], dtype=np.float32)
    return run('Fuji (annotated)', X, positives)


def user(dataset_id):
    """AI proxy labels: each interval's top Japan-list candidate with score >= 0.1, as stored with the sound map."""
    db = sqlite3.connect(f'file:{DATA / "echoroo.sqlite"}?mode=ro', uri=True); db.row_factory = sqlite3.Row
    run_row = db.execute('SELECT r.* FROM explore_runs r JOIN datasets d ON d.site_id=r.site_id WHERE d.id=?', (dataset_id,)).fetchone()
    params = json.loads(run_row['params'])
    index = json.loads((DATA / 'explore' / params['cache_index']).read_text())
    base = DATA / 'explore' / params['cache_index'].replace('.json', '')
    matrix = np.memmap(f'{base}.f32', mode='r', dtype='<f4', shape=(index['rows'], explore.EMBEDDING_DIMS))
    ids = np.memmap(f'{base}.i64', mode='r', dtype='<i8', shape=(index['rows'],))
    row_of = {int(segment): row for row, segment in enumerate(ids)}
    names = np.full(index['rows'], '', dtype=object)
    for point in db.execute('SELECT segment_id, candidates FROM explore_points WHERE run_id=?', (run_row['id'],)):
        top = json.loads(point['candidates'])
        if top and top[0][1] >= explore.SCORE_THRESHOLD and int(point['segment_id']) in row_of:
            names[row_of[int(point['segment_id'])]] = top[0][0]
    counts = Counter(n for n in names if n)
    positives = {name: names == name for name, c in counts.items() if c >= 20}
    with tempfile.TemporaryDirectory() as folder:
        X = np.asarray(explore.exact_pca(matrix, Path(folder) / 'pca.f32')[0], dtype=np.float32)
    return run(f'dataset {dataset_id} (AI proxy, Japan list, score >= 0.1)', X, positives)


def figure(path, state, title):
    """Rows: methods; columns: the whole map and the largest top group, coloured by their smaller groups."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype('/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc', 22)
    small = ImageFont.truetype('/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc', 18)
    W, H, pad, label = 520, 380, 16, 260
    rows = [('M0 いまの方式（50 次元の Ward ＋全体の地図）', 'M0', 'Y'), ('M1 地図の 2 次元座標で Ward', 'M1', 'Y'),
            ('M3 M0 の木＋まとまりごとに地図を作り直す', 'M0', 'local'), ('M4 M0 の木＋同じまとまりを引き寄せた地図', 'M0', 'Y4'),
            ('M5 近傍グラフのスペクトル埋め込みで Ward', 'M5', 'Y'), ('M6 50 次元の Ward、地図で隣り合うものだけ合併', 'M6', 'Y'),
            ('M7 50 次元＋地図の座標（重み 2）で Ward', 'M7', 'Y')]
    canvas = Image.new('RGB', (label + 2 * W + 3 * pad, 60 + len(rows) * (H + pad)), (255, 250, 243))
    draw = ImageDraw.Draw(canvas)
    draw.text((pad, 16), title, fill=(55, 52, 80), font=font)
    for r, (caption, method, kind) in enumerate(rows):
        nodes, order = state['trees'][method]
        top = max(nodes[0]['children'], key=lambda c: nodes[c]['hi'] - nodes[c]['lo'])
        y0 = 60 + r * (H + pad)
        for line, text in enumerate([caption[:14], caption[14:30], caption[30:]]):
            draw.text((pad, y0 + 20 + line * 26), text, fill=(55, 52, 80), font=small)
        for c, shown in enumerate((nodes[0], nodes[top])):
            rows_shown = order[shown['lo']:shown['hi']]
            if kind == 'local' and shown['depth'] > 0:
                points, context = state['local'][shown['node']], None
            else:
                full = state[kind if kind != 'local' else 'Y']
                points, context = full[rows_shown], full
            lo, hi = np.percentile(points, 1, axis=0), np.percentile(points, 99, axis=0)
            scale = min((W - 20) / max(hi[0] - lo[0], 1e-9), (H - 20) / max(hi[1] - lo[1], 1e-9))
            x0 = label + pad + c * (W + pad)
            draw.rectangle([x0, y0, x0 + W, y0 + H], outline=(223, 218, 217), fill=(255, 255, 255))
            to = lambda p: (x0 + 10 + (p[:, 0] - lo[0]) * scale, y0 + H - 10 - (p[:, 1] - lo[1]) * scale)
            if context is not None and shown['depth'] > 0:
                gx, gy = to(context)
                for x, y in zip(gx, gy):
                    if x0 < x < x0 + W and y0 < y < y0 + H:
                        draw.point((x, y), fill=(225, 222, 220))
            labels = child_labels(nodes, shown)
            px, py = to(points)
            for x, y, l in zip(px, py, labels):
                if x0 < x < x0 + W and y0 < y < y0 + H:
                    draw.rectangle([x - 1, y - 1, x + 1, y + 1], fill=COLOURS[l % len(COLOURS)])
            draw.text((x0 + 8, y0 + 6), '全体' if c == 0 else f"まとまり {shown['name']} の中", fill=(105, 101, 131), font=small)
    canvas.save(path)


if __name__ == '__main__':
    results = {}
    for target in sys.argv[1:] or ['fuji']:
        result, state = fuji() if target == 'fuji' else user(int(target))
        results[target] = result
        figure(OUT / f'agreement-{target}-2026-11-05.png', state, f"{'富士' if target == 'fuji' else f'dataset {target}'}：色はいま見ているまとまりの中の小さなまとまり")
    (OUT / 'agreement-proto-2026-11-05.json').write_text(json.dumps(results, ensure_ascii=False, indent=1))
