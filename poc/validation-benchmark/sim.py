"""Compare recall/AUPRC estimators from stratified listening samples.

Methods:
  current : random above threshold + random below (hypergeometric bounds as in precision_audit.py)
  design  : score strata, Jeffreys Beta per stratum, posterior predictive over unsampled windows
  bayes2  : logistic in logit(score), grid posterior, posterior predictive
  bayes3  : floor + logistic, grid posterior, posterior predictive
"""
import sys
from functools import lru_cache
from math import comb

import numpy as np
from scipy.special import expit, logit, gammaln

import os
PRIOR_C = float(os.environ.get("PRIOR_C", .5))
FLOOR_MU = float(os.environ.get("FLOOR_MU", -4))
HERE = '/private/tmp/claude-501/-Users-okamoto-NIES-Ecoacoustics-echoroo-lite/48e0e7d1-de57-450c-8438-05f55e0df93d/scratchpad/fable/'
LOGIT = lambda s: logit(np.clip(s, 1e-9, 1 - 1e-9))


# ---------- populations / truth ----------
def jay_population():
    z = np.load(HERE + 'jay_scores.npz')
    return LOGIT(z['eval']), LOGIT(z['calib']), float(logit(z['threshold']))


def scenario(name, rng):
    """Return x (logit scores), y (truth), x_t (threshold logit), x_cal, y_cal for calibration days."""
    xe, xc, xt = jay_population()
    if name == 'jay':
        f = lambda x: expit(1.59 + 0.576 * x)
        return xe, rng.random(len(xe)) < f(xe), xt, xc, rng.random(len(xc)) < f(xc)
    if name == 'nonmono':  # bump: extra calls at scores 0.001-0.007 (e.g. a call type BirdNET ignores)
        f = lambda x: np.clip(expit(1.59 + 0.576 * x) + 0.15 * ((x > -7) & (x < -5)), 0, 1)
        return xe, rng.random(len(xe)) < f(xe), xt, xc, rng.random(len(xc)) < f(xc)
    # mixture populations with the jay's size
    N = len(xe)
    pi, m1, s1, m0, s0 = {'common': (.25, 0, 2.5, -7, 1.5), 'rare': (.01, -1, 2.5, -7, 1.5)}[name]

    def draw(n):
        y = rng.random(n) < pi
        x = np.where(y, rng.normal(m1, s1, n), rng.normal(m0, s0, n))
        return x, y
    x, y = draw(N)
    xc_, yc_ = draw(len(xc))
    # threshold: lowest x with true precision >= .9 on the calibration population (what step 1 aims at)
    order = np.argsort(-xc_)
    prec = np.cumsum(yc_[order]) / np.arange(1, len(xc_) + 1)
    ok = np.flatnonzero(prec >= .9)
    xt_ = xc_[order][ok[-1]] if len(ok) else 0.0
    return x, y, xt_, xc_, yc_


# ---------- metrics ----------
def metrics(Y, x, xt, order):
    """Y: (D, N) truth draws. Returns precision(t), recall(t), AUPRC, count per draw."""
    above = x >= xt
    tp = Y[:, above].sum(1)
    tot = Y.sum(1)
    Ys = Y[:, order]
    cum = np.cumsum(Ys, 1)
    prec_k = cum / np.arange(1, Ys.shape[1] + 1)
    ap = (prec_k * Ys).sum(1) / np.maximum(tot, 1)
    return {'precision': tp / max(above.sum(), 1), 'recall': tp / np.maximum(tot, 1), 'auprc': ap, 'count': tot}


# ---------- designs ----------
def strata_of(x, xt, width=2.0, below=4):
    """0 = at/above threshold, 1..below = successive logit bands under it, last band open-ended."""
    s = np.zeros(len(x), dtype=int)
    for k in range(1, below + 1):
        lo = -np.inf if k == below else xt - k * width
        s[(x < xt - (k - 1) * width) & (x >= lo)] = k
    return s


