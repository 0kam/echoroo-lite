#!/usr/bin/env python3
"""EVAL-ONLY Fuji comparison; never writes application state or training data.

Run with the repository .venv Python, --data-dir DATA --score-cache CACHE.
Only aggregate results are written to the repository; scores and repetitions
stay in CACHE. See the companion report for the preregistered interpretations.
"""
import os

# Each simulation worker uses one BLAS thread; inference retains app ORT settings.
for _name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[_name] = '1'

import argparse
import csv
import hashlib
import inspect
import json
import platform
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
import soundfile as sf
from scipy.special import softmax
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / 'apps/sidecar'
sys.path.insert(0, str(SIDECAR))
import analysis
import species
import validation as V
from models import REGISTRY

SEED = 20261029
G_DRAWS = 10_000
METHODS = ('C', 'A1', 'G')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def seed_of(*keys):
    return int(np.random.SeedSequence([SEED] + [zlib.crc32(str(k).encode()) for k in keys])
               .generate_state(1, dtype=np.uint64)[0])


def score_data(data, cache, models, windows, codes):
    """Decode whole clips, resample, and call the unmodified app inference path."""
    cache.mkdir(parents=True, exist_ok=True)
    inputs = {f: sha(data / f) for f in ('windows.parquet', 'clips.parquet',
                                        'segments.parquet', 'classes.json')}
    inputs['wav'] = {p.name: sha(p) for p in sorted((data / 'wav').glob('*.wav'))}
    code_names = {}
    for name, code in species.ebird_codes().items():
        code_names.setdefault(code, []).append(name)
    outputs, identities = {}, {}
    for engine in ('perch', 'kinglet-b'):
        label_path = models / REGISTRY[engine]['files'][1]
        if engine == 'perch':
            labels = analysis.perch_labels(models)
            mapped = {}
            for code in codes:
                for name in code_names.get(code, []):
                    try:
                        mapped[code] = analysis.perch_label_index(labels, name)
                        break
                    except Exception as exc:
                        if str(exc) != 'species_not_in_model':
                            raise
        else:
            with label_path.open(encoding='utf-8', newline='') as stream:
                rows = list(csv.DictReader(stream))
            labels = [r['scientific_name'] for r in rows]
            mapped = {r['ebird_code']: int(r['index']) for r in rows}
        missing = set(codes) - mapped.keys()
        if missing:
            raise ValueError(f'{engine}: unmapped Fuji codes {sorted(missing)}')
        identity = dict(engine=engine, inputs=inputs,
                        scoring_sha256=hashlib.sha256(inspect.getsource(score_data).encode()).hexdigest(),
                        model_sha256=sha(models / REGISTRY[engine]['files'][0]),
                        labels_sha256=sha(label_path),
                        app={f: sha(SIDECAR / f) for f in ('analysis.py', 'audio.py', 'models.py',
                                                          'species.py', 'reports.py')},
                        mapping_sha256=sha(SIDECAR / 'data/ebird-codes.json'),
                        aliases_sha256=sha(SIDECAR / 'data/japan-birds.json'),
                        mapping={c: dict(index=mapped[c], label=labels[mapped[c]]) for c in codes},
                        codes=codes, numpy=np.__version__, scipy=scipy.__version__,
                        soundfile=sf.__version__, onnxruntime=__import__('onnxruntime').__version__,
                        decode='whole_clip_sf_float32_app_resample_32000',
                        score_type=REGISTRY[engine]['score_type'])
        path = cache / f'{engine}.npz'
        if path.exists():
            try:
                with np.load(path, allow_pickle=False) as saved:
                    if json.loads(str(saved['identity'])) == identity:
                        scores = saved['scores']
                        assert np.array_equal(saved['window_id'], windows.window_id.to_numpy())
                        assert scores.shape == (len(windows), len(codes))
                        assert np.isfinite(scores).all() and ((0 <= scores) & (scores <= 1)).all()
                        outputs[engine], identities[engine] = scores, identity
                        print(f'{engine}: verified score cache', flush=True)
                        continue
            except (OSError, ValueError, KeyError, AssertionError):
                pass
        detector, model_sha = analysis.load_onnx(models, engine)
        assert model_sha == identity['model_sha256']
        scores = np.empty((len(windows), len(codes)), dtype=np.float64)
        started = time.monotonic()
        for number, (clip, group) in enumerate(windows.groupby('clip_id', sort=False), 1):
            native, rate = sf.read(data / 'wav' / (clip + '.wav'), dtype='float32')
            assert native.ndim == 1, 'This benchmark expects mono Fuji clips'
            audio = analysis.resample(native, rate)
            # App inference includes the partial last window; Fuji evaluates 36 complete windows.
            raw, _ = analysis.infer_onnx(detector, engine, audio, len(labels))
            if engine == 'perch':
                raw = softmax(raw.astype(np.float64), axis=1)
            indices = group.window_idx.to_numpy()
            assert np.array_equal(group.offset.to_numpy(), indices * 5.0)
            scores[group.index] = raw[indices][:, [mapped[c] for c in codes]]
            if number % 5 == 0:
                print(f'{engine}: {number}/60 clips, {time.monotonic()-started:.1f}s', flush=True)
        assert np.isfinite(scores).all() and ((0 <= scores) & (scores <= 1)).all()
        temporary = path.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            np.savez_compressed(stream, scores=scores, window_id=windows.window_id.to_numpy(dtype=str),
                                identity=json.dumps(identity, sort_keys=True))
        temporary.replace(path)
        outputs[engine], identities[engine] = scores, identity
        del detector
    return outputs, identities


