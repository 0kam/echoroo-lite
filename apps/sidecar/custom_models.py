"""Small one-species classifiers over the main AI embeddings ("species AI").

Material: the species' labels from every screen, laid on 5-second pieces (`labels.on_pieces`). There is one species AI per site, species and period; making it
again replaces it in place. It scores and is checked on every 5-second piece of the site.
"""
import hashlib
import json
import os
import shutil
import tempfile
from bisect import bisect_left
from datetime import date
from itertools import islice, zip_longest
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from models import feature_spec, main_engine, main_identity
from reports import analysis_metadata, calendar_cells, effort, finalize_cells, window_in_scope, window_key
from labels import FROM_ANSWER, TO_ANSWER, corrected, current, on_pieces, put, ref, remove
from species import names_for
from storage import CatalogError, checked_source, connect

MIN_CLASS_EXAMPLES = 3
MIN_EXAMPLES = 20  # answers in all before an AI can be made (owner decision 2026-11-12)
L2 = 1.0


def _digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _normalise(matrix):
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if matrix.ndim != 2 or matrix.shape[1] != 1536 or not np.isfinite(matrix).all() or np.any(norms == 0):
        raise CatalogError('invalid_vectors')
    return matrix / norms


def _fit(x, y):
    def objective(value):
        weights, intercept = value[:-1], value[-1]
        logits = x @ weights + intercept
        return float((np.logaddexp(0, logits) - y * logits).mean() + L2 * np.dot(weights, weights) / (2 * len(y)))

    def gradient(value):
        weights, intercept = value[:-1], value[-1]
        residual = expit(x @ weights + intercept) - y
        return np.r_[x.T @ residual / len(y) + L2 * weights / len(y), residual.mean()]
    result = minimize(objective, np.zeros(x.shape[1] + 1), jac=gradient, method='L-BFGS-B')
    if not result.success or not np.isfinite(result.x).all():
        raise CatalogError('custom_model_fit_failed')
    return result.x[:-1].astype(np.float32), float(result.x[-1])


def _platt(f, y):
    """(A, B) with P(voice) = expit(A f + B), fitted to decision values `f` as libsvm does: targets pulled in from 1 and 0."""
    voice, silent = y.sum(), len(y) - y.sum()
    target = np.where(y == 1, (voice + 1) / (voice + 2), 1 / (silent + 2))

    def objective(value):
        logits = value[0] * f + value[1]
        return float((np.logaddexp(0, logits) - target * logits).sum())

    def gradient(value):
        residual = expit(value[0] * f + value[1]) - target
        return np.array([residual @ f, residual.sum()])
    return minimize(objective, [0.0, np.log((voice + 1) / (silent + 1))], jac=gradient, method='BFGS').x


def _calibrated_fit(x, y):
    """`_fit`, then its scores turned into probabilities so that 0.5 can be relied on -> weights, intercept, calibrated.

    Platt scaling on held-out scores: 5 folds, fewer when the smaller class is under 5; the i-th example of a class goes
    to fold i mod k, so the same answers always give the same AI. The sigmoid is folded into the weights and the
    intercept, so scoring stays one matrix product. Left uncalibrated when a class is under 3 or the slope is not
    positive (which would turn the ranking upside down).
    """
    weights, intercept = _fit(x, y)
    k = int(min(5, y.sum(), len(y) - y.sum()))
    if k < 3:
        return weights, intercept, False
    fold, held = np.empty(len(y), int), np.empty(len(y))
    for label in (0, 1):
        fold[y == label] = np.arange((y == label).sum()) % k
    try:
        for i in range(k):
            w, b = _fit(x[fold != i], y[fold != i])
            held[fold == i] = x[fold == i] @ w + b
    except CatalogError:
        return weights, intercept, False
    slope, shift = _platt(held, y)
    if not (slope > 0 and np.isfinite(shift)):
        return weights, intercept, False
    return (weights * slope).astype(np.float32), float(intercept * slope + shift), True


def growth_state(rounds, voice):
    """How far a species AI has grown, from its per-round records (`create`) and its number of "voice" answers.

    Checked in this order: 'no_voice' (the last two rounds found no voice among the highest-scoring answers),
    'settled' (the last two rounds hardly changed its judgements and both highest-scoring answers were voice),
    'unsettled' (made again 8 times without either), else 'growing'.
    The numbers (2 rounds, 0.9, 8 rounds) come from simulations and are to be reviewed with test users' data.
    """
    last = rounds[-2:]
    if len(last) == 2 and voice >= MIN_CLASS_EXAMPLES and all(r['top_voice'] == 0 for r in last):
        return 'no_voice'
    if len(last) == 2 and all(r['overlap'] is not None and r['overlap'] >= .9 and r['top_voice'] == 2 for r in last):
        return 'settled'
    return 'unsettled' if len(rounds) >= 8 else 'growing'


