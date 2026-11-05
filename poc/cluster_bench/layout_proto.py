"""Can the sound map be drawn better? Layout variants scored on the Fuji annotations, numpy/scipy only.

Usage (repository root, this folder's venv):
  poc/cluster_bench/.venv/bin/python poc/cluster_bench/layout_proto.py fuji        # layout variants, 3 seeds each
  poc/cluster_bench/.venv/bin/python poc/cluster_bench/layout_proto.py inputs      # input preprocessing, shipped PaCMAP
  poc/cluster_bench/.venv/bin/python poc/cluster_bench/layout_proto.py models      # Kinglet-B / AI class scores added
  poc/cluster_bench/.venv/bin/python poc/cluster_bench/layout_proto.py local       # LocalMAP (last phase of PaCMAP changed)
  ECHOROO_PROTO_DATA=<data folder> poc/cluster_bench/.venv/bin/python poc/cluster_bench/layout_proto.py demo 3   # figure + timing
Scores, averaged over seeds:
  purity  share of each single-species window's 10 nearest dots on the map that carry the same species
  F1      per species, the best node's F1 in the tree built from the map itself (proposal v4, M1); median and count >= 0.5
  global  Spearman correlation between species-centroid distances in the input space and on the map
"""
import json, os, sys, tempfile, time
from collections import Counter
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import eigsh
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps/sidecar'))
sys.path.insert(0, str(Path(__file__).parent))
import explore  # noqa: E402
import agreement_proto as agreement  # noqa: E402

OUT = Path(os.environ.get('ECHOROO_PROTO_OUT', ROOT / 'docs/benchmarks'))
SEEDS = (20261101, 7, 42)


# ---------- inputs ----------
EXTRA = ROOT / '.local-data/cluster-bench-20261101/fuji-extra.npz'  # from fuji_extra.py


def _pca(data, dims):
    if data.shape[1] != explore.EMBEDDING_DIMS:  # exact_pca takes embeddings only; small inputs use a plain SVD
        centered = data - data.mean(axis=0)
        return (centered @ np.linalg.svd(centered, full_matrices=False)[2][:dims].T).astype(np.float32)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'm.f32'; np.asarray(data, dtype='<f4').tofile(path)
        return np.asarray(explore.exact_pca(np.memmap(path, mode='r', dtype='<f4', shape=data.shape), Path(folder) / 'p.f32',
                                            dimensions=dims)[0], dtype=np.float32)


def _spread(data):
    return np.sqrt(data.var(axis=0).sum())


def features(matrix, kind, clips):
    """kind: steps joined by '+', applied in order. cos: rows to unit length (cosine geometry); center: subtract each
    recording's mean (its steady background); context: average with the windows before and after (0.25/0.5/0.25);
    pcaN: exact PCA to N dims (as shipped); dropK: drop the first K components; white / whitehalf: divide each
    component by its standard deviation / by its square root. kinglet: start from the Kinglet-B embeddings instead of
    Perch; pair: append the Kinglet-B PCA-50 at the same total spread; aiW: append the Perch Japan-list class scores
    (square roots, PCA-20) at W times the spread of what is there."""
    data = np.asarray(matrix, dtype=np.float32).copy()
    extra = np.load(EXTRA) if EXTRA.is_file() else None
    for step in kind.split('+'):
        if step == 'kinglet':
            data = extra['kinglet_embedding'].copy()
        elif step == 'pair':
            other = _pca(extra['kinglet_embedding'], 50)
            data = np.hstack([data, other * _spread(data) / _spread(other)])
        elif step.startswith('ai'):
            scores = _pca(np.sqrt(extra['perch_japan']), 20)
            data = np.hstack([data, scores * float(step[2:]) * _spread(data) / _spread(scores)])
        elif step == 'cos':
            data /= np.linalg.norm(data, axis=1, keepdims=True)
        elif step in ('center', 'context'):
            for clip in np.unique(clips):
                rows = np.flatnonzero(clips == clip)  # windows of one recording, in time order
                if step == 'center':
                    data[rows] -= data[rows].mean(axis=0)
                else:
                    padded = np.vstack([data[rows[:1]], data[rows], data[rows[-1:]]])
                    data[rows] = 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]
        elif step.startswith('pca'):
            data = _pca(data, int(step[3:]))
        elif step.startswith('drop'):
            data = data[:, int(step[4:]):]
        elif step == 'white':
            data /= data.std(axis=0)
        elif step == 'whitehalf':
            data /= np.sqrt(data.std(axis=0))
    return np.ascontiguousarray(data, dtype=np.float32)


