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
from itertools import zip_longest
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from models import feature_spec, main_engine, main_identity
from reports import analysis_metadata, calendar_cells, effort, finalize_cells, window_in_scope, window_key
from labels import FROM_ANSWER, TO_ANSWER, complete, corrected, current, no_bird, on_pieces, put, ref, remove
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


ORIGINS = {'training': 'label', 'validation': 'check', 'no_bird': 'no_bird'}  # any other screen's answer counts as 'presence'


def _pieces(db, ctx):
    """The 5-second pieces of the site's main analysis: {recording id: sorted (start, end, segment id)}."""
    pieces = {}
    for row in db.execute('''SELECT s.id,a.recording_id,s.start_s,s.end_s FROM segments s JOIN analyses a ON a.id=s.analysis_id
            WHERE s.analysis_id IN (SELECT value FROM json_each(?)) AND ABS(s.end_s-s.start_s-5)<0.000001
            ORDER BY a.recording_id,s.start_s''', (json.dumps([r['id'] for r in _main_analyses(db, ctx)]),)):
        pieces.setdefault(row['recording_id'], []).append((row['start_s'], row['end_s'], row['id']))
    return pieces


def _materials(db, ctx, species_id, pieces=None):
    """`_labelled` without the test sounds (`test_sounds`) and without the answers given when the species AI itself
    was checked (origin 'test': they measure it; owner decision 2026-11-27): what the species AI may be made from."""
    import test_sounds
    run, found = _labelled(db, ctx, species_id, pieces)
    test = test_sounds.recordings(db, ctx['site']['id'], species_id)
    return run, {segment: {k: v for k, v in value.items() if k != 'first'} for segment, value in found.items()
                 if value['recording_id'] not in test and value['origin'] != 'test'}


def _labelled(db, ctx, species_id, pieces=None):
    """Labelled 5-second pieces of the site's main analysis, from the species' current labels at the site
    (`labels.on_pieces`). Origin: 'label' (the species-AI page), 'check' (an answer in a built-in AI's check), 'test'
    (an answer in a check of a species AI of this species, whenever it was given: never material), 'presence' (any other
    screen) or 'no_bird' (a complete sound, such as "no bird sound": "no call" for every species without its own answer there). A mark on a deleted or outdated search does not count, as on the species page.
    `first`: when its interval was first answered, however often the answer was changed since (`test_sounds.sounds`)."""
    from search_runs import site_run
    run = site_run(db, ctx['site']['id'], species_id)
    pieces = _pieces(db, ctx) if pieces is None else pieces
    # An answer put right later (source 'review') replaces every screen's earlier answer on that interval.
    rows = [row for row in corrected(current(db, ctx['dataset']['id'], species_id, per_source=True)) if not (
        row['source'] == 'species_mark' and ref(row).get('run_id') not in (None, run and run['id']))]
    # An answer given on the species-AI page is the more specific one and wins; it is what that page lists.
    found = on_pieces(rows, pieces, first='training')
    for segment, row in on_pieces([{**row, 'verdict': 'absent', 'source': 'no_bird'} for row in complete(db, ctx['dataset']['id'])], pieces).items():
        found.setdefault(segment, row)
    first = {tuple(r)[:4]: r[4] for r in db.execute('SELECT taxon_id,recording_id,start_s,end_s,MIN(created_at) FROM labels WHERE taxon_id IN (?,?) GROUP BY 1,2,3,4',
                                                    (species_id, no_bird(db)))}
    own = {r[0] for r in db.execute("SELECT id FROM validation_sessions WHERE species_id=? AND model LIKE 'custom:%'", (species_id,))}
    origin = lambda row: 'test' if row['source'] == 'validation' and ref(row).get('session_id') in own else ORIGINS.get(row['source'], 'presence')
    # Newest first, as the species-AI page lists its answers.
    return run, {segment: {'label': TO_ANSWER[row['verdict']], 'origin': origin(row), 'recording_id': row['recording_id'],
                           'first': first[row['taxon_id'], row['recording_id'], row['start_s'], row['end_s']]}
                 for segment, row in sorted(found.items(), key=lambda item: (item[1]['created_at'], item[1]['id']), reverse=True)}


