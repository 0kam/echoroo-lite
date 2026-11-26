"""Sounds an AI answered with a bird not recorded in Japan, shown as their family and asked to the user.

A window is rolled up when the AI's first answer over all classes is a bird outside the Japan checklist and its
family's score reaches the display threshold (taxa.display). The roll-ups of an analysis are found once and kept.
The user's answer is a label with source `rollup_fix`: the species they heard (evidence and species-AI material
like any other "present"), the family row itself when the answer stops there: present ("this family, species
unknown") or unknown, or "no bird sound" (`labels.put_no_bird`; older data: the family row, absent).
Families without a species on the Japan checklist are shown together under the reserved family key NO_JAPAN.
"""
import json
from zoneinfo import ZoneInfo

import labels
from models import selected_engines
from species import canonical_scientific_name, japan_names, names_for
from storage import CatalogError, connect
from taxa import family_of

SCHEMA = '''
    CREATE TABLE IF NOT EXISTS rollups (
        analysis_id INTEGER NOT NULL REFERENCES analyses, start_s REAL NOT NULL, end_s REAL NOT NULL,
        family TEXT NOT NULL, score REAL NOT NULL, top_label TEXT NOT NULL, top_score REAL NOT NULL,
        japan_label TEXT,
        PRIMARY KEY(analysis_id,start_s));
    CREATE TABLE IF NOT EXISTS rollup_scans (analysis_id INTEGER PRIMARY KEY REFERENCES analyses);
'''
KINDS = ('species', 'family', 'not_bird', 'unknown')
NO_JAPAN = '_none'
LATEST = '''a.id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)'''


def scan(root, dataset_id):
    """Find the roll-ups of the site's analyses that were not looked at yet."""
    from predictions import dense_rollups
    with connect(root) as db:
        todo = [dict(r) for r in db.execute(f'''SELECT a.*,r.seconds FROM analyses a JOIN site_recordings r ON r.id=a.recording_id
            WHERE r.scope_id=? AND {LATEST} AND a.id NOT IN (SELECT analysis_id FROM rollup_scans)''', (dataset_id,))]
    for row in todo:
        meta = json.loads(row['metadata'])
        found = []
        if row['engine'] == 'perch' or meta.get('all_classes'):
            try:
                found = dense_rollups(row['scores_path'], row['seconds'], row['engine'], meta.get('window_s', 5))
            except (OSError, CatalogError, ValueError, KeyError):
                continue
        with connect(root) as db:
            db.executemany('INSERT OR IGNORE INTO rollups VALUES(?,?,?,?,?,?,?,?)', (
                (row['id'], r['start_s'], r['end_s'], r['family']['scientific_name'], r['score'], r['top_label'],
                 r['top_score'], r['japan_label']) for r in found))
            db.execute('INSERT OR IGNORE INTO rollup_scans VALUES(?)', (row['id'],))


def _sounds(root, db, ctx, family=None):
    """Rolled-up sounds of the shown AIs in the period, one per recording interval and family, best score first
    (ties in recording order), each with the current answer."""
    from reports import window_in_scope
    zone = ZoneInfo(ctx['site']['timezone'])
    dataset_id = ctx['dataset']['id']
    engines = selected_engines(root)
    records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (dataset_id,))}
    sounds = {}
    for row in db.execute(f'''SELECT u.*,a.recording_id,a.engine FROM rollups u JOIN analyses a ON a.id=u.analysis_id
            JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=? AND {LATEST} AND (? IS NULL OR u.family=?)
            ORDER BY u.score DESC,a.recording_id,u.start_s''', (dataset_id, family, family)):
        record = records[row['recording_id']]
        if row['engine'] not in engines or not window_in_scope(record, row['start_s'], row['end_s'], zone,
                                                                 ctx['start_date'], ctx['end_date']):
            continue
        key = (row['recording_id'], row['start_s'], row['end_s'], row['family'])
        sound = sounds.setdefault(key, {'recording_id': row['recording_id'], 'start_s': row['start_s'],
            'end_s': row['end_s'], 'family': row['family'], 'engines': [], 'top_label': row['top_label'],
            'top_engine': row['engine'], 'japan_label': row['japan_label'], 'source_path': record['path'], 'seconds': record['seconds'], 'answer': None})
        sound['engines'].append(row['engine'])
    families = {r['id']: r['scientific_name'] for r in db.execute("SELECT id,scientific_name FROM species WHERE rank='family'")}
    none = labels.no_bird(db)
    for row in db.execute('''SELECT l.* FROM labels l JOIN site_recordings r ON r.id=l.recording_id
            WHERE r.scope_id=? AND l.source='rollup_fix' ORDER BY l.id''', (dataset_id,)):
        data = labels.ref(row)
        sound = sounds.get((row['recording_id'], row['start_s'], row['end_s'], data.get('family')))
        if sound is None:
            continue
        if row['taxon_id'] == none:
            sound['answer'] = {'kind': 'not_bird', 'species': None}
        elif row['taxon_id'] in families:
            # 'absent': how "no bird sound" was stored before it had its own record.
            kind = {'present': 'family', 'absent': 'not_bird', 'unknown': 'unknown'}[row['verdict']]
            sound['answer'] = {'kind': kind, 'species': None}
        else:
            taxon = db.execute('SELECT * FROM species WHERE id=?', (row['taxon_id'],)).fetchone()
            sound['answer'] = {'kind': 'species', 'species': names_for(taxon)}
    return list(sounds.values())