# ---------- layouts ----------
def spectral_init(X, k=15):
    return agreement.spectral(X, dims=2, k=k)


def nearby(layout, nearest, old, far, threshold, rng, tries=1000, block=2048):
    """LocalMAP's further pairs: each dot's partners redrawn among dots within `threshold` on the map that are not its
    neighbours (official code: up to 100 tries per slot; here `tries` draws per dot, which fills all 20 slots for
    99% of Fuji's dots); slots left empty keep the old partner."""
    n = len(layout)
    partners = old.reshape(n, far).copy()
    for start in range(0, n, block):
        end = min(start + block, n); own = np.arange(start, end)
        candidates = rng.integers(0, n, size=(end - start, tries))
        close = np.linalg.norm(layout[candidates] - layout[own, None, :], axis=-1) <= threshold
        accepted = close & (candidates != own[:, None]) & ~(candidates[:, :, None] == nearest[own, None, :]).any(-1)
        rank = np.cumsum(accepted, axis=1)
        rows, columns = np.nonzero(accepted & (rank <= far))
        partners[start + rows, rank[rows, columns] - 1] = candidates[rows, columns]
    return partners.ravel()


def pacmap(X, seed, neighbors=10, mid=5, far=20, iters=450, init='pca', lr=1.0, local=None):
    """explore.pacmap with its knobs exposed (same schedule: three phases over 100/100/rest iterations).
    local: LocalMAP's low_dist_thres (Wang et al. 2024; official code defaults to 10). In the last phase, after
    iteration 200, neighbour attraction is scaled by (local / 2) / sqrt(d) and the further pairs are redrawn near
    each dot every 10 iterations, which sharpens the boundaries between nearby clusters."""
    X = np.asarray(X, dtype=np.float32); n = len(X)
    rng = np.random.default_rng(seed)
    neighbors = min(neighbors, n - 1); candidates = min(neighbors + 50, n - 1)
    indices, distances = explore.knn(X, candidates)
    sigma = distances[:, 3:6].mean(axis=1) + 1e-10
    scaled = distances ** 2 / (sigma[:, None] * sigma[indices])
    nearest = np.take_along_axis(indices, np.argsort(scaled, axis=1)[:, :neighbors], axis=1)
    choices = rng.integers(0, n, size=(n, mid, 6))
    mids = np.empty((n, mid), dtype=np.int64)
    for start in range(0, n, 4096):
        end = min(start + 4096, n)
        squared = ((X[start:end, None, None, :] - X[choices[start:end]]) ** 2).sum(axis=-1)
        mids[start:end] = np.take_along_axis(choices[start:end], np.argsort(squared, axis=2)[:, :, 1][..., None], axis=2)[..., 0]
    pairs = [(np.repeat(np.arange(n), neighbors), nearest.ravel()), (np.repeat(np.arange(n), mid), mids.ravel()),
             (np.repeat(np.arange(n), far), rng.integers(0, n, size=n * far))]
    start_layout = spectral_init(X) if init == 'spectral' else X[:, :2]
    start_layout = np.asarray(start_layout, dtype=np.float64)
    # A spectral start is scaled to the spread of the PCA start, so only the arrangement differs.
    layout = (start_layout / start_layout[:, 0].std() * X[:, 0].std() if init == 'spectral' else start_layout) * 0.01
    moment = np.zeros_like(layout); variance = np.zeros_like(layout)
    first, second = round(iters * 100 / 450), round(iters * 200 / 450)
    for iteration in range(iters):
        if iteration < first:
            weights = (2.0, 1000 * (1 - iteration / first) + 3 * iteration / first, 1.0)
        elif iteration < second:
            weights = (3.0, 3.0, 1.0)
        else:
            weights = (1.0, 0.0, 1.0)
        gradient = np.zeros_like(layout)
        for (source, target), weight, kind in zip(pairs, weights, ('near', 'mid', 'far')):
            if not weight:
                continue
            difference = layout[source] - layout[target]
            distance = 1 + np.einsum('ij,ij->i', difference, difference)
            coefficient = (weight * 20 / (10 + distance) ** 2 if kind == 'near' else
                           weight * 20000 / (10000 + distance) ** 2 if kind == 'mid' else -weight * 2 / (1 + distance) ** 2)
            if local and kind == 'near' and iteration > second:
                coefficient = coefficient * (local / 2) / np.sqrt(distance)
            contribution = coefficient[:, None] * difference
            for column in (0, 1):
                gradient[:, column] += np.bincount(source, contribution[:, column], n) - np.bincount(target, contribution[:, column], n)
        moment = 0.9 * moment + 0.1 * gradient
        variance = 0.999 * variance + 0.001 * gradient ** 2
        layout -= lr * (moment / (1 - 0.9 ** (iteration + 1))) / (np.sqrt(variance / (1 - 0.999 ** (iteration + 1))) + 1e-7)
        if local and iteration > second and iteration % 10 == 0:
            pairs[2] = (pairs[2][0], nearby(layout, nearest, pairs[2][1], far, local, rng))
    return layout.astype(np.float32)