def _keys(root, material):
    """{segment: (recording key, start)} of material keyed by segment, to tell the same sound apart across PCs (sharing.recording_key)."""
    from sharing import recording_key
    with connect(root) as db:
        records = {r['id']: recording_key(r['path'], r['recorded_at']) for r in db.execute(
            'SELECT id,path,recorded_at FROM recordings WHERE id IN (SELECT value FROM json_each(?))', (json.dumps(sorted({v['recording_id'] for v in material.values()})),))}
        starts = {r[0]: r[1] for r in db.execute('SELECT id,start_s FROM segments WHERE id IN (SELECT value FROM json_each(?))', (json.dumps(list(material)),))}
    return {segment: (records[value['recording_id']], starts[segment]) for segment, value in material.items()
            if value['recording_id'] in records and segment in starts}


def _site_names(root, site_ids):
    with connect(root) as db:
        return {r[0]: r[1] for r in db.execute('SELECT id,name FROM sites WHERE id IN (SELECT value FROM json_each(?))', (json.dumps(sorted(site_ids)),))}


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


BATCH = 10
ENDS = 2  # species-AI batches: the highest scores, and as many drawn from the lower half
NEAR_POOL = 4
TOP_SHARE, TOP_MIN = .01, 30  # the top layer of a ranking: its best 1% of ranks, at least 30 sounds
# (top, middle, low) sounds of a batch, by whether "voice" and "no voice" are still short.
MIX = {(True, True): (4, 3, 3), (True, False): (6, 4, 0), (False, True): (0, 4, 6), (False, False): (4, 3, 3)}


def _rng(*state):
    """A random generator seeded by `state` alone, so the same state draws the same batch (a reload does not
    reshuffle) and any new answer or passed-over sound draws anew."""
    return np.random.default_rng(int.from_bytes(hashlib.sha256(json.dumps(state).encode()).digest()[:8], 'big'))


def layered_picks(ranked, known, need_voice, need_silent, rng, lanes=None):
    """Before a species AI exists: BATCH (segment, score) pairs of `ranked` (best first) not in `known`, best first.

    The ranking has three layers by rank: top (the best TOP_SHARE of the ranks, at least TOP_MIN, never into the
    lower half), low (the lower half) and middle (between). Sounds are drawn at random within a layer, MIX of each;
    what a layer cannot give comes from its neighbour (for the middle: the side MIX asks more of), then from the third.

    `lanes` (a search made from several examples): {segment: the example it is nearest to}. The top layer's sounds
    are then dealt one example at a time, at random within an example, so each example gets its share; the deal
    starts with the example that has the fewest answered sounds there, which evens the shares out over the batches,
    and an example with none left passes its turn on. An example with no sound at all in the top layer (a minor
    call, whose hits rank lower) plays with its own best-ranked max(3, top quota) free sounds instead, so it is drawn
    too. The middle and low layers are one ranking, whatever the example.
    """
    low = len(ranked) // 2
    top = min(max(TOP_MIN, int(len(ranked) * TOP_SHARE)), low)
    want = MIX[need_voice, need_silent]
    free = lambda ranks: [rank for rank in ranks if ranked[rank][0] not in known]
    by_lane = {}
    for rank, (segment, _) in enumerate(ranked):
        by_lane.setdefault(lanes[segment] if lanes else 0, []).append(rank)
    hands = []
    for _, ranks in sorted(by_lane.items()):
        span = [rank for rank in ranks if rank < top]
        if not span:
            best = free(ranks)[:max(3, want[0])]
            span = [rank for rank in ranks if best and rank <= best[-1]]
        hands.append((sum(known.get(ranked[rank][0]) is not None for rank in span), rng.permutation(free(span)).astype(int).tolist()))
    start = min(range(len(hands)), key=lambda lane: hands[lane][0])
    dealt = [rank for turn in zip_longest(*(hand for _, hand in hands[start:] + hands[:start])) for rank in turn if rank is not None]
    pools = [dealt] + [rng.permutation(sorted(set(free(range(*span))) - set(dealt))).astype(int).tolist() for span in ((top, low), (low, len(ranked)))]
    take = [min(n, len(pool)) for n, pool in zip(want, pools)]
    for layer, others in enumerate(((1, 2), (0, 2) if want[0] >= want[2] else (2, 0), (1, 0))):
        short = want[layer] - min(want[layer], len(pools[layer]))
        for other in others:
            more = min(short, len(pools[other]) - take[other])
            take[other] += more
            short -= more
    return [ranked[rank] for rank in sorted(rank for pool, n in zip(pools, take) for rank in pool[:n])]


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