def _vector_batches(root, analysis_ids, size=256):
    if not analysis_ids:
        return
    with connect(root) as db:
        cursor = db.execute('''SELECT s.id AS segment_id,s.analysis_id,a.recording_id,s.start_s,s.end_s,v.embedding
            FROM segments s JOIN analyses a ON a.id=s.analysis_id JOIN vectors v ON v.rowid=s.id
            WHERE s.analysis_id IN (SELECT value FROM json_each(?)) AND ABS(s.end_s-s.start_s-5)<0.000001
            ORDER BY a.recording_id,s.start_s''', (json.dumps(analysis_ids),))
        while rows := cursor.fetchmany(size):
            valid, vectors = [], []
            for row in rows:
                vector = np.frombuffer(row['embedding'], dtype='<f4')
                if len(vector) == 1536 and np.isfinite(vector).all() and np.linalg.norm(vector) > 0:
                    valid.append({k: row[k] for k in ('segment_id', 'analysis_id', 'recording_id', 'start_s', 'end_s')})
                    vectors.append(vector)
            if valid:
                yield valid, _normalise(np.stack(vectors))


def _main_analyses(db, ctx):
    engine, sha = main_identity(Path(db.execute('PRAGMA database_list').fetchone()[2]).parent)
    return [dict(r) for r in db.execute('''SELECT a.* FROM analyses a JOIN site_recordings r ON r.id=a.recording_id
        WHERE r.scope_id=? AND a.engine=? AND (? IS NULL OR json_extract(a.metadata,'$.model_sha256')=?) AND a.id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)
        ORDER BY a.recording_id''', (ctx['dataset']['id'], engine, sha, sha))]


def _piece(db, analyses, recording_id, start_s, end_s):
    row = db.execute('''SELECT id FROM segments WHERE analysis_id=? AND start_s=? AND end_s=?
        AND ABS(end_s-start_s-5)<0.000001''', (analyses.get(recording_id), start_s, end_s)).fetchone()
    return row and row[0]


ORIGINS = {'training': 'label', 'validation': 'check'}  # any other screen's answer counts as 'presence'


def _pieces(db, ctx):
    """The 5-second pieces of the site's main analysis: {recording id: sorted (start, end, segment id)}."""
    pieces = {}
    for row in db.execute('''SELECT s.id,a.recording_id,s.start_s,s.end_s FROM segments s JOIN analyses a ON a.id=s.analysis_id
            WHERE s.analysis_id IN (SELECT value FROM json_each(?)) AND ABS(s.end_s-s.start_s-5)<0.000001
            ORDER BY a.recording_id,s.start_s''', (json.dumps([r['id'] for r in _main_analyses(db, ctx)]),)):
        pieces.setdefault(row['recording_id'], []).append((row['start_s'], row['end_s'], row['id']))
    return pieces


def _materials(db, ctx, species_id, pieces=None):
    """Labelled 5-second pieces of the site's main analysis, from the species' current labels at the site
    (`labels.on_pieces`). Origin: 'label' (the species-AI page), 'check' (a check answer) or 'presence' (any other
    screen). A mark on a deleted or outdated search does not count, as on the species page."""
    from search_runs import site_run
    run = site_run(db, ctx['site']['id'], species_id)
    pieces = _pieces(db, ctx) if pieces is None else pieces
    # An answer put right later (source 'review') replaces every screen's earlier answer on that interval.
    rows = [row for row in corrected(current(db, ctx['dataset']['id'], species_id, per_source=True)) if not (
        row['source'] == 'species_mark' and ref(row).get('run_id') not in (None, run and run['id']))]
    # An answer given on the species-AI page is the more specific one and wins; it is what that page lists.
    found = on_pieces(rows, pieces, first='training')
    # Newest first, as the species-AI page lists its answers.
    return run, {segment: {'label': TO_ANSWER[row['verdict']], 'origin': ORIGINS.get(row['source'], 'presence')}
                 for segment, row in sorted(found.items(), key=lambda item: (item[1]['created_at'], item[1]['id']), reverse=True)}


def material_status(root, ctx, species_ids):
    """Whether a species AI can be made, for many species at once: {species id: {'positive','negative','needed'}}."""
    with connect(root) as db:
        pieces = _pieces(db, ctx)
        result = {}
        for species_id in species_ids:
            found = _materials(db, ctx, species_id, pieces)[1].values()
            positive, negative = (sum(v['label'] == label for v in found) for label in ('target', 'other'))
            short = max(0, MIN_CLASS_EXAMPLES - positive) + max(0, MIN_CLASS_EXAMPLES - negative)
            result[species_id] = {'positive': positive, 'negative': negative, 'needed': max(short, MIN_EXAMPLES - len(found))}
    return result