def umap(X, seed, neighbors=15, epochs=300, negatives=5, a=1.577, b=0.895):
    """UMAP's fuzzy graph and loss (min_dist 0.1), optimised full-batch with Adam and fresh negative samples each epoch."""
    X = np.asarray(X, dtype=np.float32); n = len(X)
    rng = np.random.default_rng(seed)
    indices, distances = explore.knn(X, neighbors)
    rho = distances[:, 0]
    low, high = np.zeros(n), np.full(n, np.inf); sigma = np.ones(n)
    target = np.log2(neighbors)
    for _ in range(64):  # per-point bandwidth so that the memberships sum to log2(k)
        total = np.exp(-np.maximum(distances - rho[:, None], 0) / sigma[:, None]).sum(axis=1)
        above = total > target
        high = np.where(above, sigma, high); low = np.where(above, low, sigma)
        sigma = np.where(np.isinf(high), sigma * 2, (low + high) / 2)
    weights = np.exp(-np.maximum(distances - rho[:, None], 0) / sigma[:, None])
    graph = csr_matrix((weights.ravel(), (np.repeat(np.arange(n), neighbors), indices.ravel())), shape=(n, n))
    graph = (graph + graph.T - graph.multiply(graph.T)).tocoo()
    source, target_, weight = graph.row, graph.col, graph.data
    layout = np.asarray(spectral_init(X), dtype=np.float64)
    layout = 10 * layout / np.abs(layout).max()
    moment = np.zeros_like(layout); variance = np.zeros_like(layout)
    for epoch in range(epochs):
        difference = layout[source] - layout[target_]
        squared = np.einsum('ij,ij->i', difference, difference)
        attract = (-2 * a * b * squared ** (b - 1) / (1 + a * squared ** b))[:, None] * difference * weight[:, None]
        attract[squared == 0] = 0
        others = rng.integers(0, n, size=len(source) * negatives)
        heads = np.repeat(source, negatives)
        difference_n = layout[heads] - layout[others]
        squared_n = np.einsum('ij,ij->i', difference_n, difference_n)
        repel = (2 * b / ((0.001 + squared_n) * (1 + a * squared_n ** b)))[:, None] * difference_n
        repel = np.clip(repel, -4, 4) * np.repeat(weight, negatives)[:, None]
        gradient = np.zeros_like(layout)
        for column in (0, 1):  # the loss is minimised, so the step goes against (attraction + repulsion) as forces
            gradient[:, column] -= np.bincount(source, attract[:, column], n) + np.bincount(heads, repel[:, column], n)
        moment = 0.9 * moment + 0.1 * gradient
        variance = 0.999 * variance + 0.001 * gradient ** 2
        layout -= 0.05 * (moment / (1 - 0.9 ** (epoch + 1))) / (np.sqrt(variance / (1 - 0.999 ** (epoch + 1))) + 1e-7)
    return layout.astype(np.float32)