def example_picks(hits, known, score):
    """With a species AI and a search: one sound for each example of the search, so a call the AI scores low (a rarer
    call of the species) is still asked about. `hits`: (segment, the example it is nearest to), most similar first.

    Each example gives its most similar sound that has a score and is not in `known`. With more examples than the
    batch has room for beside its ENDS, those with the fewest answered sounds among their own hits go first (then the
    earlier example), so every example gets its turn over the batches.
    """
    lanes = {}
    for segment, lane in hits:
        lanes.setdefault(lane, []).append(segment)
    turns = []
    for lane, segments in lanes.items():
        best = next((segment for segment in segments if segment not in known and segment in score), None)
        if best is not None:
            turns.append((sum(known.get(segment) is not None for segment in segments), lane, best))
    return [best for *_, best in sorted(turns)[:BATCH - 2 * ENDS]]


def model_picks(scored, known, rng, first=()):
    """With a species AI: BATCH (segment, score) pairs not in `known` (answered, with their label, or passed over: None).

    `first` (`example_picks`) are in the batch whatever their score, in place of as many sounds near the boundary.
    The other free pieces are ranked by score. ENDS come from the top ranks, and ENDS are drawn at random from the lower
    half: clearly different sounds, not always the same extremes. For the rest: its scores bunch together, so 0.5 is
    not where its answer changes: the boundary is the score that best separates the answered pieces
    (`boundary_score`), and the boundary rank is the number of the other free pieces scoring at or above it (the middle
    rank without both kinds of answer). They come from the NEAR_POOL times as many ranks centred on it (widened to
    whole runs of equal scores), spread evenly by segment id rather than side by side.
    """
    scored = list(scored)
    score = dict(scored)
    cut = boundary_score([(score[segment], label) for segment, label in known.items() if label and segment in score])
    order = lambda p: (-p[1], p[0])
    free = sorted(((segment, value) for segment, value in scored if segment not in known), key=order)
    mine, free = [p for p in free if p[0] in first], [p for p in free if p[0] not in first]
    if len(free) <= BATCH - len(mine):
        return sorted(mine + free, key=order)
    far = set((len(free) // 2 + rng.choice(len(free) - len(free) // 2, ENDS, replace=False)).tolist())
    middle, count = [p for rank, p in enumerate(free) if rank >= ENDS and rank not in far], BATCH - 2 * ENDS - len(mine)
    if not count:
        return sorted(mine + free[:ENDS] + [free[rank] for rank in far], key=order)
    rank = len(middle) // 2 if cut is None else sum(value >= cut for _, value in middle)
    width = count * NEAR_POOL
    low = max(0, min(rank - width // 2, len(middle) - width))
    window = middle[low:low + width]
    # ponytail: segment ids follow import and recording order, a stand-in for time; use recording times if that drifts.
    pool = sorted(p for p in middle if window[-1][1] <= p[1] <= window[0][1])
    near = [pool[(2 * i + 1) * len(pool) // (2 * count)] for i in range(count)]
    return sorted(mine + free[:ENDS] + near + [free[rank] for rank in far], key=order)


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
    'builtin' (the main AI's score) or None (nothing to rank by). `search_examples`: how many examples the species'
    search was made from. `by_example`: a 'custom' batch also has a sound
    for each example of the species' search (`example_picks`). `check`: the species AI's own check (step 3 of its
    page), as the species page has it: 'stale' once the AI was made again; None when never started.
    """
    models = list_models(root, ctx, species_id, every=True)
    model = models[0] if models else None
    source, picks = None, []
    import sharing
    shared = sharing.state(root, ctx, species_id)
    taken = sharing.inherited(root, ctx, model, set(_keys(root, shared['material']).values()))[0] if model else []
    with connect(root) as db:
        import test_sounds
        pieces = _pieces(db, ctx)
        run, every = _labelled(db, ctx, species_id, pieces)
        set_aside = test_sounds.recordings(db, ctx['site']['id'], species_id)
        # While the test sounds are being made, every sound may be offered: answering it makes its recording a test recording.
        stage = test_sounds.phase(db, ctx['site']['id'], species_id)
        collecting = stage == 'collecting'
        labels = {segment: value for segment, value in every.items() if value['recording_id'] not in set_aside and value['origin'] != 'test'}
        tests = test_sounds.sounds(db, ctx['site']['id'], species_id, every)
        test = test_sounds.progress(db, ctx, species_id, every)
        positive, negative = (sum(value['label'] == label for value in labels.values()) for label in ('target', 'other'))
        # What the batch should bring more of: test sounds while they are made, then material for the first AI.
        need_voice = test['heard'] < test['aim_heard'] if collecting else positive < MIN_CLASS_EXAMPLES
        need_silent = test['not_heard'] < test['aim_not_heard'] if collecting else negative < MIN_CLASS_EXAMPLES
        # A test sound is answered too: it is not asked again, though it is no material. Nor is a sound answered "cannot tell".
        unsure = on_pieces([{**row, 'verdict': 'present'} for row in current(db, ctx['dataset']['id'], species_id, 'unknown')], pieces)
        leave = {**dict.fromkeys(skip), **dict.fromkeys(unsure), **{segment: value['label'] for segment, value in every.items()}}
        # Sounds of a test recording are not offered here: chosen by the very AI they test, they would flatter or damn it.
        if batch and set_aside and not collecting:
            leave = {**{r[0]: None for r in db.execute('''SELECT s.id FROM segments s JOIN analyses a ON a.id=s.analysis_id
                WHERE a.recording_id IN (SELECT value FROM json_each(?))''', (json.dumps(sorted(set_aside)),))}, **leave}
        if batch:
            if collecting and model and model['applicable']:
                # An AI brought from another site or a file: its test sounds here come from the three layers of its ranking.
                source = 'custom'
                ranked = sorted(((w['segment_id'], w['score']) for w in score_windows(root, model['id'])), key=lambda p: (-p[1], p[0]))
                picks = layered_picks(ranked, leave, need_voice, need_silent, _rng(species_id, 'custom', model['id'], sorted(leave)))
            elif model and model['applicable']:
                source = 'custom'
                scored = [(w['segment_id'], w['score']) for w in score_windows(root, model['id'])]
                # A search's examples each bring one sound, whatever the AI makes of it: the AI may not know that call yet.
                picks = model_picks(scored, leave, _rng(species_id, model['id'], model['round'], sorted(leave)), example_picks(db.execute(
                    'SELECT segment_id,reference_index FROM search_hits WHERE run_id=? ORDER BY similarity DESC,segment_id', (run['id'],)), leave, dict(scored)) if run else ())
            else:
                # ponytail: the whole ranking is read for each batch; draw the ranks in SQL if a site's sounds make this slow.
                hits = db.execute('SELECT segment_id,similarity,reference_index FROM search_hits WHERE run_id=? ORDER BY similarity DESC,segment_id',
                                  (run['id'],)).fetchall() if run else []
                ranked = [tuple(r)[:2] for r in hits] if run else _ranked_builtin(root, db, ctx, species_id, [a['id'] for a in _main_analyses(db, ctx)])
                if ranked:
                    source = 'search' if run else 'builtin'
                    # A search made from several examples: each example's nearest sounds get their share of the batch.
                    picks = layered_picks(ranked, leave, need_voice, need_silent, _rng(species_id, source, run and run['id'], sorted(leave)),
                                          {r[0]: r[2] for r in hits} if run else None)
        rows = [{**row, 'label': None} for row in _rows(db, picks)]
        # Answers in a test recording are listed too, so they can be taken back here; `test` marks the test sounds.
        answered = [{**row, 'label': every[row['segment_id']]['label']}
                    for row in _rows(db, [(segment, None) for segment, value in every.items() if value['origin'] == 'label'])]
        for row in rows + answered:
            row['test'] = row['segment_id'] in tests
        # Making the AI again from the very answers it was made from would change nothing but outdate its check.
        unchanged = bool(model and model['applicable']) and {(e['segment_id'], e['label']) for e in json.loads(db.execute(
            'SELECT snapshot FROM custom_models WHERE id=?', (model['id'],)).fetchone()[0])['training']['examples']} == \
            {(segment, value['label']) for segment, value in {**labels, **shared['material']}.items()}
    zone = ZoneInfo(ctx['site']['timezone'])
    for row in rows + answered:
        row['local_time'] = window_key(row, row['start_s'], zone)[1]
    # With other sites' answers as material too (`sharing`), they count towards what an AI needs.
    more = [*shared['material'].values(), *taken]
    all_positive, all_negative = positive + sum(v['label'] == 'target' for v in more), negative + sum(v['label'] == 'other' for v in more)
    short = max(0, MIN_CLASS_EXAMPLES - all_positive) + max(0, MIN_CLASS_EXAMPLES - all_negative)
    needed = max(short, MIN_EXAMPLES - all_positive - all_negative)
    from results_workflow import current_validations
    check = current_validations(root, ctx).get((species_id, f"custom:{model['id']}")) if model else None
    reasons = [reason for reason, missing in [('custom_test_sounds_first', collecting),
                                              ('custom_more_answers_needed', all_positive + all_negative < MIN_EXAMPLES),
                                              ('custom_positive_examples_needed', all_positive < MIN_CLASS_EXAMPLES),
                                              ('custom_negative_examples_needed', all_negative < MIN_CLASS_EXAMPLES),
                                              ('custom_no_new_answers', unchanged)] if missing]
    return {'species': names_for_id(root, species_id), 'start_date': ctx['start_date'], 'end_date': ctx['end_date'],
            'model': model, 'check': check, 'source': source, 'by_example': source == 'custom' and bool(run), 'engine': main_engine(root),
            'search_examples': len(json.loads(run['snapshot'])['references']) if run else 0,
            'batch': rows if batch else None, 'answered': answered, 'needed': needed,
            'examples': {'positive_5s': positive, 'negative_5s': negative,
                         'other_answers': sum(value['origin'] not in ('label', 'no_bird') for value in labels.values()),
                         'no_bird_answers': sum(value['origin'] == 'no_bird' for value in labels.values())},
            'phase': stage, 'test': test,
            'sharing': {k: shared[k] for k in ('all_sites', 'positive_5s', 'negative_5s', 'sites')},
            # The material of the AI it grows from (sharing.inherited): taken when the AI is made here.
            'inherited': sharing._counts(taken) if taken else None,
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
    import test_sounds
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
            # ponytail: an answer taken back leaves its recording a test recording; drop it there if empty test recordings ever matter.
            test_sounds.add(db, db.execute('SELECT site_id FROM datasets WHERE id=?', (dataset_id,)).fetchone()[0], species_id, piece[0])
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
    import test_sounds
    engine = main_engine(root)
    spec = feature_spec(engine)
    state = readiness(root, ctx, species_id)
    if not state['can_train']:
        raise CatalogError(state['reasons'][0])
    # An AI copied here (from another site or a file) is not made again in place: the AI made from it is this site's own
    # AI for the period made again (next round), or a new AI beside the copy when the site has none yet.
    previous = state['model']
    if previous and previous.get('imported'):
        previous = next((m for m in list_models(root, ctx, species_id, every=True) if not m.get('imported')), None)
    import sharing
    shared = sharing.state(root, ctx, species_id)
    taken, parent = sharing.inherited(root, ctx, state['model'], set(_keys(root, shared['material']).values()))
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
    # Other sites' answers, when the species is set to use them: material only, never scored here.
    for batch, matrix in _vector_batches(root, shared['analysis_ids']):
        for row, vector in zip(batch, matrix):
            if row['segment_id'] in shared['material']:
                examples.append({**row, **shared['material'][row['segment_id']]})
                vectors.append(vector)
    elsewhere = [e for e in examples if 'site_id' in e]
    # The material of the AI it grows from: sound descriptions kept with that AI, from this PC or another.
    vectors += [e['vector'] for e in taken]
    y = np.array([row['label'] == 'target' for row in examples] + [e['label'] == 'target' for e in taken], dtype=np.float64)
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
                     'other_sites': {'positive_5s': sum(e['label'] == 'target' for e in elsewhere), 'negative_5s': sum(e['label'] == 'other' for e in elsewhere),
                                     'sites': len({e['site_id'] for e in elsewhere})} if elsewhere else None,
                     'inherited': sharing._counts(taken) if taken else None,
                     'examples': examples},
        'parent': parent,
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
        test_sounds.keep_round(temporary, folder / str(previous['id']) if previous else None, snapshot['round'], weights, intercept,
                                [e['recording_id'] for e in examples])
        # What it was made from goes with it, so it can grow further at another site or on another PC.
        keys = _keys(root, {e['segment_id']: e for e in examples})
        names = _site_names(root, {e.get('site_id', ctx['site']['id']) for e in examples})
        sharing.save_material(temporary / sharing.MATERIAL, [{'vector': v, 'label': e['label'], 'recording': keys[e['segment_id']][0], 'start_s': e['start_s'],
                                                               'site': names.get(e.get('site_id', ctx['site']['id']))} for e, v in zip(examples, vectors)] + taken)
        sharing.save_material(temporary / sharing.INHERITED, taken)
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
            sharing.choose(db, ctx['site']['id'], species_id, model_id)
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
        'imported': snap.get('imported'), 'parent': snap.get('parent'), 'analysis_ids': snap['analysis_ids'], 'applicable': scope_ok and files_ok and feature_ok,
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
    """This site's models for the displayed period, the chosen one then the newest first; by default the first usable one per species."""
    with connect(root) as db:
        # The AI the site chose for the species comes first (sharing.choose), then the newest.
        rows = db.execute('''SELECT m.* FROM custom_models m JOIN datasets d ON d.id=m.dataset_id WHERE d.site_id=?
            AND (? IS NULL OR m.species_id=?) ORDER BY EXISTS(SELECT 1 FROM species_ai_choice c WHERE c.site_id=d.site_id
            AND c.species_id=m.species_id AND c.model_id=m.id) DESC, m.id DESC''', (ctx['site']['id'], species_id, species_id)).fetchall()
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
        db.execute('DELETE FROM species_ai_choice WHERE model_id=?', (model_id,))
        db.execute('DELETE FROM species_ai_seen WHERE model_id=?', (model_id,))
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