def truth_arrays(data, windows, codes):
    y = np.array([[c in row for c in codes] for row in windows.labels], dtype=bool)
    keep = np.array([[c not in row for c in codes] for row in windows['mask']], dtype=bool)
    assert not (y & ~keep).any()
    keep &= ~windows.unknown.to_numpy()[:, None]
    half = np.zeros_like(y)
    segments = pd.read_parquet(data / 'segments.parquet')
    for clip, group in windows.groupby('clip_id', sort=False):
        seg = segments[segments.clip_id == clip]
        for row in group.itertuples():
            overlap = np.maximum(0, np.minimum(row.offset + 5, seg.etime) - np.maximum(row.offset, seg.stime))
            hit = seg[overlap >= np.minimum(.5, seg.etime - seg.stime) - 1e-9]
            labels = {c for lab in hit.labels for c in lab}
            half[row.Index] = [c in labels for c in codes]
    half &= y
    return y, keep, half


a1_strata = V.a1_strata


def curve_run(scores, answers, seed, method):
    strata = V.quantile_strata(scores, seed)[0] if method == 'C' else a1_strata(scores, seed)
    sampled = [i for i, _ in V.sample_indices(strata, seed ^ 0x5A17, 10)]
    top = scores[strata == 5]
    x0 = float(V.logit_scores(top).min()) if len(top) else float(V.logit_scores([.1])[0])

    def estimate(indices, verdicts):
        return V.estimate(scores, strata, indices, verdicts, x0, seed ^ 0xC0FFEE)

    result = estimate(sampled, answers[sampled])
    initial = len(sampled)
    if method == 'C' and V.extension_needed(result):
        sampled += [i for i, _ in V.sample_indices(strata, seed ^ 0xE17E, 3, sampled)]
        result = estimate(sampled, answers[sampled])
    # Only the requested precision fields leave the application estimator.
    return dict(threshold=result['threshold'], precision=result['precision'], reason=result['reason'],
                checkpoint='final', initial_answers=initial, answers=len(sampled),
                sampled=np.array(sampled), strata=strata, x0=x0)


