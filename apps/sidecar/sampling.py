"""Fixed acoustic strata and reproducible probability samples of complete intervals."""
from collections import Counter

import numpy as np
from scipy.cluster.vq import ClusterError, kmeans2, vq


def strata_for(groups, vectors, seed):
    """At most two acoustic strata per model-response group, plus missing vectors."""
    labels = np.full(len(groups), 'missing-embedding', dtype=object)
    rng = np.random.default_rng(seed)
    fits = {}
    valid = np.zeros(len(vectors), dtype=bool)
    for first in range(0, len(vectors), 4096):
        batch = vectors[first:first + 4096]
        valid[first:first + len(batch)] = np.isfinite(batch).all(axis=1) & (np.linalg.norm(batch, axis=1) > 0)
    for group in sorted(set(groups)):
        indexes = np.flatnonzero((groups == group) & valid)
        if not len(indexes):
            continue
        fit_indexes = rng.choice(indexes, min(2048, len(indexes)), replace=False)
        fitted = vectors[fit_indexes]
        if len(fitted) < 2 or np.all(fitted == fitted[0]):
            centers = fitted[:1]
        else:
            try:
                centers, _ = kmeans2(fitted, 2, iter=20, minit='++', missing='raise', rng=rng)
            except ClusterError:
                centers = fitted.mean(axis=0, keepdims=True)
        for first in range(0, len(indexes), 4096):
            batch = indexes[first:first + 4096]
            assigned, _ = vq(vectors[batch], centers)
            labels[batch] = [f'{group}-{int(value)}' for value in assigned]
        fits[group] = {'fit_indexes': fit_indexes.tolist(), 'centers': centers.tolist()}
    return labels, fits


def draw_plan(strata, count, seed):
    """Label-independent prefix allocations, random draws within each fixed stratum."""
    if not len(strata):
        raise ValueError('No complete 15-second intervals are available.')
    rng = np.random.default_rng(seed)
    keys = sorted(set(strata))
    members = {key: rng.permutation(np.flatnonzero(strata == key)).tolist() for key in keys}
    totals = {key: len(members[key]) for key in keys}
    proportions = {key: .5 * totals[key] / len(strata) + .5 / len(keys) for key in keys}
    draws = Counter()
    order = []
    initial = rng.permutation(keys).tolist()
    for position in range(min(count, len(strata))):
        eligible = [key for key in keys if draws[key] < totals[key]]
        key = initial[position] if position < len(initial) else max(
            eligible, key=lambda value: ((position + 1) * proportions[value] - draws[value], value))
        order.append(members[key][draws[key]])
        draws[key] += 1
    return order, [{'stratum': key, 'population': totals[key], 'sample': draws[key],
                    'inclusion_probability': draws[key] / totals[key],
                    'weight': totals[key] / draws[key] if draws[key] else None} for key in keys]
