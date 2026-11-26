"""Test sounds: answers set aside per species and site, never used as material, to compare each round of a species AI.

The split is by recording, so calls of one morning do not land on both sides. Test sounds are made first (phase
'collecting'): a recording becomes a test recording when a sound in it is answered on the species-AI page, and
recordings with a check answer count too; every answer of the species in a test recording is a test sound. Growing
starts only when the set is frozen ('fixed'), early with the sounds found so far when the species calls rarely: from
then on the test sounds are the answers first given by that time (`sounds`), so every round is tried on the same
sounds; an answer given later in a test recording is neither a test sound nor material. 'skipped' (no test sounds)
is only the state of a species grown before test sounds came first.
"""
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import expit

from reports import window_key
from storage import CatalogError, connect

SCHEMA = '''
    CREATE TABLE IF NOT EXISTS test_recordings (
        site_id INTEGER NOT NULL REFERENCES sites, species_id TEXT NOT NULL REFERENCES species,
        recording_id INTEGER NOT NULL REFERENCES recordings,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(site_id,species_id,recording_id));
    CREATE TABLE IF NOT EXISTS test_phases (
        site_id INTEGER NOT NULL REFERENCES sites, species_id TEXT NOT NULL REFERENCES species,
        state TEXT NOT NULL CHECK(state IN ('fixed','skipped')), fixed_at TEXT, PRIMARY KEY(site_id,species_id));
'''
HEARD = 10       # "heard" test sounds aimed for
NOT_HEARD = 20   # "not heard" test sounds aimed for
ASK_AFTER = 60   # answered test sounds after which growing with the test sounds found so far is offered
ENOUGH = 3       # "heard" test sounds below which growing cannot start
FOUND = 0.5      # a species AI's score from which it says the voice is there


def phase(db, site_id, species_id):
    """'collecting' (test sounds are being made; no AI yet), 'fixed' (growing, test recordings frozen) or 'skipped'
    (growing without test sounds). Only the last two are stored. A species grown before phases existed has an AI and
    no stored phase: 'fixed' when recordings were set aside for it, else 'skipped'."""
    row = db.execute('SELECT state FROM test_phases WHERE site_id=? AND species_id=?', (site_id, species_id)).fetchone()
    if row:
        return row[0]
    if not db.execute('SELECT 1 FROM custom_models m JOIN datasets d ON d.id=m.dataset_id WHERE d.site_id=? AND m.species_id=?',
                      (site_id, species_id)).fetchone():
        return 'collecting'
    return 'fixed' if _stored(db, site_id, species_id) else 'skipped'


def _stored(db, site_id, species_id):
    return {r[0] for r in db.execute('SELECT recording_id FROM test_recordings WHERE site_id=? AND species_id=?', (site_id, species_id))}


def recordings(db, site_id, species_id):
    """The species' test recordings at the site. While collecting, recordings that hold a check answer count too:
    those sounds were chosen by a built-in AI's score, not by the species AI. They are stored when the set is frozen."""
    found = _stored(db, site_id, species_id)
    if phase(db, site_id, species_id) == 'collecting':
        found |= {r[0] for r in db.execute('''SELECT DISTINCT l.recording_id FROM labels l JOIN recordings r ON r.id=l.recording_id
            JOIN datasets d ON d.id=r.dataset_id WHERE d.site_id=? AND l.taxon_id=? AND l.source='validation' AND l.verdict!='unknown' ''',
            (site_id, species_id))}
    return found


def add(db, site_id, species_id, recording_id):
    """An answer was saved on the species-AI page: while collecting, its recording becomes a test recording."""
    if phase(db, site_id, species_id) == 'collecting':
        db.execute('INSERT OR IGNORE INTO test_recordings(site_id,species_id,recording_id) VALUES(?,?,?)', (site_id, species_id, recording_id))


def sounds(db, site_id, species_id, labelled):
    """The test sounds among `labelled` (as from `custom_models._labelled`): the answers in a test recording, once
    frozen only those first given by then. A corrected answer keeps the time of its first answer, so it stays one.
    A set frozen before the time was kept (`fixed_at` NULL) takes later answers too, as it did."""
    test = recordings(db, site_id, species_id)
    fixed = db.execute('SELECT fixed_at FROM test_phases WHERE site_id=? AND species_id=?', (site_id, species_id)).fetchone()
    return {segment: value for segment, value in labelled.items()
            if value['recording_id'] in test and (not fixed or fixed[0] is None or value['first'] <= fixed[0])}