def boundary_ranks(total, known, shown, round_, size=10):
    """Up to `size` unlabelled ranks (0 = most similar) around where the voice stops.

    The voice is assumed present down to the deepest "voice" label (or the last row shown in the
    similarity list) and absent from the next "no voice" label below it. Ranks between the two are
    picked at centre-weighted (triangular) quantiles, so they cluster mid-bracket and thin out
    towards its ends. Each round shifts the quantiles and avoids the previous round's ranks.
    """
    lo = max([shown - 1] + [rank for rank, label in known.items() if label == 'target'])
    hi = min([total] + [rank for rank, label in known.items() if label == 'other' and rank > lo])
    inside = sorted(rank for rank in known if lo < rank < hi)
    free = hi - lo - 1 - len(inside)
    if free == 0:
        # Boundary pinned between two labels: take the nearest unlabelled ranks on both sides.
        order = (r for pair in zip_longest(range(hi, total), range(lo, shown - 1, -1)) for r in pair
                 if r is not None and r not in known)
        return sorted(list(islice(order, (round_ + 1) * size))[-size:])

    def rank_of(index):  # index-th unlabelled rank inside the bracket
        rank = lo + 1 + index
        for labelled in inside:
            if labelled > rank:
                break
            rank += 1
        return rank
    if free <= size:
        return [rank_of(i) for i in range(free)]

    def nearest(index, taken):
        for step in range(free):
            for candidate in (index - step, index + step):
                if 0 <= candidate < free and candidate not in taken:
                    return candidate

    previous = set()
    for k in range(round_ + 1):
        phase, picked = (0.5 + k * 0.6180339887) % 1, set()
        for i in range(size):
            u = (i + phase) / size
            x = (u / 2) ** 0.5 if u < 0.5 else 1 - ((1 - u) / 2) ** 0.5
            avoid = picked | previous if len(picked | previous) < free else picked
            picked.add(nearest(min(int(x * free), free - 1), avoid))
        previous = picked
    return sorted(rank_of(i) for i in previous)


RANKED = 'SELECT segment_id,similarity,ROW_NUMBER() OVER (ORDER BY similarity DESC,segment_id)-1 AS rank FROM search_hits WHERE run_id=?'
BATCH = 10
TOP = 3  # likely voice, offered while "voice" is under MIN_CLASS_EXAMPLES
ENDS = 2  # species-AI batches: the highest and the lowest scores, each
NEAR_POOL = 4
FLOOR = 0.005  # built-in scores below this show as 0.00 and tell the sounds apart no better than chance


def bracket_ranks(total, known, need_voice):
    """Before a species AI exists: ranks to offer from a ranking (0 = most likely voice); `known` maps the ranks to
    leave out to their label (None for sounds passed over). While "voice" is short, the top TOP free ranks;
    the rest from `boundary_ranks` around where the voice stops."""
    top = list(islice((r for r in range(total) if r not in known), TOP)) if need_voice else []
    return sorted(top + [r for r in boundary_ranks(total, known, 0, 0) if r not in top][:BATCH - len(top)])


def scored_span(scores):
    """How many of the best-first built-in `scores` to choose from: those that do not show as 0.00, and BATCH below them."""
    return min(len(scores), sum(score >= FLOOR for score in scores) + BATCH)