def allocate(N_h, budget, above_cap, rule):
    n = np.zeros(len(N_h), dtype=int)
    n[0] = min(N_h[0], above_cap)
    rest = budget - n[0]
    below = np.arange(1, len(N_h))
    w = {'equal': np.ones(len(below)), 'sqrt': np.sqrt(N_h[below]), 'prop': N_h[below].astype(float)}[rule]
    w = w * (N_h[below] > 0)
    share = np.floor(rest * w / w.sum()).astype(int)
    share[np.argsort(-w)[:rest - share.sum()]] += 1
    n[below] = np.minimum(share, N_h[below])
    # leftover from small strata goes to the largest stratum
    n[np.argmax(N_h)] += min(rest - n[below].sum(), N_h[np.argmax(N_h)] - n[np.argmax(N_h)])
    return n


def draw_sample(strata, n, rng):
    idx = []
    for h, k in enumerate(n):
        members = np.flatnonzero(strata == h)
        idx.extend(rng.choice(members, k, replace=False) if k < len(members) else members)
    return np.array(idx, dtype=int)


# ---------- estimators ----------
def posterior_predictive(P, w, x, y, sampled, xt, order, rng, D=600):
    """P: (G, N) probabilities per grid point; w: grid weights. Sampled windows keep their known truth."""
    g = rng.choice(len(w), D, p=w)
    Y = rng.random((D, len(x))) < P[g]
    Y[:, sampled] = y[sampled]
    return metrics(Y, x, xt, order)


def fit_bayes(x, y, sampled, xt, floor, rng, order):
    xs, ys = x[sampled] - xt, y[sampled].astype(float)
    a = np.linspace(-7, 7, 57)
    b = np.linspace(0.02, 4, 30)
    lc = np.linspace(-11, -0.5, 36) if floor else np.array([-30.0])
    A, B, C = np.meshgrid(a, b, lc, indexing='ij')
    A, B, C = A.ravel(), B.ravel(), C.ravel()
    c = expit(C)
    p = c[:, None] + (1 - c[:, None]) * expit(A[:, None] + B[:, None] * xs[None, :])
    p = np.clip(p, 1e-12, 1 - 1e-12)
    ll = (ys * np.log(p) + (1 - ys) * np.log1p(-p)).sum(1)
    lp = -0.5 * (A / 2.5) ** 2 - 0.5 * (B / 1.5) ** 2 + (-0.5 * ((C - FLOOR_MU) / 2) ** 2 if floor else 0)
    w = np.exp(ll + lp - (ll + lp).max())
    w /= w.sum()
    keep = np.flatnonzero(w > 1e-7)  # drop negligible grid points before the N-wide expansion
    w = w[keep] / w[keep].sum()
    P = c[keep, None] + (1 - c[keep, None]) * expit(A[keep, None] + B[keep, None] * (x[None, :] - xt))
    return posterior_predictive(P, w, x, y, sampled, xt, order, rng)


def fit_design(x, y, sampled, strata, xt, rng, order, D=600):
    N = len(x)
    Y = np.zeros((D, N), dtype=bool)
    for h in np.unique(strata):
        members = np.flatnonzero(strata == h)
        s = members[np.isin(members, sampled)]
        k, n = y[s].sum(), len(s)
        p = rng.beta(PRIOR_C + k, PRIOR_C + n - k, D)
        Y[:, members] = rng.random((D, len(members))) < p[:, None]
    Y[:, sampled] = y[sampled]
    return metrics(Y, x, xt, order)


@lru_cache(maxsize=None)
def exact_count_interval(N, n, k, alpha):
    """Copied from apps/sidecar/validation.py."""
    if not n:
        return 0, N
    if n == N:
        return k, k
    denominator = comb(N, n)

    def tail(first, last, positive):
        return sum(comb(positive, x) * comb(N - positive, n - x)
                   for x in range(max(0, n - N + positive, first), min(n, positive, last) + 1)) / denominator
    left, right = k, N - n + k
    lo, hi = left, right
    while lo < hi:
        mid = (lo + hi) // 2
        if tail(k, n, mid) >= alpha / 2:
            hi = mid
        else:
            lo = mid + 1
    lower = lo
    lo, hi = left, right
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if tail(0, k, mid) >= alpha / 2:
            lo = mid
        else:
            hi = mid - 1
    return lower, lo


