"""Perch call_density.py (quantile bins, k per bin, Beta(pos+.1, neg+.1), infinite population)
versus the threshold-anchored design + floor-logistic posterior of sim.py, on identical truths.
Metrics: density (= count / N), precision/recall at the fixed threshold and at the top-bin boundary,
and Strategy-1 per-slice density (day / night) with a truth where the species is silent at night.
"""
import sys
import numpy as np
from scipy.special import expit
import sim

HERE = sim.HERE
PERCH4 = np.array([0, .5, .75, .875, 1.0])
PERCH5 = np.array([0, .5, .75, .875, .9375, 1.0])


def perch_bins(x, q):
    edges = np.quantile(x, q)
    b = np.clip(np.searchsorted(edges[1:-1], x, side='right'), 0, len(q) - 2)
    return b, q[1:] - q[:-1]


def perch_sample(bins, k, rng):
    return np.concatenate([rng.choice(np.flatnonzero(bins == b), min(k, (bins == b).sum()), replace=False) for b in range(bins.max() + 1)])


def perch_estimate(x, y, bins, w, sampled, xt, order, slices, rng, D=2000):
    """Beta per bin as in call_density.py; recall/precision at t assume a constant rate inside a bin."""
    nb = len(w)
    pos = np.array([y[sampled][bins[sampled] == b].sum() for b in range(nb)])
    neg = np.array([(bins[sampled] == b).sum() for b in range(nb)]) - pos
    p = rng.beta(pos + .1, neg + .1, (D, nb))                      # (D, nb)
    density = p @ w
    above = x >= xt
    n_above_b = np.array([(above & (bins == b)).sum() for b in range(nb)])
    n_b = np.bincount(bins, minlength=nb)
    tp = p @ n_above_b
    total = p @ n_b
    top = bins == nb - 1
    out = {'density': density, 'count': total, 'precision': tp / max(above.sum(), 1), 'recall': tp / np.maximum(total, 1),
           'precision_top': p[:, -1], 'recall_top': p[:, -1] * n_b[-1] / np.maximum(total, 1)}
    for name, mask in slices.items():   # Strategy 1: sum_b P(+|b) P_slice(b)
        out['density_' + name] = p @ (np.array([(mask & (bins == b)).sum() for b in range(nb)]) / mask.sum())
    return out, np.concatenate([pos, neg])


def my_estimate(x, y, sampled, xt, order, slices, top_edge, rng):
    """bayes3 posterior predictive, plus the same extra metrics."""
    d = sim.fit_bayes(x, y, sampled, xt, True, rng, order)
    # recompute draws once more to get slice / top-boundary metrics (sim.fit_bayes returns summaries only)
    return d


def fit_bayes_full(x, y, sampled, xt, order, slices, top_edge, rng, D=600):
    xs, ys = x[sampled] - xt, y[sampled].astype(float)
    a = np.linspace(-7, 7, 57); b = np.linspace(0.02, 4, 30); lc = np.linspace(-11, -0.5, 36)
    A, B, C = [m.ravel() for m in np.meshgrid(a, b, lc, indexing='ij')]
    c = expit(C)
    p = np.clip(c[:, None] + (1 - c[:, None]) * expit(A[:, None] + B[:, None] * xs[None, :]), 1e-12, 1 - 1e-12)
    ll = (ys * np.log(p) + (1 - ys) * np.log1p(-p)).sum(1) - 0.5 * (A / 2.5) ** 2 - 0.5 * (B / 1.5) ** 2 - 0.5 * ((C - sim.FLOOR_MU) / 2) ** 2
    w = np.exp(ll - ll.max()); w /= w.sum(); keep = np.flatnonzero(w > 1e-7); w = w[keep] / w[keep].sum()
    g = rng.choice(len(w), D, p=w)
    P = c[keep][g, None] + (1 - c[keep][g, None]) * expit(A[keep][g, None] + B[keep][g, None] * (x[None, :] - xt))
    Y = rng.random(P.shape) < P
    Y[:, sampled] = y[sampled]
    m = sim.metrics(Y, x, xt, order)
    total = Y.sum(1)
    top = x >= top_edge
    m['density'] = total / len(x)
    m['precision_top'] = Y[:, top].sum(1) / max(top.sum(), 1)
    m['recall_top'] = Y[:, top].sum(1) / np.maximum(total, 1)
    for name, mask in slices.items():
        m['density_' + name] = Y[:, mask].sum(1) / mask.sum()
    return m