def g_design(scores, seed):
    rng = np.random.default_rng(seed)
    order = np.lexsort((rng.permutation(len(scores)), -V.logit_scores(scores)))
    d = order[scores[order] >= .1]
    rest = order[scores[order] < .1]
    groups = [d] if len(d) <= 24 else list(np.array_split(d, 3))
    nd = len(groups)
    k = max(len(d), 25)
    groups += [rest[:k], rest[k:5*k], rest[5*k:]]
    samples = [rng.permutation(g)[:min(len(g), len(g) if i < nd and len(d) <= 24 else 8)]
               for i, g in enumerate(groups)]
    return groups, samples, nd


def g_draws(groups, samples, answers, seed):
    rng = np.random.default_rng(seed)
    totals = np.empty((G_DRAWS, len(groups)), dtype=np.int64)
    for h, (g, sample) in enumerate(zip(groups, samples)):
        a = answers[sample]
        pos, neg = int(np.sum(a == 'target')), int(np.sum(a == 'other'))
        unseen = len(g) - pos - neg
        totals[:, h] = pos
        if unseen:
            totals[:, h] += rng.binomial(unseen, rng.beta(pos + .5, neg + .5, G_DRAWS))
    return totals


def interval(values):
    return dict(value=float(np.median(values)), low=float(np.quantile(values, .05)),
                high=float(np.quantile(values, .95)))


def g_estimate(scores, groups, totals, limit, nd):
    candidates = []
    for h in range(limit):
        if not len(groups[h]):
            continue
        members = np.concatenate(groups[:h+1])
        threshold = .1 if h == nd - 1 else float(scores[groups[h]].min())
        # A score threshold cannot select only part of a cross-stratum tie.
        selected = scores >= threshold
        if selected.sum() != len(members) or not selected[members].all():
            continue
        draws = totals[:, :h+1].sum(axis=1) / len(members)
        if np.median(draws) >= .9:
            candidates.append((threshold, interval(draws)))
    if not candidates:
        return dict(threshold=None, precision=None, reason='target_unreachable')
    threshold, precision = min(candidates, key=lambda pair: pair[0])
    return dict(threshold=threshold, precision=precision, reason=None)


def g_run(scores, answers, seed):
    groups, planned, nd = g_design(scores, seed)
    samples = [s.copy() if h < nd else np.array([], dtype=int) for h, s in enumerate(planned)]
    m = sum(len(g) for g in groups[:nd])
    draws = g_draws(groups[:nd], samples[:nd], answers, seed ^ 0xC001)
    p0 = draws.sum(axis=1) / m if m else None
    lo, hi = np.quantile(p0, [.025, .975]) if m else (0., 1.)
    cp1 = m > 0 and (hi < .9 or lo >= .9)
    initial = sum(map(len, samples))
    if cp1:
        result = g_estimate(scores, groups, draws, nd, nd)
        result.update(checkpoint='CP1', b1_upper=None, b1_status='unassessed_early_stop')
    else:
        samples = [s.copy() for s in planned]
        draws = g_draws(groups, samples, answers, seed ^ 0xC002)
        p0 = draws[:, :nd].sum(axis=1) / m if m else None
        lo, hi = np.quantile(p0, [.025, .975]) if m else (0., 1.)
        if m and lo < .9 <= hi:
            rng = np.random.default_rng(seed ^ 0xE17E)
            for h in range(nd):
                remaining = np.setdiff1d(groups[h], samples[h])
                extra = rng.choice(remaining, min(4, len(remaining)), replace=False)
                samples[h] = np.concatenate([samples[h], extra])
            draws = g_draws(groups, samples, answers, seed ^ 0xC002)
        result = g_estimate(scores, groups, draws, len(groups), nd)
        result.update(checkpoint='CP2', b1_upper=int(np.quantile(draws[:, nd], .95, method='higher')),
                      b1_status='sampled' if len(groups[nd]) else 'empty')
    # Shadow B1 is never used to select/stop, and is not charged as an actual answer.
    # It tests the proposed bound at a fixed sample size independently of CP1 selection.
    shadow = g_draws([groups[nd]], [planned[nd]], answers, seed ^ 0xB100)
    result.update(b1_shadow_upper=int(np.quantile(shadow[:, 0], .95, method='higher')),
                  b1_members=groups[nd], b3_status='unassessable_design_based',
                  initial_answers=initial, answers=sum(map(len, samples)),
                  sampled=np.concatenate(samples), groups=groups, samples=samples, nd=nd)
    assert result['answers'] <= 60
    return result