def marker(db, site_id):
    """For the list of checked sounds: a function telling whether a label row of the site is a test sound (as `sounds`)."""
    stored = {(r[0], r[1]) for r in db.execute('SELECT species_id,recording_id FROM test_recordings WHERE site_id=?', (site_id,))}
    fixed = {r[0]: r[1] for r in db.execute('SELECT species_id,fixed_at FROM test_phases WHERE site_id=? AND fixed_at IS NOT NULL', (site_id,))}
    first = {tuple(r)[:4]: r[4] for r in db.execute('''SELECT taxon_id,recording_id,start_s,end_s,MIN(created_at) FROM labels
        WHERE taxon_id IN (SELECT value FROM json_each(?)) GROUP BY 1,2,3,4''', (json.dumps(list(fixed)),))}
    return lambda row: (row['taxon_id'], row['recording_id']) in stored and (
        row['taxon_id'] not in fixed or first[row['taxon_id'], row['recording_id'], row['start_s'], row['end_s']] <= fixed[row['taxon_id']])


def progress(db, ctx, species_id, labelled):
    """How far the test sounds are: `labelled` as from `custom_models._labelled`."""
    from labels import current
    site_id = ctx['site']['id']
    test = recordings(db, site_id, species_id)
    said = [value['label'] for value in sounds(db, site_id, species_id, labelled).values()]
    heard, not_heard = said.count('target'), said.count('other')
    unknown = sum(row['recording_id'] in test for row in current(db, ctx['dataset']['id'], species_id, 'unknown'))
    return {'heard': heard, 'not_heard': not_heard, 'answered': heard + not_heard + unknown, 'aim_heard': HEARD, 'aim_not_heard': NOT_HEARD,
            'ask_after': ASK_AFTER, 'enough': ENOUGH, 'complete': heard >= HEARD and not_heard >= NOT_HEARD}


def freeze(root, ctx, species_id):
    """End the making of test sounds and freeze the test recordings; growing starts. Refused below ENOUGH "heard"
    test sounds (the page offers it before the aims are met only after ASK_AFTER answers or when nothing is left
    to offer). It cannot be changed afterwards."""
    from custom_models import _labelled
    site_id = ctx['site']['id']
    with connect(root) as db:
        test = recordings(db, site_id, species_id)
        if phase(db, site_id, species_id) != 'collecting' or \
                progress(db, ctx, species_id, _labelled(db, ctx, species_id)[1])['heard'] < ENOUGH:
            raise CatalogError('test_phase_refused')
        db.executemany('INSERT OR IGNORE INTO test_recordings(site_id,species_id,recording_id) VALUES(?,?,?)',
                       [(site_id, species_id, recording) for recording in test])
        # The same clock and format as `labels.created_at`.
        db.execute("INSERT INTO test_phases(site_id,species_id,state,fixed_at) VALUES(?,?,'fixed',strftime('%Y-%m-%d %H:%M:%f','now'))", (site_id, species_id))
        db.execute('DELETE FROM workflow_cache')
    return {'state': 'fixed'}


def keep_round(folder, previous, number, weights, intercept, trained_on=()):
    """Keep this round's weights beside the model, with those of the rounds before, so that every round can be
    scored again on test sounds answered later. `trained_on`: the recordings its material came from."""
    rounds = Path(folder) / 'rounds'
    rounds.mkdir()
    if previous is not None and (Path(previous) / 'rounds').is_dir():
        for path in (Path(previous) / 'rounds').glob('round-*.npz'):
            (rounds / path.name).write_bytes(path.read_bytes())
    np.savez_compressed(rounds / f'round-{number}.npz', weights=weights, intercept=np.array(intercept),
                        trained_on=np.asarray(sorted(set(trained_on)), dtype=np.int64))