def tsne(X, seed, perplexity=30, iters=750):
    """Exact t-SNE (O(n^2); a reference only, too slow for the app's 10,000-point maps)."""
    X = np.asarray(X, dtype=np.float64); n = len(X)
    squared = ((X[:, None, :] - X[None]) ** 2).sum(-1)
    np.fill_diagonal(squared, np.inf)
    low, high = np.full(n, 1e-20), np.full(n, 1e20); beta = np.ones(n) / np.median(squared[np.isfinite(squared)])
    for _ in range(64):
        P = np.exp(-squared * beta[:, None]); P /= P.sum(1, keepdims=True)
        entropy = -(P * np.log(P + 1e-12)).sum(1)
        too_flat = entropy > np.log(perplexity)
        low = np.where(too_flat, beta, low); high = np.where(too_flat, high, beta)
        beta = np.where(high >= 1e20, beta * 2, (low + high) / 2)
    P = (P + P.T) / (2 * n)
    rng = np.random.default_rng(seed)
    Y = rng.normal(0, 1e-4, (n, 2)); gains = np.ones_like(Y); update = np.zeros_like(Y)
    for iteration in range(iters):
        exaggeration = 12 if iteration < 250 else 1
        distance = 1 / (1 + ((Y[:, None, :] - Y[None]) ** 2).sum(-1)); np.fill_diagonal(distance, 0)
        Q = distance / distance.sum()
        force = (exaggeration * P - Q) * distance
        gradient = 4 * ((np.diag(force.sum(1)) - force) @ Y)
        momentum = 0.5 if iteration < 250 else 0.8
        gains = np.where(np.sign(gradient) != np.sign(update), gains + 0.2, gains * 0.8).clip(0.01)
        update = momentum * update - 200 * gains * gradient
        Y += update
    return Y.astype(np.float32)


# ---------- scores ----------
def purity(Y, labels, mask, k=10):
    points = np.asarray(Y[mask], dtype=np.float32); kept = labels[mask]
    indices, _ = explore.knn(points, k)
    return float((kept[indices] == kept[:, None]).mean())


def silhouette(Y, labels, mask):
    """Mean silhouette of the single-species windows by their species (the LocalMAP paper's separation measure)."""
    points = np.asarray(Y[mask], dtype=np.float64); kept = labels[mask]
    names, codes = np.unique(kept, return_inverse=True)
    distance = np.sqrt(((points[:, None] - points[None]) ** 2).sum(-1))
    sums = np.stack([distance[:, codes == c].sum(1) for c in range(len(names))], axis=1)
    sizes = np.bincount(codes)
    own = sums[np.arange(len(kept)), codes] / np.maximum(sizes[codes] - 1, 1)
    others = np.where(np.arange(len(names))[None] == codes[:, None], np.inf, sums / sizes[None]).min(1)
    value = (others - own) / np.maximum(own, others)
    return float(np.where(sizes[codes] > 1, value, 0).mean())


def map_tree_f1(Y, positives):
    binary, root, _ = explore.binary_tree(np.asarray(Y, dtype=np.float32))
    nodes, order = explore.flatten_tree(binary, root, np.asarray(Y, dtype=np.float32))
    median, good, _ = agreement.fidelity(nodes, order, positives)
    return median, good


def global_order(X, Y, labels, mask):
    names = sorted(set(labels[mask]))
    if len(names) < 4:
        return float('nan')
    cx = np.stack([X[mask & (labels == s)].mean(0) for s in names]); cy = np.stack([Y[mask & (labels == s)].mean(0) for s in names])
    upper = np.triu_indices(len(names), 1)
    dx = np.sqrt(((cx[:, None] - cx[None]) ** 2).sum(-1))[upper]; dy = np.sqrt(((cy[:, None] - cy[None]) ** 2).sum(-1))[upper]
    return float(spearmanr(dx, dy).statistic)


def load_fuji():
    import pandas as pd
    cache = ROOT / '.local-data/cluster-bench-20261101'
    matrix = np.load(cache / 'fuji-embeddings.npy')
    frame = pd.read_parquet(cache / 'fuji-labels.parquet')
    truth = [json.loads(v) for v in frame['labels']]
    single = np.array([len(t) == 1 for t in truth]) & ~frame['unknown'].to_numpy(dtype=bool)
    labels = np.array([t[0] if len(t) == 1 else '' for t in truth], dtype=object)
    counts = Counter(name for t in truth for name in t)
    positives = {name: np.array([name in t for t in truth]) for name, c in counts.items() if c >= 5}
    return matrix, labels, single, positives, frame['clip'].to_numpy()