def true_precision(scores, truth, threshold):
    if threshold is None:
        return None
    # Match score boundaries after the app's logit -> expit round trip.
    x, boundary = V.logit_scores(scores), float(V.logit_scores([threshold])[0])
    chosen = (x >= boundary) | np.isclose(x, boundary, rtol=0, atol=2e-14)
    return float(truth[chosen].mean()) if chosen.any() else None


def metric_row(scores, truth, answers, result, rep):
    p = result['precision']
    actual = true_precision(scores, truth, result['threshold'])
    row = dict(rep=rep, threshold=result['threshold'], reason=result['reason'],
               checkpoint=result['checkpoint'], answers=result['answers'],
               initial_answers=result['initial_answers'],
               sample_tp3=bool(truth[result['sampled']].sum() >= 3),
               answered_tp3=bool(np.sum(answers[result['sampled']] == 'target') >= 3),
               true_precision=actual, precision=p,
               covered=bool(p['low'] - 1e-12 <= actual <= p['high'] + 1e-12) if p and actual is not None else None,
               absolute_error=abs(p['value'] - actual) if p and actual is not None else None)
    if 'b1_members' in result:
        misses = int(truth[result['b1_members']].sum())
        upper = result['b1_upper']
        row.update(b1_true_misses=misses, b1_upper=upper,
                   b1_covered=bool(misses <= upper) if upper is not None else None,
                   b1_shadow_upper=result['b1_shadow_upper'],
                   b1_shadow_covered=bool(misses <= result['b1_shadow_upper']))
    return row


def flip_rows(scores, answers, result, seed, method):
    sampled = result['sampled']
    out = []
    for before, after in (('target', 'other'), ('other', 'target')):
        candidates = sampled[answers[sampled] == before]
        if not len(candidates):
            continue
        i = candidates[np.argmin(scores[candidates]) if before == 'target' else np.argmax(scores[candidates])]
        changed = answers.copy()
        changed[i] = after
        if method == 'G':
            # Fixed final sample: this sensitivity does not replay the stopping policy.
            totals = g_draws(result['groups'], result['samples'], changed,
                            seed ^ (0xC001 if result['checkpoint'] == 'CP1' else 0xC002))
            limit = result['nd'] if result['checkpoint'] == 'CP1' else len(result['groups'])
            altered = g_estimate(scores, result['groups'], totals, limit, result['nd'])
        else:
            altered = V.estimate(scores, result['strata'], sampled, changed[sampled],
                                 result['x0'], seed ^ 0xC0FFEE)
        p, q = result['precision'], altered['precision']
        out.append(dict(flip=before + '_to_' + after,
                        decided_changed=(result['threshold'] is None) != (altered['threshold'] is None),
                        precision_delta=abs(p['value'] - q['value']) if p and q else None,
                        threshold_logit_delta=abs(float(V.logit_scores([result['threshold']])[0]) -
                                                  float(V.logit_scores([altered['threshold']])[0]))
                        if p and q else None))
    return out


def average(values):
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else None


def median(values):
    values = [v for v in values if v is not None]
    return float(np.median(values)) if values else None


