"""Implementation sketch for the sidecar (numpy/scipy only): grid posterior of a
floor+logistic curve on logit(score), posterior predictive over the finite
evaluation-day population, plus a stratum-wise Beta cross-check.

estimate(scores, sampled_index, verdicts, threshold, strata, seed) -> dict
Runtime: ~0.3 s for N=4000 windows, 80 answers (grid 57x30x36, 1000 draws).
"""
import numpy as np
from scipy.special import expit, logit

GRID_A = np.linspace(-7, 7, 57)          # logit P(call) at the threshold
GRID_B = np.linspace(0.02, 4, 30)        # slope per logit-score unit (monotone: b > 0)
FLOOR = expit(-11)                        # agreed effectively floorless curve
DRAWS = 1000


def curve(a, b, x):
    return FLOOR + (1 - FLOOR) * expit(a + b * x)


def posterior(x, y):
    """Grid weights for (a, b). x: logit score minus threshold logit; y: 1 heard / 0 not heard."""
    A, B = (m.ravel() for m in np.meshgrid(GRID_A, GRID_B, indexing='ij'))
    p = np.clip(curve(A[:, None], B[:, None], x[None, :]), 1e-12, 1 - 1e-12)
    log_post = (y * np.log(p) + (1 - y) * np.log1p(-p)).sum(1)
    log_post += -0.5 * (A / 2.5) ** 2 - 0.5 * (B / 1.5) ** 2
    w = np.exp(log_post - log_post.max())
    return A, B, w / w.sum()


def summarize(Y, x, xt, order):
    above = x >= xt
    tp, total = Y[:, above].sum(1), Y.sum(1)
    cum = np.cumsum(Y[:, order], 1)
    ap = (cum / np.arange(1, Y.shape[1] + 1) * Y[:, order]).sum(1) / np.maximum(total, 1)

    def iv(v):
        return {'median': float(np.median(v)), 'low90': float(np.quantile(v, .05)), 'high90': float(np.quantile(v, .95))}
    return {'precision': iv(tp / max(int(above.sum()), 1)), 'recall': iv(tp / np.maximum(total, 1)),
            'count': iv(total), 'auprc': iv(ap)}


def estimate(scores, sampled, verdicts, threshold, strata, seed=0):
    """scores: all evaluation-day windows; sampled: indices answered; verdicts: True heard / False not.
    Windows answered 'unknown' are left out of `sampled` (they stay unsampled, never negatives)."""
    rng = np.random.default_rng(seed)
    x = logit(np.clip(np.asarray(scores, float), 1e-9, 1 - 1e-9))
    xt = float(logit(threshold))
    y = np.zeros(len(x), bool)
    y[sampled] = verdicts
    order = np.argsort(-x)
    # model-based
    A, B, w = posterior(x[sampled] - xt, y[sampled].astype(float))
    g = rng.choice(len(w), DRAWS, p=w)
    P = curve(A[g, None], B[g, None], x[None, :] - xt)
    Y = rng.random(P.shape) < P
    Y[:, sampled] = y[sampled]
    model = summarize(Y, x, xt, order)
    # design-based cross-check: one Beta per stratum, Jeffreys prior
    Yd = np.zeros_like(Y)
    for h in np.unique(strata):
        members = np.flatnonzero(strata == h)
        s = members[np.isin(members, sampled)]
        ph = rng.beta(.5 + y[s].sum(), .5 + len(s) - y[s].sum(), DRAWS)
        Yd[:, members] = rng.random((DRAWS, len(members))) < ph[:, None]
    Yd[:, sampled] = y[sampled]
    design = summarize(Yd, x, xt, order)
    disagree = not (design['recall']['low90'] <= model['recall']['median'] <= design['recall']['high90'])
    return {'model': model, 'design': design, 'disagree': disagree}


if __name__ == '__main__':
    rng = np.random.default_rng(1)
    x = rng.normal(-6, 2, 4000)
    truth = rng.random(4000) < curve(1.5, 0.6, x)
    strata = np.digitize(x, [-6, -4, -2, 0])
    sampled = np.concatenate([rng.choice(np.flatnonzero(strata == h), min(15, (strata == h).sum()), replace=False) for h in range(5)])
    out = estimate(expit(x), sampled, truth[sampled], 0.5, strata)
    true_recall = truth[x >= 0].sum() / truth.sum()
    assert out['model']['recall']['low90'] <= true_recall <= out['model']['recall']['high90'], out
    print('ok', true_recall, out['model']['recall'], out['disagree'])
