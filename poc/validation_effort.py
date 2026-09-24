"""Read-only calculations for the 80% precision / annotation-effort proposal.

This does not produce ground truth, estimate real model quality, or change the
application's evaluation policy. Optional overlap counts measure distinct clips,
not human annotation time. Run with the repository's existing Python environment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
from scipy.optimize import brentq
from scipy.stats import beta, binom, hypergeom


def pass_probability(p):
    """Stop at 20/20; otherwise pass at 28/30, with both looks fixed in advance."""
    return float(p ** 20 + binom.sf(27, 30, p) - p ** 20 * binom.sf(7, 10, p))


def finite_pass_probability(population, positives):
    early = hypergeom.pmf(20, population, positives, 20)
    final = hypergeom.sf(27, population, positives, 30)
    overlap = early * hypergeom.sf(7, population - 20, positives - 20, 10)
    return early + final - overlap


def statistical_examples():
    minimums = {}
    for target in (.8, .9):
        minimums[str(target)] = {
            str(errors): next(n for n in range(errors + 1, 1000)
                              if binom.sf(n - errors - 1, n, target) <= .05)
            for errors in range(3)
        }
    populations = np.arange(30, 10001)
    positives = populations * 4 // 5
    risks = finite_pass_probability(populations, positives)
    worst = int(risks.argmax())
    return {
        'single_fixed_look_95_one_sided_minimum_n_by_errors': minimums,
        'recommended_fixed_30': {
            'minimum_successes_large_population': 28,
            'lower_95_one_sided_at_28': float(beta.ppf(.05, 28, 3)),
            'audio_seconds_at_15s_per_clip': 450,
            'scenarios': [{'true_precision': p, 'probability_of_support': float(binom.sf(27, 30, p))}
                          for p in (.8, .85, .9, .95, .98)],
        },
        'two_look_20_then_30': {
            'null_precision': .8,
            'false_support_probability_binomial': pass_probability(.8),
            'early_20_of_20_stagewise_lower': .05 ** (1 / 20),
            'final_28_of_30_stagewise_lower': brentq(lambda p: pass_probability(p) - .05, .01, .99),
            'finite_null_grid': {
                'population_min': 30, 'population_max': 10000,
                'worst_population': int(populations[worst]),
                'maximum_false_support_probability': float(risks[worst]),
                'note': 'A finite grid is a check, not a proof for every population size.',
            },
            'scenarios': [
                {'true_precision': p, 'probability_of_support': pass_probability(p),
                 'expected_clips': 30 - 10 * p ** 20}
                for p in (.8, .85, .9, .95, .98)
            ],
        },
    }


def overlap_examples(data, session, threshold, repetitions):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/sidecar'))
    from reports import read_classification_windows, regional_list

    db = sqlite3.connect((data / 'echoroo.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    try:
        snapshot = json.loads(db.execute('SELECT snapshot FROM annotation_sessions WHERE id=?', (session,)).fetchone()[0])
        reserved = set(snapshot['evaluation_recording_ids'])
        records = {r['id']: dict(r) for r in snapshot['recordings'] if r['id'] in reserved}
        keys = [(rid, start) for rid, record in sorted(records.items())
                for start in range(0, int(record['seconds']) - 14, 15)]
        lookup = {key: i for i, key in enumerate(keys)}
        species = ('Tarsiger cyanurus', 'Horornis diphone', 'Phylloscopus xanthodryas')
        pools = {(name, engine): [] for name in species for engine in ('birdnet', 'perch')}
        source_ids = []
        for analysis in db.execute('''SELECT * FROM analyses WHERE id IN
                (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)
                AND engine IN ('birdnet','perch') ORDER BY recording_id,engine'''):
            rid = analysis['recording_id']
            if rid not in reserved:
                continue
            allowed = regional_list(db, analysis)
            if allowed is None:
                continue
            path = Path(analysis['scores_path'])
            stat = path.stat()
            signature = [stat.st_size, stat.st_mtime_ns, analysis['engine'], float(records[rid]['seconds']), tuple(sorted(allowed)), 1]
            digest = hashlib.sha256(json.dumps(signature).encode()).hexdigest()[:20]
            cached = path.with_name(path.name + '.' + digest + '.windows.npz')
            if cached.exists():
                with np.load(cached, allow_pickle=False) as saved:
                    labels, starts, scores = (saved[field] for field in ('labels', 'starts', 'scores'))
            else:
                # The uncached reader does not write a new derived cache.
                labels, starts, scores, _ = read_classification_windows(analysis, records[rid]['seconds'], allowed)
            source_ids.append(analysis['id'])
            for name in species:
                columns = np.flatnonzero(labels == name)
                if not len(columns):
                    continue
                selected = starts[scores[:, columns[0]] >= threshold]
                pools[name, analysis['engine']].extend(lookup[rid, int(start)] for start in selected)
        pools = {key: np.array(values, dtype=int) for key, values in pools.items() if values}
    finally:
        db.close()

    scenarios = []
    for sample in (20, 30):
        independent, coordinated = [], []
        for seed in range(repetitions):
            rng = np.random.default_rng(20260923 + seed)
            chosen = [rng.choice(pool, min(sample, len(pool)), replace=False) for pool in pools.values()]
            independent.append(len(np.unique(np.concatenate(chosen))))
            ranks = np.random.default_rng(20260923 + seed).random(len(keys))
            chosen = [pool[np.argsort(ranks[pool])[:sample]] for pool in pools.values()]
            coordinated.append(len(np.unique(np.concatenate(chosen))))
        baseline = sum(min(sample, len(pool)) for pool in pools.values())
        scenarios.append({
            'sample_per_species_model': sample, 'without_deduplication': baseline,
            'independent_with_deduplication_mean': float(np.mean(independent)),
            'shared_ranks_with_deduplication_mean': float(np.mean(coordinated)),
        })
    return {
        'purpose': 'Clip-overlap opportunity only; not model quality or measured human effort.',
        'annotation_session': session, 'evaluation_recording_ids': sorted(reserved),
        'analysis_ids': source_ids, 'population_15s_windows': len(keys),
        'illustrative_fixed_score_threshold': threshold,
        'threshold_note': 'Not a validated species-specific threshold.',
        'pool_sizes': [{'species': key[0], 'model': key[1], 'positives': len(pool)} for key, pool in pools.items()],
        'repetitions': repetitions, 'seed_start': 20260923, 'scenarios': scenarios,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--session', type=int, default=2)
    parser.add_argument('--threshold', type=float, default=.1)
    parser.add_argument('--repetitions', type=int, default=500)
    args = parser.parse_args()
    result = statistical_examples()
    if args.data:
        result['overlap'] = overlap_examples(args.data, args.session, args.threshold, args.repetitions)
    print(json.dumps(result, ensure_ascii=False, indent=2))