def summarize(rows, flips):
    return dict(repetitions=len(rows), tp3_rate=average([r['sample_tp3'] for r in rows]),
                answered_tp3_rate=average([r['answered_tp3'] for r in rows]),
                undecided_rate=average([r['threshold'] is None for r in rows]),
                precision_coverage=average([r['covered'] for r in rows]),
                precision_defined=sum(r['covered'] is not None for r in rows),
                precision_median_absolute_error=median([r['absolute_error'] for r in rows]),
                true_precision_median=median([r['true_precision'] for r in rows]),
                answers_median=median([r['answers'] for r in rows]),
                answers_max=max(r['answers'] for r in rows),
                extension_rate=average([r['answers'] > r['initial_answers'] for r in rows])
                if 'b1_upper' not in rows[0] else None,
                cp1_rate=average([r['checkpoint'] == 'CP1' for r in rows]),
                b1_report_rate=average([r.get('b1_upper') is not None for r in rows])
                if 'b1_upper' in rows[0] else None,
                b1_coverage=average([r.get('b1_covered') for r in rows]),
                b1_shadow_coverage=average([r.get('b1_shadow_covered') for r in rows]),
                flip_count=len(flips),
                flip_decided_change_rate=average([r['decided_changed'] for r in flips]),
                flip_precision_delta_median=median([r['precision_delta'] for r in flips]),
                flip_precision_delta_p90=float(np.quantile([r['precision_delta'] for r in flips
                                                            if r['precision_delta'] is not None], .9))
                if any(r['precision_delta'] is not None for r in flips) else None)


def simulate_species(task):
    engine, code, scores, truth, half, reps, conditions, flip_reps, destination = task
    summaries, raw, all_flips = [], [], {}
    for condition in conditions:
        by_method, flip_method = {m: [] for m in METHODS}, {m: [] for m in METHODS}
        for rep in range(reps):
            seed = seed_of('Fuji', engine, code, condition, rep)
            answers = np.where(truth, 'target', 'other').astype('<U7')
            if condition == 'boundary':
                edge = truth & ~half
                answers[edge & (np.random.default_rng(seed ^ 0xA115).random(len(truth)) < .5)] = 'unknown'
            for method in METHODS:
                result = g_run(scores, answers, seed) if method == 'G' else curve_run(scores, answers, seed, method)
                row = metric_row(scores, truth, answers, result, rep)
                by_method[method].append(row)
                raw.append(dict(condition=condition, method=method, **row))
                if condition == 'any' and rep < flip_reps:
                    flip_method[method] += flip_rows(scores, answers, result, seed, method)
        for method in METHODS:
            summaries.append(dict(engine=engine, code=code, condition=condition, method=method,
                                  **summarize(by_method[method], flip_method[method])))
        all_flips[condition] = flip_method
    save_json(destination, dict(rows=raw, flips=all_flips))
    return summaries


def sanity_checks():
    scores = np.linspace(.001, .999, 90)
    truth = scores > .5
    answers = np.where(truth, 'target', 'other')
    st = V.quantile_strata(scores, 1)[0]
    assert np.array_equal(st, V.quantile_strata(scores, 1)[0])
    indices = np.array([i for i, _ in V.sample_indices(st, 3, 10_000)])
    assert len(indices) == len(scores) == len(set(indices))
    r = V.estimate(scores, st, indices, answers[indices], float(V.logit_scores(scores[st == 5]).min()), 4)
    actual = true_precision(scores, truth, r['threshold'])
    assert r['precision']['low'] == r['precision']['high'] == actual
    for s in (scores, np.zeros(90), np.ones(90) * .2):
        groups, samples, nd = g_design(s, 3)
        members = np.concatenate(groups)
        assert len(members) == len(set(members)) == len(s)
        totals = g_draws(groups, groups, answers, 4)
        assert np.all(totals == [truth[g].sum() for g in groups])
        a1 = a1_strata(s, 3)
        assert ((0 <= a1) & (a1 <= 5)).all()
        if (s >= .1).sum() < (V.quantile_strata(s, 3)[0] == 5).sum():
            assert np.array_equal(a1 == 5, s >= .1)
    groups, _, nd = g_design(scores, 3)
    totals = g_draws(groups, groups, answers, 4)
    r = g_estimate(scores, groups, totals, len(groups), nd)
    actual = true_precision(scores, truth, r['threshold'])
    assert r['precision']['low'] == r['precision']['high'] == actual
    assert g_run(np.zeros(90), np.full(90, 'other'), 8)['threshold'] is None
    s = np.r_[np.linspace(.2, .9, 10), np.linspace(.001, .09, 80)]
    for verdict in ('target', 'other'):
        r = g_run(s, np.full(90, verdict), 8)
        assert r['checkpoint'] == 'CP1' and r['answers'] == 10 and r['b1_upper'] is None
        assert (r['threshold'] is None) == (verdict == 'other')
    print('Sanity checks: passed (census precision, partition, ties, zero detections, seeds)', flush=True)