def current_method(x, y, xt, budget, rng):
    """precision_audit.py logic: n_above + n_below random, hypergeometric two-stratum bounds."""
    above, below = np.flatnonzero(x >= xt), np.flatnonzero(x < xt)
    na = min(len(above), budget // 2)
    nb = min(len(below), budget - na)
    sa, sb = rng.choice(above, na, replace=False), rng.choice(below, nb, replace=False)
    ka, kb = int(y[sa].sum()), int(y[sb].sum())
    out = {}
    for level, alpha in [('95', .025), ('90', .05)]:
        tp_lo, tp_hi = exact_count_interval(len(above), na, ka, alpha)
        fn_lo, fn_hi = exact_count_interval(len(below), nb, kb, alpha)
        lo = tp_lo / (tp_lo + fn_hi) if tp_lo + fn_hi else 0.0
        hi = tp_hi / (tp_hi + fn_lo) if tp_hi + fn_lo else 1.0
        out[level] = (lo, hi)
    tp = len(above) * ka / max(na, 1)
    fn = len(below) * kb / max(nb, 1)
    return out, tp / (tp + fn) if tp + fn else np.nan, na + nb


def interval(draws, level):
    q = (1 - level) / 2
    return np.quantile(draws, q), np.quantile(draws, 1 - q)


# ---------- simulation ----------
def run(scen, budgets=(40, 60, 80), reps=200, rule='sqrt', above_cap=15, seed=1):
    rng = np.random.default_rng(seed)
    x, y, xt, xc, yc = scenario(scen, rng)
    order = np.argsort(-x)
    truth = metrics(y[None, :], x, xt, order)
    truth = {k: float(v[0]) for k, v in truth.items()}
    strata = strata_of(x, xt)
    N_h = np.bincount(strata, minlength=5)
    print(f'\n=== {scen}: N={len(x)} threshold={expit(xt):.3f} strata N={N_h.tolist()} positives={int(y.sum())} '
          f'truth precision={truth["precision"]:.3f} recall={truth["recall"]:.3f} auprc={truth["auprc"]:.3f}', flush=True)
    rows = []
    for budget in budgets:
        n = allocate(N_h, budget, above_cap, rule)
        res = {m: {k: [] for k in ('recall', 'auprc', 'precision', 'count')} for m in ('current', 'design', 'bayes2', 'bayes3')}
        for r in range(reps):
            civ, _, _ = current_method(x, y, xt, budget, rng)
            res['current']['recall'].append({'95': civ['95'], '90': civ['90']})
            sampled = draw_sample(strata, n, rng)
            for name, draws in [('design', fit_design(x, y, sampled, strata, xt, rng, order)),
                                ('bayes2', fit_bayes(x, y, sampled, xt, False, rng, order)),
                                ('bayes3', fit_bayes(x, y, sampled, xt, True, rng, order))]:
                for k in res[name]:
                    res[name][k].append({'95': interval(draws[k], .95), '90': interval(draws[k], .90)})
        print(f'-- budget {budget}: allocation {n.tolist()}')
        for name in res:
            for k in ('recall', 'auprc', 'count'):
                ivs = res[name][k]
                if not ivs:
                    continue
                for level in ('90', '95'):
                    lo = np.array([iv[level][0] for iv in ivs]); hi = np.array([iv[level][1] for iv in ivs])
                    cover = np.mean((lo <= truth[k]) & (truth[k] <= hi))
                    width = np.median(hi - lo)
                    rows.append((scen, budget, name, k, level, cover, width, np.median(lo), np.median(hi)))
                    if level == '90' or k == 'recall':
                        print(f'   {name:8s} {k:9s} {level}%: coverage={cover:.2f} median width={width:.3f} '
                              f'median interval=[{np.median(lo):.3f},{np.median(hi):.3f}] truth={truth[k]:.3f}', flush=True)
    return rows


if __name__ == '__main__':
    scen = sys.argv[1]
    rule = sys.argv[2] if len(sys.argv) > 2 else 'sqrt'
    reps = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    rows = run(scen, rule=rule, reps=reps)
    np.save(HERE + f'rows_{scen}_{rule}.npy', np.array(rows, dtype=object), allow_pickle=True)