def boundary_score(answers):
    """The species-AI score that best separates the answered sounds, from (score, label) pairs; None without both kinds.

    Candidate cuts are the midpoints between neighbouring distinct scores. A cut counts the "voice" answers below it
    and the "no voice" answers at or above it, each as a share of its kind (balanced error), so a few wrong answers
    barely move it. When the answers do not overlap this is the middle of the gap between them. Equal best cuts:
    the middle one (the lower of two).
    """
    voice = sorted(score for score, label in answers if label == 'target')
    silent = sorted(score for score, label in answers if label == 'other')
    if not voice or not silent:
        return None
    values = sorted(set(voice + silent))
    cuts = [(a + b) / 2 for a, b in zip(values, values[1:])] or values
    # Integer cross-multiplied balanced error, so equal errors compare exactly.
    errors = [bisect_left(voice, cut) * len(silent) + (len(silent) - bisect_left(silent, cut)) * len(voice) for cut in cuts]
    best = [cut for cut, error in zip(cuts, errors) if error == min(errors)]
    return best[(len(best) - 1) // 2]


def model_picks(scored, known):
    """With a species AI: BATCH (segment, score) pairs not in `known` (answered, with their label, or passed over: None).

    The free pieces are ranked by score. Its scores bunch together, so 0.5 is not where its answer changes: the boundary
    is the score that best separates the answered pieces (`boundary_score`), and the boundary rank is the number of
    free pieces scoring at or above it (the middle rank without both kinds of answer). Most of the batch comes from the
    NEAR_POOL times as many ranks centred on it (widened to whole runs of equal scores), spread evenly by segment id
    rather than side by side; ENDS come from the top ranks and ENDS from the bottom ranks.
    """
    scored = list(scored)
    score = dict(scored)
    cut = boundary_score([(score[segment], label) for segment, label in known.items() if label and segment in score])
    free = sorted(((segment, value) for segment, value in scored if segment not in known), key=lambda p: (-p[1], p[0]))
    if len(free) <= BATCH:
        return free
    middle, count = free[ENDS:-ENDS], BATCH - 2 * ENDS
    rank = len(middle) // 2 if cut is None else sum(value >= cut for _, value in middle)
    width = count * NEAR_POOL
    low = max(0, min(rank - width // 2, len(middle) - width))
    window = middle[low:low + width]
    # ponytail: segment ids follow import and recording order, a stand-in for time; use recording times if that drifts.
    pool = sorted(p for p in middle if window[-1][1] <= p[1] <= window[0][1])
    near = [pool[(2 * i + 1) * len(pool) // (2 * count)] for i in range(count)]
    return sorted(free[:ENDS] + near + free[-ENDS:], key=lambda p: (-p[1], p[0]))


def _ranked_builtin(root, db, ctx, species_id, analyses):
    """(segment, score) by the main built-in AI's score for this species, best first; empty if it does not list the species."""
    from validation import model_windows
    windows = model_windows(root, ctx, species_id, main_engine(root))[0]
    pieces = {(r[1], r[2]): r[0] for r in db.execute('''SELECT s.id,a.recording_id,s.start_s FROM segments s JOIN analyses a ON a.id=s.analysis_id
        WHERE s.analysis_id IN (SELECT value FROM json_each(?)) AND ABS(s.end_s-s.start_s-5)<0.000001''', (json.dumps(analyses),))}
    return sorted(((pieces[w['recording_id'], w['start_s']], w['score']) for w in windows
                   if (w['recording_id'], w['start_s']) in pieces and abs(w['end_s'] - w['start_s'] - 5) < 1e-6), key=lambda p: (-p[1], p[0]))


def _rows(db, scored):
    found = {r['segment_id']: dict(r) for r in db.execute('''SELECT s.id AS segment_id,s.start_s,s.end_s,
        r.id AS recording_id,r.path,r.seconds,r.recorded_at,r.timezone FROM segments s JOIN analyses a ON a.id=s.analysis_id
        JOIN recordings r ON r.id=a.recording_id WHERE s.id IN (SELECT value FROM json_each(?))''', (json.dumps([p[0] for p in scored]),))}
    return [{**found[segment], 'score': score} for segment, score in scored if segment in found]


def readiness(root, ctx, species_id, batch=False, skip=()):
    """Material counts, the species AI, and the labels given on its page (newest first).

    With `batch` also the next sounds to answer, never an answered one nor one in `skip` (shown and passed over),
    and where they were chosen from (`source`): 'custom' (species AI scores), 'search' (similarity),
    'builtin' (the main AI's score) or None (nothing to rank by).
    """
    models = list_models(root, ctx, species_id, every=True)
    model = models[0] if models else None
    source, picks = None, []
    with connect(root) as db:
        run, labels = _materials(db, ctx, species_id)
        positive = sum(value['label'] == 'target' for value in labels.values())
        leave = {**dict.fromkeys(skip), **{segment: value['label'] for segment, value in labels.items()}}
        if batch:
            if model and model['applicable']:
                source = 'custom'
                picks = model_picks(((w['segment_id'], w['score']) for w in score_windows(root, model['id'])), leave)
            elif run:
                source = 'search'
                total = db.execute('SELECT COUNT(*) FROM search_hits WHERE run_id=?', (run['id'],)).fetchone()[0]
                known = {rank: leave[segment] for segment, rank in db.execute(
                    f'SELECT segment_id,rank FROM ({RANKED}) WHERE segment_id IN (SELECT value FROM json_each(?))', (run['id'], json.dumps(list(leave))))}
                ranks = bracket_ranks(total, known, positive < MIN_CLASS_EXAMPLES)
                picks = [tuple(r) for r in db.execute(f'SELECT segment_id,similarity FROM ({RANKED}) WHERE rank IN (SELECT value FROM json_each(?)) ORDER BY rank',
                                                      (run['id'], json.dumps(ranks)))]
            else:
                ranked = _ranked_builtin(root, db, ctx, species_id, [a['id'] for a in _main_analyses(db, ctx)])
                if ranked:
                    source = 'builtin'
                    # Sounds scored 0.00 are left for last: first the scored ones and a few below them, then all.
                    for span in (scored_span([score for _, score in ranked]), len(ranked)):
                        known = {rank: leave[segment] for rank, (segment, _) in enumerate(ranked[:span]) if segment in leave}
                        ranks = bracket_ranks(span, known, positive < MIN_CLASS_EXAMPLES)
                        if len(ranks) == BATCH:
                            break
                    picks = [ranked[r] for r in ranks]
        rows = [{**row, 'label': None} for row in _rows(db, picks)]
        answered = [{**row, 'label': labels[row['segment_id']]['label']}
                    for row in _rows(db, [(segment, None) for segment, value in labels.items() if value['origin'] == 'label'])]
        # Making the AI again from the very answers it was made from would change nothing but outdate its check.
        unchanged = bool(model and model['applicable']) and {(e['segment_id'], e['label']) for e in json.loads(db.execute(
            'SELECT snapshot FROM custom_models WHERE id=?', (model['id'],)).fetchone()[0])['training']['examples']} == \
            {(segment, value['label']) for segment, value in labels.items()}
    zone = ZoneInfo(ctx['site']['timezone'])
    for row in rows + answered:
        row['local_time'] = window_key(row, row['start_s'], zone)[1]
    negative = sum(value['label'] == 'other' for value in labels.values())
    short = max(0, MIN_CLASS_EXAMPLES - positive) + max(0, MIN_CLASS_EXAMPLES - negative)
    needed = max(short, MIN_EXAMPLES - len(labels))
    reasons = [reason for reason, missing in [('custom_more_answers_needed', len(labels) < MIN_EXAMPLES),
                                              ('custom_positive_examples_needed', positive < MIN_CLASS_EXAMPLES),
                                              ('custom_negative_examples_needed', negative < MIN_CLASS_EXAMPLES),
                                              ('custom_no_new_answers', unchanged)] if missing]
    return {'species': names_for_id(root, species_id), 'start_date': ctx['start_date'], 'end_date': ctx['end_date'],
            'model': model, 'source': source, 'engine': main_engine(root),
            'batch': rows if batch else None, 'answered': answered, 'needed': needed,
            'examples': {'positive_5s': positive, 'negative_5s': negative,
                         'other_answers': sum(value['origin'] != 'label' for value in labels.values())},
            'requirements': {'positive_5s': MIN_CLASS_EXAMPLES, 'negative_5s': MIN_CLASS_EXAMPLES, 'total_5s': MIN_EXAMPLES},
            'can_train': not reasons, 'reasons': reasons}


def names_for_id(root, species_id):
    with connect(root) as db:
        row = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    return names_for(row)


def set_label(root, dataset_id, species_id, segment_id, label):
    """Voice / no voice / cannot tell on one 5-second piece of this site; None clears it. A changed label keeps its
    place in the list. "Cannot tell" is kept as a record but is no material for the AI."""
    with connect(root) as db:
        if not db.execute('''SELECT 1 FROM segments s JOIN analyses a ON a.id=s.analysis_id JOIN site_recordings r ON r.id=a.recording_id
                WHERE r.scope_id=? AND s.id=? AND ABS(s.end_s-s.start_s-5)<0.000001''', (dataset_id, segment_id)).fetchone() \
                or not db.execute('SELECT 1 FROM species WHERE id=?', (species_id,)).fetchone():
            raise CatalogError('not_found')
        piece = db.execute('''SELECT a.recording_id,s.start_s,s.end_s FROM segments s JOIN analyses a ON a.id=s.analysis_id
            WHERE s.id=?''', (segment_id,)).fetchone()
        if label is None:
            remove(db, species_id, *piece, 'training')
        else:
            put(db, species_id, *piece, FROM_ANSWER[label], 'training')
        db.execute('DELETE FROM workflow_cache')
    return {'segment_id': segment_id, 'label': label}


def _score_rows(segment_ids, analysis_ids, recording_ids, starts, ends, scores):
    return [{'segment_id': int(sid), 'analysis_id': int(aid), 'recording_id': int(rid), 'start_s': float(start), 'end_s': float(end),
             'score': float(score), 'example_start_s': float(start)}
            for sid, aid, rid, start, end, score in zip(segment_ids, analysis_ids, recording_ids, starts, ends, scores)
            if abs(float(end) - float(start) - 5) < 1e-6]


def create(root, ctx, species_id, cancel=None):
    """Make the species AI, or make it again in place: same id, new files, round + 1. Its earlier checks
    then no longer match its files and count as outdated (validation.custom_model_status)."""
    engine = main_engine(root)
    spec = feature_spec(engine)
    state = readiness(root, ctx, species_id)
    if not state['can_train']:
        raise CatalogError(state['reasons'][0])
    previous = state['model']
    with connect(root) as db:
        run, labels = _materials(db, ctx, species_id)
        analyses = _main_analyses(db, ctx)
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))}
        old = json.loads(db.execute('SELECT snapshot FROM custom_models WHERE id=?', (previous['id'],)).fetchone()[0]) if previous else None
    # The AI about to be replaced: its scores tell how much this round changes.
    before = {w['segment_id']: w['score'] for w in score_windows(root, previous['id'])} if previous and previous['applicable'] else {}
    expected = {analysis_metadata(a['metadata'])['model_sha256'] for a in analyses}
    if len(expected) != 1:
        raise CatalogError('custom_perch_mixed' if expected else 'custom_perch_missing')
    zone = ZoneInfo(ctx['site']['timezone'])
    examples, vectors = [], []
    ids = [a['id'] for a in analyses]
    rows, scored = [], []
    for batch, matrix in _vector_batches(root, ids):
        if cancel and cancel.is_set():
            raise CatalogError('cancelled')
        for row, vector in zip(batch, matrix):
            if row['segment_id'] in labels:
                examples.append({**row, **labels[row['segment_id']]})
                vectors.append(vector)
    y = np.array([row['label'] == 'target' for row in examples], dtype=np.float64)
    if len(y) < MIN_EXAMPLES or y.sum() < MIN_CLASS_EXAMPLES or len(y) - y.sum() < MIN_CLASS_EXAMPLES:
        raise CatalogError('custom_more_answers_needed' if len(y) < MIN_EXAMPLES else
                           'custom_positive_examples_needed' if y.sum() < MIN_CLASS_EXAMPLES else 'custom_negative_examples_needed')
    weights, intercept, calibrated = _calibrated_fit(_normalise(np.stack(vectors)), y)
    for batch, matrix in _vector_batches(root, ids):
        if cancel and cancel.is_set():
            raise CatalogError('cancelled')
        for row, score in zip(batch, expit(matrix @ weights + intercept)):
            record = records.get(row['recording_id'])
            if record and window_in_scope(record, row['start_s'], row['end_s'], zone, ctx['start_date'], ctx['end_date']):
                rows.append(row)
                scored.append(float(score))
    run_id = run['id'] if run else None
    history = []
    if previous:
        # This round's record: how many of the sounds scored 0.5 or more stayed the same (shared / in either; None when
        # either AI has none), and how many of the previous AI's two highest-scoring sounds answered since it was made
        # were voice (None when fewer than two were answered).
        was, now = {s for s, v in before.items() if v >= .5}, {r['segment_id'] for r, v in zip(rows, scored) if v >= .5}
        seen = {e['segment_id'] for e in old['training']['examples']}
        top = sorted((-before[e['segment_id']], e['segment_id'], e['label'] == 'target') for e in examples
                     if e['origin'] == 'label' and e['segment_id'] not in seen and e['segment_id'] in before)[:ENDS]
        history = old.get('rounds', []) + [{'round': previous['round'] + 1, 'overlap': len(was & now) / len(was | now) if was and now else None,
                                            'top_voice': sum(voice for *_, voice in top) if len(top) == ENDS else None}]
    snapshot = {'schema': 'custom_model_v2', 'species_id': species_id, 'run_id': run_id, 'source_fingerprint': ctx['fingerprint'],
        'round': previous['round'] + 1 if previous else 1, 'rounds': history,
        'scope': {'site_id': ctx['site']['id'], 'start_date': ctx['start_date'], 'end_date': ctx['end_date'], 'recording_ids': sorted(records)},
        'feature': {'engine': engine, 'model_sha256': expected.pop(), 'spec': spec, 'window_s': 5},
        'training': {'positive_5s': int(y.sum()), 'negative_5s': int(len(y) - y.sum()), 'l2': L2, 'calibrated': calibrated,
                     'negative_from_checks': sum(e['origin'] == 'check' and e['label'] == 'other' for e in examples),
                     'positive_from_checks': sum(e['origin'] == 'check' and e['label'] == 'target' for e in examples),
                     'positive_from_presence': sum(e['origin'] == 'presence' and e['label'] == 'target' for e in examples),
                     'examples': examples},
        'analysis_ids': ids, 'inference': {'scored_5s': len(scored)}}
    folder = Path(root) / 'custom-models'
    folder.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.new-', dir=folder))
    final = backup = None
    swapped = False
    try:
        np.savez_compressed(temporary / 'model.npz', weights=weights, intercept=np.array(intercept), l2=np.array(L2),
                            feature_spec=np.array(spec), model_sha256=np.array(snapshot['feature']['model_sha256']))
        np.savez_compressed(temporary / 'scores.npz', segment_ids=np.asarray([r['segment_id'] for r in rows], dtype=np.int64),
            analysis_ids=np.asarray([r['analysis_id'] for r in rows], dtype=np.int64),
            recording_ids=np.asarray([r['recording_id'] for r in rows], dtype=np.int64),
            start_s=np.asarray([r['start_s'] for r in rows]), end_s=np.asarray([r['end_s'] for r in rows]), scores=np.asarray(scored))
        artifact, scores = _digest(temporary / 'model.npz'), _digest(temporary / 'scores.npz')
        with connect(root) as db:
            model_id = previous['id'] if previous else db.execute('''INSERT INTO custom_models(dataset_id,species_id,run_id,snapshot,
                artifact_path,artifact_sha256,scores_path,scores_sha256) VALUES(?,?,?,'{}','','','','')''', (ctx['dataset']['id'], species_id, run_id)).lastrowid
            final = folder / str(model_id)
            if final.exists():
                backup = folder / f'.{model_id}-previous'
                shutil.rmtree(backup, ignore_errors=True)
                os.replace(final, backup)
            os.replace(temporary, final)
            swapped = True
            db.execute('''UPDATE custom_models SET dataset_id=?,run_id=?,snapshot=?,artifact_path=?,artifact_sha256=?,scores_path=?,scores_sha256=?,
                created_at=CURRENT_TIMESTAMP WHERE id=?''', (ctx['dataset']['id'], run_id, json.dumps(snapshot, allow_nan=False),
                str(final / 'model.npz'), artifact, str(final / 'scores.npz'), scores, model_id))
            db.execute('DELETE FROM workflow_cache')
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if swapped:
            shutil.rmtree(final, ignore_errors=True)
        if backup:
            os.replace(backup, final)
        raise
    if backup:
        shutil.rmtree(backup, ignore_errors=True)
    return get_model(root, model_id)


def _public(root, row, current_fingerprint=None):
    snap = json.loads(row['snapshot'])
    engine, sha = main_identity(root)
    feature_ok = (snap['feature']['engine'] == engine and snap['feature']['spec'] == feature_spec(engine)
                  and (sha is None or snap['feature']['model_sha256'] == sha))

    def file_ok(path, digest):
        try:
            return Path(path).is_file() and _digest(path) == digest
        except OSError:
            return False
    files_ok = file_ok(row['artifact_path'], row['artifact_sha256']) and file_ok(row['scores_path'], row['scores_sha256'])
    scope_ok = (current_fingerprint or snap['source_fingerprint']) == snap['source_fingerprint']
    return {'id': row['id'], 'created_at': row['created_at'], 'dataset_id': row['dataset_id'], 'run_id': row['run_id'], 'round': snap.get('round', 1),
        # AIs saved before the per-round records have no history: they count as growing until made again.
        'growth': {'rounds': snap.get('rounds', []), 'state': growth_state(snap.get('rounds', []), snap['training']['positive_5s'])},
        'species': names_for_id(root, row['species_id']), 'scope': snap['scope'], 'feature': snap['feature'],
        'training': {k: v for k, v in snap['training'].items() if k != 'examples'}, 'inference': snap['inference'],
        'analysis_ids': snap['analysis_ids'], 'applicable': scope_ok and files_ok and feature_ok,
        'reason': 'custom_model_scope_changed' if not feature_ok else None if scope_ok and files_ok else 'custom_model_scope_changed' if not scope_ok else 'custom_model_files_missing',
        'artifact_sha256': row['artifact_sha256'], 'scores_sha256': row['scores_sha256']}


def get_model(root, model_id):
    from results_workflow import fingerprint
    with connect(root) as db:
        row = db.execute('SELECT * FROM custom_models WHERE id=?', (model_id,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    return _public(root, row, fingerprint(root, row['dataset_id']))


def training_windows(root, model_id):
    with connect(root) as db:
        row = db.execute('SELECT snapshot FROM custom_models WHERE id=?', (model_id,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    return {(e['recording_id'], float(e['start_s']), float(e['end_s'])) for e in json.loads(row['snapshot'])['training']['examples']}


def list_models(root, ctx, species_id=None, every=False):
    """This site's models for the displayed period, newest first; by default the newest usable one per species."""
    with connect(root) as db:
        rows = db.execute('''SELECT m.* FROM custom_models m JOIN datasets d ON d.id=m.dataset_id WHERE d.site_id=?
            AND (? IS NULL OR m.species_id=?) ORDER BY m.id DESC''', (ctx['site']['id'], species_id, species_id)).fetchall()
    rows = [row for row in rows if [json.loads(row['snapshot'])['scope'][k] for k in ('start_date', 'end_date')] == [ctx['start_date'], ctx['end_date']]]
    rows = [row for row in rows if json.loads(row['snapshot'])['feature']['engine'] == main_engine(root)]
    models = [_public(root, row, ctx['fingerprint']) for row in rows]
    if every:
        return models
    chosen = {}
    for model in models:
        if model['species']['id'] not in chosen or model['applicable'] and not chosen[model['species']['id']]['applicable']:
            chosen[model['species']['id']] = model
    return sorted(chosen.values(), key=lambda model: model['id'], reverse=True)


def delete_model(root, model_id):
    """Remove what the model produced: its files, its row and its own checks."""
    from validation import delete_sessions
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM custom_models WHERE id=?', (model_id,)).fetchone():
            raise CatalogError('not_found')
        sessions = [r[0] for r in db.execute('SELECT id FROM validation_sessions WHERE model=?', (f'custom:{model_id}',))]
        delete_sessions(db, sessions)
        db.execute('DELETE FROM custom_models WHERE id=?', (model_id,))
    # Only our own generated folder is removed, never a path read from the catalogue.
    shutil.rmtree(Path(root) / 'custom-models' / str(model_id), ignore_errors=True)
    return {'id': model_id, 'validations': len(sessions)}


def score_windows(root, model_id):
    with connect(root) as db:
        row = db.execute('SELECT * FROM custom_models WHERE id=?', (model_id,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    model = _public(root, row)
    if not model['applicable']:
        raise CatalogError(model['reason'])
    path = Path(row['scores_path'])
    if not path.is_file() or _digest(path) != row['scores_sha256']:
        raise CatalogError('custom_model_files_missing')
    with np.load(path, allow_pickle=False) as saved:
        keys = ('segment_ids', 'analysis_ids', 'recording_ids', 'start_s', 'end_s', 'scores')
        if len({len(saved[key]) for key in keys}) != 1 or not all(np.isfinite(saved[key]).all() for key in ('start_s', 'end_s', 'scores')):
            raise CatalogError('invalid_scores')
        return _score_rows(saved['segment_ids'], saved['analysis_ids'], saved['recording_ids'], saved['start_s'], saved['end_s'], saved['scores'])


def report_snapshot(root, ctx, model_id, threshold):
    model = get_model(root, model_id)
    if not model['applicable']:
        raise CatalogError(model['reason'])
    windows = score_windows(root, model_id)
    zone = ZoneInfo(ctx['site']['timezone'])
    record_ids = set(model['scope']['recording_ids'])
    with connect(root) as db:
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],)) if r['id'] in record_ids}
    cells = calendar_cells(list(records.values()), zone, date.fromisoformat(ctx['start_date']), date.fromisoformat(ctx['end_date']))
    eligible = found = 0
    days, candidate_days, evidence = set(), set(), []
    for record in records.values():
        checked_source(record)
        for key, duration in effort(record, zone):
            if key in cells:
                cells[key]['analysed_seconds'] += duration
    for row in windows:
        record = records.get(row['recording_id'])
        if not record or not window_in_scope(record, row['start_s'], row['end_s'], zone, ctx['start_date'], ctx['end_date']):
            continue
        key, local = window_key(record, row['start_s'], zone)
        eligible += 1
        days.add(key[0])
        cells[key]['eligible_windows'] += 1
        cells[key]['effective_seconds'] += 5
        if threshold is not None and row['score'] >= threshold:
            found += 1
            candidate_days.add(key[0])
            cells[key]['candidate_windows'] += 1
            evidence.append({**row, 'path': record['path'], 'seconds': record['seconds'], 'recorded_at': record['recorded_at'], 'timezone': record['timezone'],
                'start_s': row['example_start_s'], 'end_s': row['example_start_s'] + 5, 'window_start_s': row['start_s'], 'window_end_s': row['end_s'],
                'local_time': window_key(record, row['example_start_s'], zone)[1], 'window_local_time': local, 'date': key[0], 'hour': key[1], 'status': 'ai_candidate'})
    counted = threshold is not None
    summary = {'species': model['species'], 'eligible_windows': eligible, 'candidate_windows': found if counted else None,
        'candidate_rate': found / eligible if eligible and counted else None, 'recorded_days': len(days),
        'candidate_days': len(candidate_days) if counted else None, 'quality': 'not_evaluated'}
    return {'kind': 'detection', 'dataset': ctx['dataset'], 'site': ctx['site'], 'engine': 'custom', 'model_sha256': model['artifact_sha256'], 'model_id': model_id,
        'custom_model': {'id': model_id, 'engine': model['feature']['engine'], 'model_sha256': model['feature']['model_sha256'],
            **({'perch_model_sha256': model['feature']['model_sha256']} if model['feature']['engine'] == 'perch' else {}),
            'feature_spec': model['feature']['spec'],
            'training_window_s': 5, 'evaluation_window_s': 5, 'training': model['training'], 'run_id': model['run_id']},
        'analysis_ids': sorted({row['analysis_id'] for row in windows}), 'missing_region': [], 'threshold': threshold,
        'start_date': ctx['start_date'], 'end_date': ctx['end_date'], 'window_s': 5, 'species': model['species'], 'summaries': [summary],
        'cells': finalize_cells(cells, threshold), 'evidence': evidence, 'quality': 'not_evaluated', 'complete_windows_only': True,
        'recording_ids': sorted(records), 'base_report_id': None}