def census_checks(scores, truth):
    answers = np.where(truth, 'target', 'other')
    for method in ('C', 'A1'):
        st = V.quantile_strata(scores, 9)[0] if method == 'C' else a1_strata(scores, 9)
        top = scores[st == 5]
        x0 = float(V.logit_scores(top).min()) if len(top) else float(V.logit_scores([.1])[0])
        r = V.estimate(scores, st, np.arange(len(scores)), answers, x0, 10)
        if r['threshold'] is not None:
            actual = true_precision(scores, truth, r['threshold'])
            assert abs(r['precision']['low'] - actual) < 1e-12
            assert abs(r['precision']['high'] - actual) < 1e-12
    groups, _, nd = g_design(scores, 9)
    totals = g_draws(groups, groups, answers, 10)
    assert np.all(totals == [truth[g].sum() for g in groups])
    r = g_estimate(scores, groups, totals, len(groups), nd)
    if r['threshold'] is not None:
        assert abs(r['precision']['value'] - true_precision(scores, truth, r['threshold'])) < 1e-12


def rare_sampling_check(reps):
    """Structural 42/21840 stress case; synthetic scores, not model evidence."""
    scores = np.r_[np.linspace(.2, .99, 42), np.linspace(.00001, .09, 21840-42)]
    truth = np.arange(len(scores)) < 42
    rows = []
    for method in METHODS:
        counts = []
        for rep in range(reps):
            seed = seed_of('synthetic_42_21840', rep)
            if method == 'G':
                _, samples, nd = g_design(scores, seed)
                sampled = np.concatenate(samples[:nd])
            else:
                st = V.quantile_strata(scores, seed)[0] if method == 'C' else a1_strata(scores, seed)
                sampled = [i for i, _ in V.sample_indices(st, seed ^ 0x5A17, 10)]
            counts.append(int(truth[sampled].sum()))
        rows.append(dict(method=method, repetitions=reps, zero_positive_rate=average([v == 0 for v in counts]),
                         tp3_rate=average([v >= 3 for v in counts]), mean_sample_positives=average(counts)))
    return rows