def _rounds(folder, current):
    """{round: (weights, intercept, recordings its material came from or None when not kept)}; an AI made before
    rounds were kept has only its current one."""
    found = {}
    for path in sorted((Path(folder) / 'rounds').glob('round-*.npz')) if (Path(folder) / 'rounds').is_dir() else []:
        with np.load(path, allow_pickle=False) as saved:
            found[int(path.stem.split('-')[1])] = (saved['weights'], float(saved['intercept']),
                                                   set(saved['trained_on'].tolist()) if 'trained_on' in saved else None)
    model = Path(folder) / 'model.npz'
    if current not in found and model.is_file():
        with np.load(model, allow_pickle=False) as saved:
            found[current] = (saved['weights'], float(saved['intercept']), None)
    return dict(sorted(found.items()))


def report(root, ctx, species_id):
    """How each round of the species AI does on the test sounds, and which sounds to listen to.

    {'state': 'none'} when the species has no test sounds; else the counts, whether the set is closed to later
    answers (`fixed`), one row per round ("found" of the heard test sounds, "false" of the not-heard ones), and for
    the current round the sounds it missed, wrongly found, and found for the first time."""
    from custom_models import _labelled, _normalise, list_models
    with connect(root) as db:
        run, labelled = _labelled(db, ctx, species_id)
        test = recordings(db, ctx['site']['id'], species_id)
        found = sounds(db, ctx['site']['id'], species_id, labelled)
        rows = {r['segment_id']: dict(r) for r in db.execute('''SELECT s.id AS segment_id,s.start_s,s.end_s,r.id AS recording_id,r.path,r.seconds,
            r.recorded_at,v.embedding FROM segments s JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
            JOIN vectors v ON v.rowid=s.id WHERE s.id IN (SELECT value FROM json_each(?))''', (json.dumps(list(found)),))}
        fixed = db.execute('SELECT fixed_at FROM test_phases WHERE site_id=? AND species_id=?', (ctx['site']['id'], species_id)).fetchone()
    files = sorted({Path(r['path']).name for r in rows.values()})
    heard = [s for s, v in found.items() if s in rows and v['label'] == 'target']
    absent = [s for s, v in found.items() if s in rows and v['label'] == 'other']
    if not heard and not absent:
        return {'state': 'none'}
    models = list_models(root, ctx, species_id, every=True)
    model = models[0] if models else None
    result = {'state': 'ready', 'sounds': {'heard': len(heard), 'not_heard': len(absent)}, 'rounds': [], 'threshold': FOUND,
              'fixed': bool(fixed and fixed[0]), 'recordings': files, 'missed': [], 'false': [], 'new': []}
    if model is None:
        return result
    order = heard + absent
    matrix = _normalise(np.stack([np.frombuffer(rows[s]['embedding'], dtype='<f4') for s in order]))
    said = {}
    scores = {}
    for number, (weights, intercept, trained_on) in _rounds(Path(root) / 'custom-models' / str(model['id']), model['round']).items():
        if len(weights) != matrix.shape[1]:
            continue
        if trained_on is not None and trained_on & test:
            # Made before test sounds came first, from sounds that are test sounds now: a score on them would flatter it.
            result['rounds'].append({'round': number, 'found': None, 'false': None, 'current': number == model['round'], 'made_from_test': True})
            continue
        scores[number] = dict(zip(order, expit(matrix @ weights + intercept).tolist()))
        said[number] = {s: value >= FOUND for s, value in scores[number].items()}
        result['rounds'].append({'round': number, 'found': sum(said[number][s] for s in heard), 'false': sum(said[number][s] for s in absent),
                                 'current': number == model['round'], 'made_from_test': False})
    zone = ZoneInfo(ctx['site']['timezone'])
    shape = lambda s: {'segment_id': s, 'score': round(scores[model['round']][s], 2), 'recording_id': rows[s]['recording_id'], 'start_s': rows[s]['start_s'], 'end_s': rows[s]['end_s'],
                       'path': rows[s]['path'], 'seconds': rows[s]['seconds'], 'local_time': window_key(rows[s], rows[s]['start_s'], zone)[1]}
    now = said.get(model['round'])
    if now:
        before = said.get(max((n for n in said if n < model['round']), default=None))
        result['missed'] = [shape(s) for s in heard if not now[s]]
        result['false'] = [shape(s) for s in absent if now[s]]
        result['new'] = [shape(s) for s in heard if now[s] and before is not None and not before[s]]
    return result