def _family(db, family):
    row = db.execute("SELECT * FROM species WHERE scientific_name=? AND rank='family'", (family,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    return row


def _japan_species(db):
    """The Japan checklist's species rows, by family."""
    japan, found = japan_names(), {}
    for row in db.execute("SELECT * FROM species WHERE rank='species' AND accepted_id IS NULL"):
        if row['scientific_name'] in japan:
            found.setdefault(family_of(row['scientific_name']), []).append(row)
    return found


def _guess(db, sound):
    """The species the AI answered, with its names where the app knows them."""
    name = canonical_scientific_name(sound['top_engine'], sound['top_label']) or sound['top_label'].partition('_')[0]
    row = db.execute('SELECT * FROM species WHERE scientific_name=?', (name,)).fetchone()
    return names_for(row) if row else {'id': None, 'scientific_name': name, 'common_names': {}}


def summary(root, dataset_id, first=None, last=None):
    """Per family: how many sounds were rolled up to it, how many still wait for an answer, and the two species
    the AI answered most often. Families without a Japan species share one last row (`family` None) that names
    its two largest families."""
    from results_workflow import context
    ctx = context(root, dataset_id, first, last)
    scan(root, dataset_id)
    with connect(root) as db:
        rows, guesses, away, home = {}, {}, {}, _japan_species(db)
        for sound in _sounds(root, db, ctx):
            family = sound['family'] if sound['family'] in home else NO_JAPAN
            row = rows.setdefault(family, {'family': names_for(_family(db, family)) if family != NO_JAPAN else None, 'sounds': 0, 'left': 0})
            row['sounds'] += 1
            row['left'] += sound['answer'] is None
            if family == NO_JAPAN:
                away[sound['family']] = away.get(sound['family'], 0) + 1
            counts = guesses.setdefault(family, {})
            key = (sound['top_engine'], sound['top_label'])
            counts[key] = counts.get(key, 0) + 1
        for family, row in rows.items():
            # Two AIs naming the same species count as one guess.
            named = {}
            for (engine, label), count in guesses[family].items():
                guess = _guess(db, {'top_engine': engine, 'top_label': label})
                named.setdefault(guess['scientific_name'], [guess, 0])[1] += count
            row['guesses'] = [guess for guess, _ in sorted(named.values(), key=lambda pair: -pair[1])[:2]]
        merged = rows.pop(NO_JAPAN, None)
        if merged:
            merged['families'] = len(away)
            merged['examples'] = [names_for(_family(db, name)) for name in sorted(away, key=lambda name: (-away[name], name))[:2]]
    return {'start_date': ctx['start_date'], 'end_date': ctx['end_date'],
            'families': sorted(rows.values(), key=lambda r: (-r['sounds'], r['family']['scientific_name'])) + ([merged] if merged else [])}


def family_sounds(root, dataset_id, family, first=None, last=None):
    """One family's sounds (the AI's best score first), the Japan species of the family to choose from, and the AI's
    own answer for each sound. NO_JAPAN gives the sounds of every family without a Japan species, with no choices."""
    from results_workflow import context
    ctx = context(root, dataset_id, first, last)
    scan(root, dataset_id)
    from reports import window_key
    zone = ZoneInfo(ctx['site']['timezone'])
    with connect(root) as db:
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (dataset_id,))}
        home = _japan_species(db)
        merged = family == NO_JAPAN
        taxon = None if merged else names_for(_family(db, family))
        choices = [names_for(r) for r in home.get(family, [])]
        # The order depends on the scores alone, so a sound keeps its place whatever was answered.
        sounds = [s for s in _sounds(root, db, ctx) if s['family'] not in home] if merged else _sounds(root, db, ctx, family)
        offered = {choice['id'] for choice in choices}
        for sound in sounds:
            sound['guess'] = _guess(db, sound)
            # The AI's best Japan species for this sound, unless the family's own list already offers it.
            home = _guess(db, {**sound, 'top_label': sound['japan_label']}) if sound['japan_label'] else None
            sound['japan_guess'] = home if home and home['id'] and home['id'] not in offered else None
            del sound['top_label'], sound['top_engine'], sound['japan_label']
            sound['local_time'] = window_key(records[sound['recording_id']], sound['start_s'], zone)[1]
    # Species already confirmed by listening at this site come first, then those the range model expects here; the
    # rest follow by name. An AI detection nobody checked gives no priority. `likely` marks the first two groups.
    from results_workflow import region_statuses
    with connect(root) as db:
        heard = {row['taxon_id'] for row in labels.current(db, dataset_id, verdict='present')}
    regions = region_statuses(root, ctx, [choice['scientific_name'] for choice in choices])
    for choice in choices:
        usual = regions.get(choice['scientific_name']) == 'usual'
        choice['likely'] = choice['id'] in heard or usual
        choice['_order'] = (choice['id'] not in heard, not usual, choice['common_names'].get('ja') or choice['scientific_name'])
    choices.sort(key=lambda c: c.pop('_order'))
    return {'family': taxon, 'site': ctx['site'], 'start_date': ctx['start_date'], 'end_date': ctx['end_date'],
            'choices': choices, 'sounds': sounds}