def aggregate(summaries, species_info):
    meta = {(r['engine'], r['code']): r for r in species_info if r['included']}
    out = []
    for engine in ('perch', 'kinglet-b'):
        for condition in sorted({r['condition'] for r in summaries}):
            for prevalence in ('all', 'common', 'medium', 'rare', 'auc_ge_0.9'):
                for method in METHODS:
                    rows = [r for r in summaries if r['engine'] == engine and r['condition'] == condition
                            and r['method'] == method and
                            (prevalence == 'all' or meta[engine, r['code']]['prevalence_class'] == prevalence
                             or prevalence == 'auc_ge_0.9' and meta[engine, r['code']]['roc_auc'] >= .9)]
                    if not rows:
                        continue
                    numeric = [k for k in rows[0] if k not in ('engine', 'code', 'condition', 'method')]
                    out.append(dict(engine=engine, condition=condition, prevalence_class=prevalence,
                                    method=method, species=len(rows),
                                    **{k: average([r[k] for r in rows]) for k in numeric}))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--score-cache', type=Path, required=True)
    parser.add_argument('--models-dir', type=Path, default=ROOT / 'poc/models')
    parser.add_argument('--score-only', action='store_true')
    parser.add_argument('--reps', type=int, default=200)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--conditions', default='any,boundary')
    parser.add_argument('--flip-reps', type=int, default=5)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/benchmarks/validation-fuji-2026-10-29.json')
    args = parser.parse_args()
    windows = pd.read_parquet(args.data_dir / 'windows.parquet').reset_index(drop=True)
    codes = [r['species'] for r in json.loads((args.data_dir / 'classes.json').read_text())]
    assert len(windows) == 2160 and windows.clip_id.nunique() == 60
    scores, identities = score_data(args.data_dir, args.score_cache, args.models_dir, windows, codes)
    if args.score_only:
        return
    assert args.reps > 0 and 1 <= args.workers <= 8 and 0 <= args.flip_reps <= args.reps
    conditions = args.conditions.split(',')
    assert set(conditions) <= {'any', 'boundary'}
    sanity_checks()
    y, keep, half = truth_arrays(args.data_dir, windows, codes)
    tasks, species_info = [], []
    config = dict(script_sha256=sha(__file__), validation_sha256=sha(SIDECAR / 'validation.py'),
                  reps=args.reps, conditions=conditions, flip_reps=args.flip_reps,
                  score_identities=identities, seed=SEED)
    run_id = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    runs = args.score_cache / 'simulation' / run_id
    summaries = []
    for engine, matrix in scores.items():
        for j, code in enumerate(codes):
            valid = keep[:, j]
            truth, s = y[valid, j], matrix[valid, j]
            pos, n = int(truth.sum()), len(truth)
            prevalence = pos / n
            auc = float((rankdata(s)[truth].sum() - pos * (pos+1)/2) / (pos * (n-pos))) if 0 < pos < n else None
            info = dict(engine=engine, code=code, windows=n, excluded=2160-n, positives=pos,
                        prevalence=prevalence, prevalence_class='common' if prevalence >= .05 else
                        'medium' if prevalence >= .01 else 'rare', included=pos >= 5,
                        roc_auc=auc, detections_at_0_1=int((s >= .1).sum()),
                        boundary_windows=int((truth & ~half[valid, j]).sum()))
            species_info.append(info)
            if not info['included']:
                continue
            census_checks(s, truth)
            destination = runs / f'{engine}-{code}.json'
            tasks.append((engine, code, s, truth, half[valid, j], args.reps, conditions,
                          args.flip_reps, destination))
    print(f'Real-data census checks: passed ({len(tasks)} species/model pairs)', flush=True)
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        from concurrent.futures import as_completed
        futures = {pool.submit(simulate_species, t): t[:2] for t in tasks}
        for count, future in enumerate(as_completed(futures), 1):
            summaries += future.result()
            print(f'Simulation: {count}/{len(tasks)} {futures[future]}, '
                  f'{time.monotonic()-started:.1f}s', flush=True)
    summaries.sort(key=lambda r: (r['engine'], r['code'], r['condition'], r['method']))
    result = dict(study='Fuji EVAL-ONLY validation simulation 2026-10-29', config=config,
                  environment=dict(python=platform.python_version(), platform=platform.platform(),
                                   numpy=np.__version__, scipy=scipy.__version__, pandas=pd.__version__),
                  elapsed_simulation_s=time.monotonic()-started, sanity_checks='passed',
                  real_census_pairs=len(tasks), synthetic_rare_sampling=rare_sampling_check(args.reps),
                  aggregation='equal species weight; MAE and answers are means of per-species medians',
                  species=species_info, summaries=summaries, aggregate=aggregate(summaries, species_info))
    save_json(args.output, result)
    print(f'Wrote {args.output}', flush=True)


if __name__ == '__main__':
    main()
