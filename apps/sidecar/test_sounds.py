"""Test sounds: answers set aside per species and site, never used as material, to compare each round of a species AI.

The split is by recording, so calls of one morning do not land on both sides: every answer of the species in a
"test recording" is a test sound, also answers given later. Recordings are set aside when the AI is made, only
for a species with enough "heard" sounds, and only as far as the AI can still be made from the rest.
"""
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import expit

from reports import window_key
from storage import connect

SCHEMA = '''
    CREATE TABLE IF NOT EXISTS test_recordings (
        site_id INTEGER NOT NULL REFERENCES sites, species_id TEXT NOT NULL REFERENCES species,
        recording_id INTEGER NOT NULL REFERENCES recordings,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(site_id,species_id,recording_id));
'''
HEARD = 10       # "heard" test sounds aimed for
NEEDED = 13      # "heard" sounds a species needs before any are set aside: HEARD + the 3 an AI needs
FOUND = 0.5      # a species AI's score from which it says the voice is there


def recordings(db, site_id, species_id):
    return {r[0] for r in db.execute('SELECT recording_id FROM test_recordings WHERE site_id=? AND species_id=?', (site_id, species_id))}


def choose(db, site_id, species_id, labelled, minimum, minimum_each):
    """Set recordings aside until the test sounds hold HEARD "heard" ones. `labelled`: {segment: {'label', 'origin',
    'recording_id'}} of the species at the site. Recordings with check answers go first (they were chosen by the
    built-in AI's score, not by the species AI), then a fixed shuffle. A recording is skipped when the rest would no
    longer make an AI (`minimum` answers, `minimum_each` of each kind). Returns the recordings newly set aside."""
    if sum(v['label'] == 'target' for v in labelled.values()) < NEEDED:
        return []
    test = recordings(db, site_id, species_id)
    per = {}
    for value in labelled.values():
        counts = per.setdefault(value['recording_id'], {'target': 0, 'other': 0, 'check': 0})
        counts[value['label']] += 1
        counts['check'] += value['origin'] == 'check'
    total = lambda ids, key: sum(per[r][key] for r in ids)
    material = set(per) - test
    order = sorted(material, key=lambda r: (-per[r]['check'], hashlib.sha256(f'{species_id}:{r}'.encode()).hexdigest()))
    added = []
    for recording in order:
        if total(set(per) & (test | set(added)), 'target') >= HEARD:
            break
        rest = material - set(added) - {recording}
        if total(rest, 'target') < minimum_each or total(rest, 'other') < minimum_each or \
                total(rest, 'target') + total(rest, 'other') < minimum or not per[recording]['target'] + per[recording]['other']:
            continue
        added.append(recording)
    db.executemany('INSERT OR IGNORE INTO test_recordings(site_id,species_id,recording_id) VALUES(?,?,?)',
                   [(site_id, species_id, recording) for recording in added])
    return added


def keep_round(folder, previous, number, weights, intercept):
    """Keep this round's weights beside the model, with those of the rounds before, so that every round can be
    scored again on test sounds answered later."""
    rounds = Path(folder) / 'rounds'
    rounds.mkdir()
    if previous is not None and (Path(previous) / 'rounds').is_dir():
        for path in (Path(previous) / 'rounds').glob('round-*.npz'):
            (rounds / path.name).write_bytes(path.read_bytes())
    np.savez_compressed(rounds / f'round-{number}.npz', weights=weights, intercept=np.array(intercept))


def _rounds(folder, current):
    """{round: (weights, intercept)}; an AI made before rounds were kept has only its current one."""
    found = {}
    for path in sorted((Path(folder) / 'rounds').glob('round-*.npz')) if (Path(folder) / 'rounds').is_dir() else []:
        with np.load(path, allow_pickle=False) as saved:
            found[int(path.stem.split('-')[1])] = (saved['weights'], float(saved['intercept']))
    model = Path(folder) / 'model.npz'
    if current not in found and model.is_file():
        with np.load(model, allow_pickle=False) as saved:
            found[current] = (saved['weights'], float(saved['intercept']))
    return dict(sorted(found.items()))


def report(root, ctx, species_id):
    """How each round of the species AI does on the test sounds, and which sounds to listen to.

    {'state': 'none', 'heard', 'needed'} while nothing is set aside; else the counts, one row per round ("found"
    of the heard test sounds, "false" of the not-heard ones), and for the current round the sounds it missed,
    wrongly found, and found for the first time."""
    from custom_models import _labelled, _normalise, list_models
    with connect(root) as db:
        run, labelled = _labelled(db, ctx, species_id)
        test = recordings(db, ctx['site']['id'], species_id)
        sounds = {segment: value for segment, value in labelled.items() if value['recording_id'] in test}
        rows = {r['segment_id']: dict(r) for r in db.execute('''SELECT s.id AS segment_id,s.start_s,s.end_s,r.id AS recording_id,r.path,r.seconds,
            r.recorded_at,v.embedding FROM segments s JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
            JOIN vectors v ON v.rowid=s.id WHERE s.id IN (SELECT value FROM json_each(?))''', (json.dumps(list(sounds)),))}
    heard_all = sum(v['label'] == 'target' for v in labelled.values())
    sounds = {segment: value for segment, value in sounds.items() if segment in rows}
    heard = [s for s, v in sounds.items() if v['label'] == 'target']
    absent = [s for s, v in sounds.items() if v['label'] == 'other']
    if not test or not heard:
        return {'state': 'none', 'heard': heard_all, 'needed': NEEDED}
    models = list_models(root, ctx, species_id, every=True)
    model = models[0] if models else None
    result = {'state': 'ready', 'sounds': {'heard': len(heard), 'not_heard': len(absent)}, 'rounds': [], 'aim': HEARD,
              'missed': [], 'false': [], 'new': []}
    if model is None:
        return result
    order = heard + absent
    matrix = _normalise(np.stack([np.frombuffer(rows[s]['embedding'], dtype='<f4') for s in order]))
    said = {}
    for number, (weights, intercept) in _rounds(Path(root) / 'custom-models' / str(model['id']), model['round']).items():
        if len(weights) != matrix.shape[1]:
            continue
        said[number] = dict(zip(order, (expit(matrix @ weights + intercept) >= FOUND).tolist()))
        result['rounds'].append({'round': number, 'found': sum(said[number][s] for s in heard), 'false': sum(said[number][s] for s in absent),
                                 'current': number == model['round']})
    zone = ZoneInfo(ctx['site']['timezone'])
    shape = lambda s: {'segment_id': s, 'recording_id': rows[s]['recording_id'], 'start_s': rows[s]['start_s'], 'end_s': rows[s]['end_s'],
                       'path': rows[s]['path'], 'seconds': rows[s]['seconds'], 'local_time': window_key(rows[s], rows[s]['start_s'], zone)[1]}
    now = said.get(model['round'])
    if now:
        before = said.get(max((n for n in said if n < model['round']), default=None))
        result['missed'] = [shape(s) for s in heard if not now[s]]
        result['false'] = [shape(s) for s in absent if now[s]]
        result['new'] = [shape(s) for s in heard if now[s] and before is not None and not before[s]]
    return result