def answer(root, body):
    """Record what the user heard in one rolled-up sound; `kind` None takes the answer back."""
    with connect(root) as db:
        record = db.execute('SELECT * FROM site_recordings WHERE scope_id=? AND id=?', (body.dataset_id, body.recording_id)).fetchone()
        family = _family(db, body.family)
        rolled = db.execute(f'''SELECT u.top_label,a.engine FROM rollups u JOIN analyses a ON a.id=u.analysis_id
            WHERE a.recording_id=? AND u.start_s=? AND u.end_s=? AND u.family=? AND {LATEST} ORDER BY u.score DESC''',
            (body.recording_id, body.start_s, body.end_s, body.family)).fetchall()
        if record is None or not rolled:
            raise CatalogError('not_found')
        taxon = family['id']
        if body.kind == 'species':
            from species import accepted
            taxon = accepted(db, body.species_id)
            if not db.execute("SELECT 1 FROM species WHERE id=? AND rank='species'", (taxon,)).fetchone():
                raise CatalogError('not_found')
        # One answer per sound: an earlier one, whatever it named, is replaced.
        db.execute('''DELETE FROM labels WHERE source='rollup_fix' AND recording_id=? AND start_s=? AND end_s=?
            AND json_extract(source_ref,'$.family')=?''', (body.recording_id, body.start_s, body.end_s, body.family))
        note = {'family': body.family, 'ai_answer': rolled[0]['top_label'], 'engines': [r['engine'] for r in rolled]}
        if body.kind == 'not_bird':
            from validation import refresh
            for heard in labels.put_no_bird(db, body.recording_id, body.start_s, body.end_s, 'rollup_fix', note):
                refresh(root, db, heard, body.recording_id, body.start_s, body.end_s)
            db.execute('DELETE FROM workflow_cache')
        elif body.kind is not None:
            verdict = {'species': 'present', 'family': 'present', 'unknown': 'unknown'}[body.kind]
            labels.put(db, taxon, body.recording_id, body.start_s, body.end_s, verdict, 'rollup_fix', note)
    return {'kind': body.kind}