VARIANTS = {  # name: (features, layout function, options)
    'shipped: PaCMAP on PCA-50': ('pca50', 'pacmap', {}),
    'cosine PCA-50': ('cos+pca50', 'pacmap', {}),
    'cosine PCA-100': ('cos+pca100', 'pacmap', {}),
    'PCA-100': ('pca100', 'pacmap', {}),
    'neighbours 15': ('pca50', 'pacmap', {'neighbors': 15}),
    'neighbours 20': ('pca50', 'pacmap', {'neighbors': 20}),
    'neighbours 30': ('pca50', 'pacmap', {'neighbors': 30}),
    'far pairs 10 (less repulsion)': ('pca50', 'pacmap', {'far': 10}),
    'far pairs 40 (more repulsion)': ('pca50', 'pacmap', {'far': 40}),
    'mid-near 10': ('pca50', 'pacmap', {'mid': 10}),
    'iterations 900': ('pca50', 'pacmap', {'iters': 900}),
    'spectral start': ('pca50', 'pacmap', {'init': 'spectral'}),
    'UMAP-style (numpy)': ('pca50', 'umap', {}),
    't-SNE exact (reference)': ('pca50', 'tsne', {}),
}
FUNCTIONS = {'pacmap': pacmap, 'umap': umap, 'tsne': tsne}
INPUTS = {  # round 2: the shipped PaCMAP on preprocessed inputs
    'shipped: PCA-50': ('pca50', 'pacmap', {}),
    'recording mean removed': ('center+pca50', 'pacmap', {}),
    'cosine, recording mean removed': ('cos+center+pca50', 'pacmap', {}),
    'whitened': ('pca50+white', 'pacmap', {}),
    'half-whitened': ('pca50+whitehalf', 'pacmap', {}),
    'first component dropped': ('pca51+drop1', 'pacmap', {}),
    'first 2 components dropped': ('pca52+drop2', 'pacmap', {}),
    'neighbouring windows averaged': ('context+pca50', 'pacmap', {}),
    'mean removed + half-whitened': ('center+pca50+whitehalf', 'pacmap', {}),
}
LOCAL = {  # round 4: LocalMAP (the user's suggestion), on the shipped input
    'shipped: PaCMAP': ('pca50', 'pacmap', {}),
    'LocalMAP (threshold 10, official default)': ('pca50', 'pacmap', {'local': 10}),
    'LocalMAP threshold 5': ('pca50', 'pacmap', {'local': 5}),
    'LocalMAP threshold 20': ('pca50', 'pacmap', {'local': 20}),
    'LocalMAP threshold 10, half-whitened input': ('pca50+whitehalf', 'pacmap', {'local': 10}),
}
MODELS = {  # round 3: more information in the input (needs fuji_extra.py)
    'shipped: Perch PCA-50': ('pca50', 'pacmap', {}),
    'Kinglet-B embedding': ('kinglet+pca50', 'pacmap', {}),
    'Perch + Kinglet-B': ('pca50+pair', 'pacmap', {}),
    'Perch + AI class scores x0.5': ('pca50+ai0.5', 'pacmap', {}),
    'Perch + AI class scores x1': ('pca50+ai1', 'pacmap', {}),
    'Perch + AI class scores x2': ('pca50+ai2', 'pacmap', {}),
    'Perch + Kinglet-B + scores x1': ('pca50+pair+ai1', 'pacmap', {}),
}