def truth_metrics(x, y, xt, order, slices, top_edge):
    m = {k: float(v[0]) for k, v in sim.metrics(y[None, :], x, xt, order).items()}
    m['density'] = y.mean()
    top = x >= top_edge
    m['precision_top'] = y[top].mean()
    m['recall_top'] = y[top].sum() / max(y.sum(), 1)
    for name, mask in slices.items():
        m['density_' + name] = y[mask].mean()
    return m


def run(scen, budgets=(40, 60, 80), reps=200, seed=1):
    rng = np.random.default_rng(seed)
    night = scen.endswith('_night')
    x, y, xt, _, _ = sim.scenario(scen.replace('_night', ''), rng)
    hour = np.load(HERE + 'jay_scores.npz')['hour'] if len(x) == 3840 else rng.integers(0, 24, len(x))
    slices = {'day': (hour >= 5) & (hour < 20), 'night': (hour < 5) | (hour >= 20)}
    if night:
        y = y & slices['day']
    order = np.argsort(-x)
    b4, w4 = perch_bins(x, PERCH4)
    b5, w5 = perch_bins(x, PERCH5)
    top_edge = float(np.quantile(x, .875))
    truth = truth_metrics(x, y, xt, order, slices, top_edge)
    strata = sim.strata_of(x, xt)
    N_h = np.bincount(strata, minlength=5)
    keys = ['density', 'recall', 'precision', 'precision_top', 'recall_top', 'density_day', 'density_night']
    print(f'\n=== {scen}: N={len(x)} t={expit(xt):.3f} top-bin edge score={expit(top_edge):.4f} '
          + ' '.join(f'{k}={truth[k]:.3f}' for k in keys), flush=True)
    for budget in budgets:
        n = sim.allocate(N_h, budget, 15, 'sqrt')
        res = {m: {k: [] for k in keys} for m in ('perch4', 'perch5', 'mine', 'design')}
        for r in range(reps):
            s4 = perch_sample(b4, budget // 4, rng)
            s5 = perch_sample(b5, budget // 5, rng)
            sm = sim.draw_sample(strata, n, rng)
            fits = {'perch4': perch_estimate(x, y, b4, w4, s4, xt, order, slices, rng)[0],
                    'perch5': perch_estimate(x, y, b5, w5, s5, xt, order, slices, rng)[0],
                    'mine': fit_bayes_full(x, y, sm, xt, order, slices, top_edge, rng)}
            d = sim.fit_design(x, y, sm, strata, xt, rng, order)
            d['density'] = d['count'] / len(x)
            fits['design'] = d
            for name, f in fits.items():
                for k in keys:
                    if k in f:
                        res[name][k].append((*sim.interval(f[k], .90), float(np.median(f[k]))))
        print(f'-- budget {budget}: perch4 k={budget // 4}, perch5 k={budget // 5}, mine alloc={n.tolist()}')
        for k in keys:
            for name in res:
                a = np.array(res[name][k])
                if not len(a):
                    continue
                cover = np.mean((a[:, 0] <= truth[k]) & (truth[k] <= a[:, 1]))
                print(f'   {k:14s} {name:7s} 90% cov={cover:.2f} width={np.median(a[:, 1] - a[:, 0]):.3f} '
                      f'median est={np.median(a[:, 2]):.3f} truth={truth[k]:.3f}', flush=True)


if __name__ == '__main__':
    run(sys.argv[1], reps=int(sys.argv[2]) if len(sys.argv) > 2 else 200)