def grid(variants):
    matrix, labels, single, positives, clips = load_fuji()
    cache = {}
    reference = {}
    rows = {}
    for name, (kind, function, options) in variants.items():
        if kind not in cache:
            cache[kind] = features(matrix, kind, clips)
            reference[kind] = purity(cache[kind], labels, single)
        X = cache[kind]; scores = []
        for seed in SEEDS:
            clock = time.perf_counter(); Y = FUNCTIONS[function](X, seed, **options); seconds = time.perf_counter() - clock
            median, good = map_tree_f1(Y, positives)
            scores.append((purity(Y, labels, single), median, good, global_order(X, Y, labels, single), seconds, silhouette(Y, labels, single)))
        scores = np.array(scores)
        mean, spread = scores.mean(0), scores.std(0)
        rows[name] = {'purity': round(mean[0], 3), 'purity_sd': round(spread[0], 3), 'f1_median': round(mean[1], 3), 'f1_sd': round(spread[1], 3),
                      'f1_ge_half': round(mean[2], 1), 'global': round(mean[3], 3), 'seconds': round(mean[4], 1), 'input_purity': round(reference[kind], 3),
                      'silhouette': round(mean[5], 3), 'silhouette_sd': round(spread[5], 3)}
        r = rows[name]
        print(f"{name:32s} purity {r['purity']:.3f}±{r['purity_sd']:.3f}  F1 {r['f1_median']:.3f}±{r['f1_sd']:.3f} (>=0.5 {r['f1_ge_half']:.1f}/{len(positives)})  "
              f"global {r['global']:.2f}  silhouette {r['silhouette']:.3f}±{r['silhouette_sd']:.3f}  {r['seconds']:.1f}s   [input {kind}: purity {r['input_purity']:.3f}]", flush=True)
    return rows


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else 'fuji'
    if target in ('fuji', 'inputs', 'models', 'local'):
        result = grid({'fuji': VARIANTS, 'inputs': INPUTS, 'models': MODELS, 'local': LOCAL}[target])
        (OUT / f'layout-proto-{target}-2026-11-05.json').write_text(json.dumps(result, ensure_ascii=False, indent=1))


def species_figure(path, chosen):
    """Fuji maps side by side, the eight commonest annotated species in colour, everything else grey (first seed)."""
    from PIL import Image, ImageDraw, ImageFont
    matrix, labels, single, positives, clips = load_fuji()
    names = [n for n, _ in Counter(labels[single]).most_common(8)]
    ja = {}
    font = ImageFont.truetype('/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc', 20)
    W, H, pad, columns = 440, 360, 14, 3
    rows = (len(chosen) + columns - 1) // columns
    canvas = Image.new('RGB', (columns * (W + pad) + pad, 70 + rows * (H + 40 + pad)), (255, 250, 243))
    draw = ImageDraw.Draw(canvas)
    legend_x = pad
    for i, name in enumerate(names):
        draw.rectangle([legend_x, 20, legend_x + 14, 34], fill=agreement.COLOURS[i]); draw.text((legend_x + 18, 14), name, fill=(55, 52, 80), font=font)
        legend_x += 30 + int(draw.textlength(name, font=font))
    for index, title in enumerate(chosen):
        kind, function, options = {**VARIANTS, **INPUTS, **MODELS}[title]
        Y = FUNCTIONS[function](features(matrix, kind, clips), SEEDS[0], **options)
        x0 = pad + (index % columns) * (W + pad); y0 = 60 + (index // columns) * (H + 40 + pad)
        draw.text((x0, y0), title, fill=(55, 52, 80), font=font)
        y0 += 32
        draw.rectangle([x0, y0, x0 + W, y0 + H], outline=(223, 218, 217), fill=(255, 255, 255))
        lo, hi = np.percentile(Y, 0.5, axis=0), np.percentile(Y, 99.5, axis=0)
        scale = min((W - 16) / (hi[0] - lo[0]), (H - 16) / (hi[1] - lo[1]))
        px = x0 + 8 + (Y[:, 0] - lo[0]) * scale; py = y0 + H - 8 - (Y[:, 1] - lo[1]) * scale
        colour = [agreement.COLOURS[names.index(l)] if s and l in names else None for l, s in zip(labels, single)]
        for x, y, c in sorted(zip(px, py, colour), key=lambda item: item[2] is not None):
            if x0 < x < x0 + W and y0 < y < y0 + H:
                draw.rectangle([x - 1.5, y - 1.5, x + 1.5, y + 1.5], fill=c or (220, 216, 214))
    canvas.save(path)


if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == 'figure':
    species_figure(OUT / 'layout-fuji-2026-11-05.png', ['shipped: PaCMAP on PCA-50', 'half-whitened', 'neighbours 20',
                   'UMAP-style (numpy)', 't-SNE exact (reference)', 'Kinglet-B embedding'])
