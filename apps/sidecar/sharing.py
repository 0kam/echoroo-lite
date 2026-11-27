"""Passing material and species AIs around: answers of other sites as material, a species AI as one file, and the
answers given by ear as a table (CSV).

A species AI is only weights on the 1536-number sound description of the Perch family (Perch V2 and the Kinglet
models made from it), so its file is small and holds no audio. A file made with another AI of that family is
taken with a warning: it runs, but may do worse than where it was made. An imported AI is a new AI of the site;
the one made here stays and is current again when the imported one is deleted.
"""
import base64
import csv
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import expit

from storage import CatalogError, connect

SCHEMA = '''
    CREATE TABLE IF NOT EXISTS species_ai_options (
        site_id INTEGER NOT NULL REFERENCES sites, species_id TEXT NOT NULL REFERENCES species,
        all_sites INTEGER NOT NULL DEFAULT 0 CHECK(all_sites IN (0,1)), PRIMARY KEY(site_id,species_id));
    CREATE TABLE IF NOT EXISTS species_ai_choice (
        site_id INTEGER NOT NULL, species_id TEXT NOT NULL, model_id INTEGER NOT NULL, PRIMARY KEY(site_id,species_id));
    CREATE TABLE IF NOT EXISTS species_ai_seen (
        site_id INTEGER NOT NULL, species_id TEXT NOT NULL, model_id INTEGER NOT NULL, round INTEGER NOT NULL,
        PRIMARY KEY(site_id,species_id,model_id,round));
'''
FORMAT, VERSION = 'echoroo-species-ai', 2
SPEC_END = '_embedding_1536_l2_v1'
FAMILY = ('perch', 'kinglet')
COLUMNS = ('file', 'recording_start', 'start_s', 'end_s', 'scientific_name', 'name', 'verdict', 'screen')


def all_sites(db, site_id, species_id):
    row = db.execute('SELECT all_sites FROM species_ai_options WHERE site_id=? AND species_id=?', (site_id, species_id)).fetchone()
    return bool(row and row[0])


def set_all_sites(root, dataset_id, species_id, on):
    with connect(root) as db:
        site = db.execute('SELECT site_id FROM datasets WHERE id=?', (dataset_id,)).fetchone()
        if site is None or not db.execute('SELECT 1 FROM species WHERE id=?', (species_id,)).fetchone():
            raise CatalogError('not_found')
        db.execute('''INSERT INTO species_ai_options(site_id,species_id,all_sites) VALUES(?,?,?)
            ON CONFLICT(site_id,species_id) DO UPDATE SET all_sites=excluded.all_sites''', (site[0], species_id, int(bool(on))))
        db.execute('DELETE FROM workflow_cache')
    return {'all_sites': bool(on)}


def elsewhere(root, ctx, species_id):
    """Material of the species at the other sites analysed with the same main AI, without their test sounds:
    ({segment: {'label','origin','recording_id','site_id'}}, their analysis ids)."""
    from custom_models import _main_analyses, _materials
    from results_workflow import context
    with connect(root) as db:
        others = [tuple(r) for r in db.execute('SELECT site_id,MAX(id) FROM datasets WHERE site_id!=? GROUP BY site_id', (ctx['site']['id'],))]
    found, analyses = {}, []
    for site_id, dataset_id in others:
        try:
            other = context(root, dataset_id)
        except CatalogError:
            continue  # nothing analysed there yet
        with connect(root) as db:
            material = _materials(db, other, species_id)[1]
            if material:
                analyses += [a['id'] for a in _main_analyses(db, other)]
        found.update({segment: {**value, 'site_id': site_id} for segment, value in material.items()})
    return found, analyses


def state(root, ctx, species_id):
    """Whether other sites' answers are used for this species here, and how many there are."""
    with connect(root) as db:
        on = all_sites(db, ctx['site']['id'], species_id)
    found, analyses = elsewhere(root, ctx, species_id)
    return {'all_sites': on, 'positive_5s': sum(v['label'] == 'target' for v in found.values()),
            'negative_5s': sum(v['label'] == 'other' for v in found.values()), 'sites': len({v['site_id'] for v in found.values()}),
            'material': found if on else {}, 'analysis_ids': analyses if on else []}


def _model(root, model_id):
    with connect(root) as db:
        row = db.execute('SELECT * FROM custom_models WHERE id=?', (model_id,)).fetchone()
        if row is None:
            raise CatalogError('not_found')
        species = db.execute('SELECT * FROM species WHERE id=?', (row['species_id'],)).fetchone()
        site = db.execute('SELECT s.* FROM sites s JOIN datasets d ON d.site_id=s.id WHERE d.id=?', (row['dataset_id'],)).fetchone()
    return row, species, dict(site)


def export_model(root, model_id, folder, hide_site=False):
    """Write the species AI as one file into `folder` and return its path. No recording and no sound goes in: its
    material goes as sound descriptions, so it can be grown further on another PC."""
    import test_sounds
    from results_workflow import context
    from species import names_for
    parent = Path(folder).expanduser().resolve()
    if not parent.is_dir():
        raise CatalogError('folder_missing')
    row, species, site = _model(root, model_id)
    snap = json.loads(row['snapshot'])
    try:
        with np.load(row['artifact_path'], allow_pickle=False) as saved:
            weights, intercept = saved['weights'].astype('<f4'), float(saved['intercept'])
    except (OSError, KeyError, ValueError) as error:
        raise CatalogError('custom_model_files_missing') from error
    taken = snap.get('imported')
    if taken:
        # A copy goes out as the AI it is: made elsewhere, with that site's own result.
        tests, made_at, number, link = taken.get('tests'), taken.get('made_at'), taken.get('round') or snap.get('round', 1), taken.get('parent')
        where = None if hide_site or not taken.get('site') else {'name': taken['site'], 'latitude': None, 'longitude': None}
    else:
        try:
            report = test_sounds.report(root, context(root, row['dataset_id'], snap['scope']['start_date'], snap['scope']['end_date']), row['species_id'])
        except CatalogError:
            report = {'state': 'none'}
        tests = {'sounds': report['sounds'], 'threshold': report['threshold'], 'rounds': report['rounds']} if report['state'] == 'ready' else None
        made_at, number, link = row['created_at'], snap.get('round', 1), snap.get('parent')
        where = None if hide_site else {'name': site['name'], 'latitude': site['latitude'], 'longitude': site['longitude']}
    material = load_material(Path(row['artifact_path']).parent / MATERIAL)
    made = datetime.now(timezone.utc)
    data = {'format': FORMAT, 'version': VERSION, 'exported_at': made.isoformat(timespec='seconds'),
            'species': {k: v for k, v in names_for(species).items() if k != 'id'},
            'feature': snap['feature'], 'weights': base64.b64encode(weights.tobytes()).decode(), 'intercept': intercept,
            'round': number, 'made_at': made_at,
            'training': {k: snap['training'].get(k) for k in ('positive_5s', 'negative_5s', 'other_sites')},
            'tests': tests, 'site': where,
            'parent': None if not link else {'round': link.get('round'), 'site': None if hide_site else link.get('site')},
            'material': {'vectors': base64.b64encode(np.asarray([e['vector'] for e in material], dtype='<f2').tobytes()).decode(),
                         'voice': [e['label'] == 'target' for e in material], 'recordings': [e['recording'] for e in material],
                         'starts': [e['start_s'] for e in material], 'sites': [None if hide_site else e['site'] for e in material]}}
    name = ''.join(c if c.isalnum() else '_' for c in species['scientific_name'])
    # The site's time, as the checked-sounds file is named.
    destination = parent / f"echoroo-ai-{name}-{made.astimezone(ZoneInfo(site['timezone'])):%Y%m%d-%H%M%S}.json"
    try:
        with open(destination, 'x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1)
    except OSError as error:
        raise CatalogError('report_write_failed') from error
    return {'path': str(destination)}


def _read(root, path):
    """The checked contents of an AI file: (data, weights, intercept, species row, counts, warning, material)."""
    from models import main_identity
    if not Path(path).is_file():
        raise CatalogError('file_missing')
    try:
        if Path(path).stat().st_size > 200_000_000:
            raise CatalogError('ai_file_invalid')
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise CatalogError('ai_file_invalid') from error
    if not isinstance(data, dict) or data.get('format') != FORMAT or not isinstance(data.get('version'), int):
        raise CatalogError('ai_file_invalid')
    if data['version'] > VERSION:
        raise CatalogError('ai_file_newer')
    try:
        weights = np.frombuffer(base64.b64decode(data['weights'], validate=True), dtype='<f4').astype(np.float64)
        intercept = float(data['intercept'])
        feature, name = data['feature'], data['species']['scientific_name']
        spec, engine, sha = str(feature['spec']), str(feature['engine']), str(feature['model_sha256'])
        counts = {k: int(data['training'][k]) for k in ('positive_5s', 'negative_5s')}
        material = []
        if data.get('material'):  # version 2: the material as sound descriptions
            given = data['material']
            vectors = np.frombuffer(base64.b64decode(given['vectors'], validate=True), dtype='<f2').reshape(-1, 1536).astype(np.float32)
            if not len(vectors) == len(given['voice']) == len(given['recordings']) == len(given['starts']) == len(given['sites']) or not np.isfinite(vectors).all():
                raise ValueError('material')
            material = [{'vector': vector, 'label': 'target' if voice is True else 'other', 'recording': str(recording)[:24],
                         'start_s': float(start), 'site': str(site)[:200] if site else None}
                        for vector, voice, recording, start, site in zip(vectors, given['voice'], given['recordings'], given['starts'], given['sites'])]
    except (KeyError, TypeError, ValueError) as error:
        raise CatalogError('ai_file_invalid') from error
    # Only the Perch family shares the sound description these weights apply to; a 5-second window is the only one made.
    if len(weights) != 1536 or not np.isfinite(weights).all() or not np.isfinite(intercept) or abs(intercept) > 1e4 \
            or not engine.startswith(FAMILY) or spec != f'{engine}{SPEC_END}' or feature.get('window_s') != 5:
        raise CatalogError('ai_file_invalid')
    with connect(root) as db:
        species = db.execute('''SELECT * FROM species WHERE id=(SELECT COALESCE(accepted_id,id) FROM species
            WHERE scientific_name=? ORDER BY accepted_id IS NOT NULL LIMIT 1)''', (name,)).fetchone()
    if species is None:
        raise CatalogError('ai_file_species_unknown')
    here, here_sha = main_identity(root)
    if not here.startswith(FAMILY):
        raise CatalogError('ai_file_main_engine')
    warning = None if engine == here and (here_sha is None or sha == here_sha) else 'ai_file_other_engine'
    return data, weights, intercept, species, counts, warning, material


def _file_score(tests):
    """The file's result where it was made, as {'heard','not_heard','found','false'} (None without one)."""
    row = next((r for r in (tests or {}).get('rounds', []) if r.get('current') and r.get('found') is not None), None)
    return None if row is None else {**tests['sounds'], 'found': row['found'], 'false': row['false']}


def inspect(root, path, ctx=None):
    """What an AI file holds, before it is taken in; with `ctx`, also how it does on that site's test sounds."""
    import test_sounds
    from models import main_engine
    from species import names_for
    data, weights, intercept, species, counts, warning, material = _read(root, path)
    here = _score(test_sounds.judge(root, ctx, species['id'], [(weights, intercept)])) if ctx else None
    return {'species': names_for(species), 'engine': data['feature']['engine'], 'main_engine': main_engine(root), 'warning': warning,
            'round': data.get('round', 1), 'made_at': data.get('made_at'), 'tests': data.get('tests'), 'here': here,
            'training': {**counts, 'other_sites': (data.get('training') or {}).get('other_sites')},
            'material': len(material), 'site': (data.get('site') or {}).get('name'), 'parent': data.get('parent')}


def import_model(root, ctx, path, species_id=None):
    """Take an AI file in as a new species AI of the site and score the site's sounds with it. With `species_id`
    the file must be for that species."""
    data, weights, intercept, species, counts, warning, material = _read(root, path)
    if species_id is not None and species['id'] != species_id:
        raise CatalogError('ai_file_other_species')
    source = {'file': Path(path).name, 'from_model': None, 'engine': data['feature']['engine'], 'warning': warning, 'made_at': data.get('made_at'),
              'site': (data.get('site') or {}).get('name'), 'round': int(data.get('round', 1)), 'parent': data.get('parent'),
              'tests': data.get('tests'), 'score': _file_score(data.get('tests'))}
    return _take(root, ctx, species['id'], weights, intercept, counts, source, material, (data.get('training') or {}).get('other_sites'))


def _take(root, ctx, species_id, weights, intercept, counts, source, material, other_sites):
    """A new species AI of the site from given weights (a copy: `source` says where from), scoring the site's sounds;
    it becomes the AI the site uses."""
    from custom_models import _digest, _main_analyses, _vector_batches, get_model
    from models import feature_spec, main_engine
    from reports import window_in_scope
    engine = main_engine(root)
    with connect(root) as db:
        analyses = _main_analyses(db, ctx)
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))}
    shas = {json.loads(a['metadata']).get('model_sha256') for a in analyses}
    if len(shas) != 1:
        raise CatalogError('custom_perch_mixed' if shas else 'custom_perch_missing')
    zone = ZoneInfo(ctx['site']['timezone'])
    rows, scored = [], []
    for batch, matrix in _vector_batches(root, [a['id'] for a in analyses]):
        for row, score in zip(batch, expit(matrix @ weights + intercept)):
            record = records.get(row['recording_id'])
            if record and window_in_scope(record, row['start_s'], row['end_s'], zone, ctx['start_date'], ctx['end_date']):
                rows.append(row)
                scored.append(float(score))
    snapshot = {'schema': 'custom_model_v2', 'species_id': species_id, 'run_id': None, 'source_fingerprint': ctx['fingerprint'],
        'round': int(source.get('round') or 1), 'rounds': [],
        'scope': {'site_id': ctx['site']['id'], 'start_date': ctx['start_date'], 'end_date': ctx['end_date'], 'recording_ids': sorted(records)},
        'feature': {'engine': engine, 'model_sha256': shas.pop(), 'spec': feature_spec(engine), 'window_s': 5},
        'training': {**counts, 'l2': None, 'calibrated': None, 'examples': [], 'other_sites': other_sites},
        'imported': source, 'analysis_ids': [a['id'] for a in analyses], 'inference': {'scored_5s': len(scored)}}
    folder = Path(root) / 'custom-models'
    folder.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.new-', dir=folder))
    try:
        np.savez_compressed(temporary / 'model.npz', weights=weights, intercept=np.array(intercept), l2=np.array(0.0),
                            feature_spec=np.array(snapshot['feature']['spec']), model_sha256=np.array(snapshot['feature']['model_sha256']))
        np.savez_compressed(temporary / 'scores.npz', segment_ids=np.asarray([r['segment_id'] for r in rows], dtype=np.int64),
            analysis_ids=np.asarray([r['analysis_id'] for r in rows], dtype=np.int64),
            recording_ids=np.asarray([r['recording_id'] for r in rows], dtype=np.int64),
            start_s=np.asarray([r['start_s'] for r in rows]), end_s=np.asarray([r['end_s'] for r in rows]), scores=np.asarray(scored))
        save_material(temporary / MATERIAL, material)
        artifact, scores = _digest(temporary / 'model.npz'), _digest(temporary / 'scores.npz')
        with connect(root) as db:
            model_id = db.execute('''INSERT INTO custom_models(dataset_id,species_id,run_id,snapshot,artifact_path,artifact_sha256,
                scores_path,scores_sha256) VALUES(?,?,NULL,'{}','','','','')''', (ctx['dataset']['id'], species_id)).lastrowid
            final = folder / str(model_id)
            os.replace(temporary, final)
            db.execute('UPDATE custom_models SET snapshot=?,artifact_path=?,artifact_sha256=?,scores_path=?,scores_sha256=? WHERE id=?',
                       (json.dumps(snapshot, allow_nan=False), str(final / 'model.npz'), artifact, str(final / 'scores.npz'), scores, model_id))
            choose(db, ctx['site']['id'], species_id, model_id)
            db.execute('DELETE FROM workflow_cache')
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return get_model(root, model_id)


# --- Versions: an AI of one site tried and grown at another, and chosen per site. ---
# A species AI is one row of custom_models (made again in place: its round goes up). Taken from another site of this
# PC or from a file, it is copied as a new row of this site (`imported` in its snapshot: where it came from). Every row
# keeps the material it was made from (`MATERIAL`: sound descriptions, never audio), so an AI grown from a copy is made
# from that material and this site's answers, here or on another PC. Each site uses one AI per species
# (`species_ai_choice`, else the newest); a new version elsewhere never changes it.

MATERIAL, INHERITED = 'material.npz', 'inherited.npz'


def recording_key(path, recorded_at):
    """A recording told apart by its file name and start, the same on every PC, as a short digest (no name in files)."""
    start = datetime.fromisoformat(str(recorded_at).replace('Z', '+00:00'))
    start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    return hashlib.sha256(f'{Path(path).name.casefold()}|{start.astimezone(timezone.utc).isoformat()}'.encode()).hexdigest()[:24]


def save_material(folder, entries):
    """`entries`: [{'vector', 'label' ('target'|'other'), 'recording' (recording_key), 'start_s', 'site' (name or None)}]."""
    np.savez_compressed(Path(folder), vectors=np.asarray([e['vector'] for e in entries], dtype='<f2').reshape(-1, 1536),
                        voice=np.asarray([e['label'] == 'target' for e in entries], dtype=bool),
                        recordings=np.asarray([e['recording'] for e in entries], dtype='U24'),
                        starts=np.asarray([float(e['start_s']) for e in entries], dtype=np.float64),
                        sites=np.asarray([e['site'] or '' for e in entries], dtype=str))


def load_material(path):
    try:
        with np.load(Path(path), allow_pickle=False) as saved:
            return [{'vector': vector.astype(np.float32), 'label': 'target' if voice else 'other', 'recording': str(recording),
                     'start_s': float(start), 'site': str(site) or None}
                    for vector, voice, recording, start, site in zip(saved['vectors'], saved['voice'], saved['recordings'], saved['starts'], saved['sites'])]
    except (OSError, KeyError, ValueError):
        return []


def _counts(entries):
    sites = sorted({e['site'] for e in entries if e['site']})
    return {'positive_5s': sum(e['label'] == 'target' for e in entries), 'negative_5s': sum(e['label'] == 'other' for e in entries), 'sites': sites}


def origin(model):
    """(row id or None, round) of the AI a row is: a copy names the row it was copied from (None for a file)."""
    taken = model.get('imported')
    if taken:
        return taken.get('from_model'), taken.get('round') or model.get('round', 1)
    return model['id'], model.get('round', 1)


def inherited(root, ctx, model, own):
    """What an AI made here now takes from the AI it grows from, and the link to it ({'model_id','round','site'} or None).

    Grown from a copy (`model` imported): all the copy's material. Made again in place: what it took the first time.
    Material from a recording of this site is left out (this site's answers as they are now count instead, and its
    test sounds are never material), as is a sound also in `own` (recording key, start) taken from this PC's sites."""
    if not model:
        return [], None
    folder = Path(root) / 'custom-models' / str(model['id'])
    with connect(root) as db:
        here = {recording_key(r['path'], r['recorded_at']) for r in db.execute(
            'SELECT r.path,r.recorded_at FROM recordings r JOIN datasets d ON d.id=r.dataset_id WHERE d.site_id=?', (ctx['site']['id'],))}
        snap = json.loads(db.execute('SELECT snapshot FROM custom_models WHERE id=?', (model['id'],)).fetchone()[0])
    if model.get('imported'):
        entries, taken = load_material(folder / MATERIAL), model['imported']
        parent = {'model_id': taken.get('from_model'), 'round': taken.get('round'), 'site': taken.get('site'), 'file': taken.get('file')}
    else:
        entries, parent = load_material(folder / INHERITED), snap.get('parent')
    return [e for e in entries if e['recording'] not in here and (e['recording'], e['start_s']) not in own], parent


def _site_rows(root, species_id):
    """Every species AI of the species on this PC: rows with their site."""
    with connect(root) as db:
        return [dict(r) for r in db.execute('''SELECT m.*,d.site_id,s.name AS site_name,s.timezone FROM custom_models m
            JOIN datasets d ON d.id=m.dataset_id JOIN sites s ON s.id=d.site_id WHERE m.species_id=? ORDER BY m.id''', (species_id,))]


def _current_rows(root, species_id, rows=None):
    """{site id: the row that site uses}: its choice, else its newest row made with the main AI."""
    from models import main_engine
    engine = main_engine(root)
    rows = _site_rows(root, species_id) if rows is None else rows
    with connect(root) as db:
        chosen = {r[0]: r[1] for r in db.execute('SELECT site_id,model_id FROM species_ai_choice WHERE species_id=?', (species_id,))}
    by_site = {}
    for row in rows:
        if json.loads(row['snapshot'])['feature']['engine'] == engine:
            by_site.setdefault(row['site_id'], []).append(row)
    current = {}
    for site_id, its in by_site.items():
        current[site_id] = next((r for r in its if r['id'] == chosen.get(site_id)), None) or max(its, key=lambda r: r['id'])
    return current


def choose(db, site_id, species_id, model_id):
    db.execute('''INSERT INTO species_ai_choice(site_id,species_id,model_id) VALUES(?,?,?)
        ON CONFLICT(site_id,species_id) DO UPDATE SET model_id=excluded.model_id''', (site_id, species_id, model_id))


def _weights(row):
    with np.load(row['artifact_path'], allow_pickle=False) as saved:
        return saved['weights'].astype(np.float64), float(saved['intercept'])


def _describe(row):
    """The name parts of a row: where and in which round it was made, and what it grew from."""
    snap = json.loads(row['snapshot'])
    taken = snap.get('imported')
    if taken:
        return {'site': taken.get('site'), 'round': taken.get('round') or snap.get('round', 1), 'file': taken.get('file'),
                'parent': taken.get('parent'), 'made_at': taken.get('made_at')}
    return {'site': row['site_name'], 'round': snap.get('round', 1), 'file': None, 'parent': snap.get('parent'), 'made_at': row['created_at']}


def _score(found):
    return None if found is None else {**found['sounds'], 'found': found['results'][0]['found'], 'false': found['results'][0]['false']}


def try_there(root, row, species_id):
    """How a row does on the test sounds of the site it was made at (None without them)."""
    import test_sounds
    from results_workflow import context
    snap = json.loads(row['snapshot'])
    try:
        ctx = context(root, row['dataset_id'], snap['scope']['start_date'], snap['scope']['end_date'])
        return _score(test_sounds.judge(root, ctx, species_id, [_weights(row)]))
    except (CatalogError, OSError, KeyError, ValueError):
        return None


def versions(root, ctx, species_id):
    """The species' AIs on this PC, as versions: for the list of AIs, the AIs of other sites to try here, and a newer
    version grown from the AI this site uses.

    `versions`: one per AI made at a site or taken from a file (a copy from another site of this PC is not a version
    of its own), with where it is used and how it does on this site's test sounds. `others`: the AI each other site
    uses. `newer`: a version at another site that grew from the AI this site uses (or that AI made again there), not
    yet set aside here, with both AIs tried on this site's test sounds."""
    import test_sounds
    from custom_models import list_models
    from models import main_engine
    engine = main_engine(root)
    rows = [r for r in _site_rows(root, species_id) if json.loads(r['snapshot'])['feature']['engine'].startswith(FAMILY)]
    current = _current_rows(root, species_id, rows)
    by_id = {r['id']: r for r in rows}
    here_models = list_models(root, ctx, species_id, every=True)
    here = by_id.get(here_models[0]['id']) if here_models else None
    copy_of = lambda row: (json.loads(row['snapshot']).get('imported') or {}).get('from_model')
    # The row a site's AI is, and its round: a copy stands for the row it came from.
    use = {}
    for site_id, row in current.items():
        snap = json.loads(row['snapshot'])
        key = copy_of(row) or row['id']
        use.setdefault(key, []).append({'site': row['site_name'], 'site_id': site_id, 'round': (snap.get('imported') or {}).get('round') or snap.get('round', 1)})
    listed = [r for r in rows if not copy_of(r) or copy_of(r) not in by_id]
    try:
        tried = test_sounds.judge(root, ctx, species_id, [_weights(r) for r in listed])
    except (OSError, KeyError, ValueError):
        tried = None
    entries = []
    for index, row in enumerate(listed):
        snap = json.loads(row['snapshot'])
        entries.append({'id': row['id'], **_describe(row), 'here': bool(here) and (copy_of(here) or here['id']) == row['id'],
                        'at_this_site': row['site_id'] == ctx['site']['id'], 'used': use.get(row['id'], []),
                        'engine': snap['feature']['engine'], 'warning': snap['feature']['engine'] != engine,
                        'score': None if tried is None else {**tried['sounds'], 'found': tried['results'][index]['found'], 'false': tried['results'][index]['false']}})
    others = []
    for site_id, row in current.items():
        if site_id == ctx['site']['id']:
            continue
        snap = json.loads(row['snapshot'])
        others.append({'id': row['id'], **_describe(row), 'engine': snap['feature']['engine'], 'warning': snap['feature']['engine'] != engine,
                       'training': {k: snap['training'].get(k) for k in ('positive_5s', 'negative_5s')}, 'score': try_there(root, row, species_id)})
    return {'versions': entries, 'others': others, 'current_id': here['id'] if here else None, 'newer': _newer(root, ctx, species_id, rows, here, current)}


def _newer(root, ctx, species_id, rows, here, current):
    import test_sounds
    if not here:
        return None
    by_id = {r['id']: r for r in rows}
    snap = json.loads(here['snapshot'])
    key, have = (snap['imported'].get('from_model'), snap['imported'].get('round')) if snap.get('imported') else (here['id'], snap.get('round', 1))
    if key is None:
        return None

    def grew_from(row, seen=()):
        parent = (json.loads(row['snapshot']).get('parent') or {}).get('model_id')
        return parent == key or parent in by_id and parent not in seen and grew_from(by_id[parent], (*seen, parent))
    with connect(root) as db:
        aside = {tuple(r) for r in db.execute('SELECT model_id,round FROM species_ai_seen WHERE site_id=? AND species_id=?', (ctx['site']['id'], species_id))}
    # The round of another site's AI that the AI here grew from is not offered back; a later round of it is.
    basis = snap.get('parent') or {}
    found = []
    for site_id, row in current.items():
        own = json.loads(row['snapshot'])
        if site_id == ctx['site']['id'] or own.get('imported'):
            continue
        number = own.get('round', 1)
        if row['id'] == basis.get('model_id') and number <= (basis.get('round') or 0):
            continue
        if (row['id'] == key and number > have or row['id'] != key and grew_from(row)) and (row['id'], number) not in aside:
            found.append(row)
    if not found:
        return None
    row = max(found, key=lambda r: r['created_at'])
    tried = test_sounds.judge(root, ctx, species_id, [_weights(here), _weights(row)])
    differ = []
    if tried:
        now, new = (result['said'] for result in tried['results'])
        differ = [{**tried['rows'][s], 'now': now[s], 'new': new[s]} for s in tried['rows'] if now[s] != new[s]]
    material = json.loads(row['snapshot'])['training']
    return {'id': row['id'], **_describe(row), 'training': {k: material.get(k) for k in ('positive_5s', 'negative_5s', 'inherited', 'other_sites')},
            'tests': None if tried is None else {**tried['sounds'], 'now': {k: tried['results'][0][k] for k in ('found', 'false')},
                                                 'new': {k: tried['results'][1][k] for k in ('found', 'false')}},
            'differ': differ}


def set_aside(root, ctx, species_id, model_id):
    """Keep using the AI here: the newer version `model_id` (as it is now) is not offered again."""
    with connect(root) as db:
        row = db.execute('SELECT snapshot FROM custom_models WHERE id=? AND species_id=?', (model_id, species_id)).fetchone()
        if row is None:
            raise CatalogError('not_found')
        db.execute('INSERT OR IGNORE INTO species_ai_seen(site_id,species_id,model_id,round) VALUES(?,?,?,?)',
                   (ctx['site']['id'], species_id, model_id, json.loads(row[0]).get('round', 1)))
        db.execute('DELETE FROM workflow_cache')
    return {'model_id': model_id}


def use(root, ctx, model_id):
    """Use the AI `model_id` at this site: one of its own is chosen; one of another site is copied here (or the copy
    made before, when it is still the same round) and chosen."""
    from custom_models import get_model
    from models import main_engine, main_identity
    with connect(root) as db:
        row = db.execute('''SELECT m.*,d.site_id,s.name AS site_name FROM custom_models m JOIN datasets d ON d.id=m.dataset_id
            JOIN sites s ON s.id=d.site_id WHERE m.id=?''', (model_id,)).fetchone()
        if row is None:
            raise CatalogError('not_found')
        row = dict(row)
        if row['site_id'] == ctx['site']['id']:
            choose(db, ctx['site']['id'], row['species_id'], model_id)
            db.execute('DELETE FROM workflow_cache')
            return get_model(root, model_id)
    snap = json.loads(row['snapshot'])
    engine, sha = snap['feature']['engine'], snap['feature']['model_sha256']
    here, here_sha = main_identity(root)
    if not here.startswith(FAMILY) or not engine.startswith(FAMILY):
        raise CatalogError('ai_file_main_engine')
    taken = snap.get('imported')
    source = dict(taken) if taken else {'file': None, 'from_model': row['id'], 'site': row['site_name'], 'round': snap.get('round', 1),
                                         'parent': snap.get('parent'), 'made_at': row['created_at'], 'score': try_there(root, row, row['species_id'])}
    source.update(engine=engine, warning=None if engine == here and (here_sha is None or sha == here_sha) else 'ai_file_other_engine')
    with connect(root) as db:
        for other in db.execute('''SELECT m.id,m.snapshot FROM custom_models m JOIN datasets d ON d.id=m.dataset_id WHERE d.site_id=? AND m.species_id=?''',
                                (ctx['site']['id'], row['species_id'])):
            was = json.loads(other['snapshot'])
            if was.get('imported') and was['imported'].get('from_model') == source.get('from_model') and source.get('from_model') is not None \
                    and was['imported'].get('round') == source.get('round') and [was['scope'][k] for k in ('start_date', 'end_date')] == [ctx['start_date'], ctx['end_date']]:
                choose(db, ctx['site']['id'], row['species_id'], other['id'])
                db.execute('DELETE FROM workflow_cache')
                return get_model(root, other['id'])
    weights, intercept = _weights(row)
    material = load_material(Path(root) / 'custom-models' / str(row['id']) / MATERIAL)
    counts = {k: int(snap['training'][k]) for k in ('positive_5s', 'negative_5s')}
    return _take(root, ctx, row['species_id'], weights, intercept, counts, source, material, snap['training'].get('other_sites'))


def _start(recorded_at, zone):
    start = datetime.fromisoformat(recorded_at.replace('Z', '+00:00'))
    return (start if start.tzinfo else start.replace(tzinfo=timezone.utc)).astimezone(zone)


def export_heard(root, dataset_id, folder):
    """Write the site's current answers as a CSV table into `folder`: file name, time, species and what was heard.
    "No bird sound" is a row with scientific_name `(no bird)` and verdict `present`."""
    from labels import current, no_bird
    parent = Path(folder).expanduser().resolve()
    if not parent.is_dir():
        raise CatalogError('folder_missing')
    with connect(root) as db:
        site = db.execute('SELECT s.* FROM sites s JOIN datasets d ON d.site_id=s.id WHERE d.id=?', (dataset_id,)).fetchone()
        if site is None:
            raise CatalogError('not_found')
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (dataset_id,))}
        from species import NO_BIRD, names_for
        # "No bird sound" taken back since is no answer.
        rows = [row for row in current(db, dataset_id) if row['verdict'] == 'present' or row['taxon_id'] != no_bird(db)]
        species = {r['id']: r for r in db.execute('SELECT * FROM species WHERE id IN (SELECT value FROM json_each(?))',
                   (json.dumps(sorted({row['taxon_id'] for row in rows})),))}
        names = {key: '鳥の声がしない' if r['scientific_name'] == NO_BIRD else names_for(r)['common_names'].get('ja') or names_for(r)['common_names'].get('en') or '' for key, r in species.items()}
    if not rows:
        raise CatalogError('nothing_to_export')
    zone, made = ZoneInfo(site['timezone']), datetime.now(timezone.utc)
    destination = parent / f'echoroo-heard-{dataset_id}-{made.astimezone(zone):%Y%m%d-%H%M%S}.csv'
    try:
        with open(destination, 'x', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            for row in sorted(rows, key=lambda r: (records[r['recording_id']]['recorded_at'], r['start_s'], r['taxon_id'])):
                record, taxon = records[row['recording_id']], species[row['taxon_id']]
                start = _start(record['recorded_at'], zone)
                writer.writerow([Path(record['path']).name, start.isoformat(timespec='seconds'), f"{row['start_s']:g}", f"{row['end_s']:g}",
                                 taxon['scientific_name'], names[row['taxon_id']], row['verdict'], row['source']])
    except OSError as error:
        raise CatalogError('report_write_failed') from error
    return {'path': str(destination), 'rows': len(rows)}


def import_heard(root, dataset_id, path):
    """Take answers in from a table written by `export_heard` (or by hand in the same columns). A row is matched to
    a recording of the site by its file name; rows for a recording or species not found here are counted and left out.
    A sound that already has an answer for the species here keeps it: a different answer in the table is counted and left out."""
    from labels import LATEST, VERDICTS, no_bird, put, put_no_bird
    if not Path(path).is_file():
        raise CatalogError('file_missing')
    try:
        if Path(path).stat().st_size > 50_000_000:
            raise CatalogError('heard_file_invalid')
        raw = Path(path).read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp932')  # a table saved again from Excel in Japan
        table = list(csv.DictReader(text.splitlines()))
    except (OSError, ValueError, csv.Error) as error:
        raise CatalogError('heard_file_invalid') from error
    if not table or not {'file', 'start_s', 'end_s', 'scientific_name', 'verdict'} <= set(table[0]):
        raise CatalogError('heard_file_invalid')
    result = {'added': 0, 'different': 0, 'same': 0, 'no_recording': 0, 'no_species': 0, 'invalid': 0}
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM datasets WHERE id=?', (dataset_id,)).fetchone():
            raise CatalogError('not_found')
        by_name = {}
        zone = ZoneInfo(db.execute('SELECT s.timezone FROM sites s JOIN datasets d ON d.site_id=s.id WHERE d.id=?', (dataset_id,)).fetchone()[0])
        for record in db.execute('SELECT id,path,seconds,recorded_at FROM site_recordings WHERE scope_id=?', (dataset_id,)):
            by_name.setdefault(Path(record['path']).name.casefold(), []).append(record)
        species = {r['scientific_name']: r['id'] for r in db.execute('SELECT scientific_name,COALESCE(accepted_id,id) AS id FROM species')}
        touched, none = set(), no_bird(db)
        for line in table:
            try:
                start, end, verdict = float(line['start_s']), float(line['end_s']), line['verdict'].strip()
            except (TypeError, ValueError, AttributeError):
                result['invalid'] += 1
                continue
            found = by_name.get(Path(str(line['file']).replace('\\', '/')).name.casefold(), [])
            if (line.get('recording_start') or '').strip():
                # The start time, when the table has it, tells apart recordings of the same file name and catches another site's file.
                try:
                    wanted = datetime.fromisoformat(line['recording_start'].strip())
                    found = [r for r in found if abs((_start(r['recorded_at'], zone) - (wanted if wanted.tzinfo else wanted.replace(tzinfo=zone))).total_seconds()) < 1]
                except ValueError:
                    found = []
            taxon = species.get(str(line['scientific_name']).strip())
            if verdict not in VERDICTS or not 0 <= start < end:
                result['invalid'] += 1
            elif len(found) != 1 or end > found[0]['seconds'] + 1e-6:
                result['no_recording'] += 1
            elif taxon is None:
                result['no_species'] += 1
            else:
                piece = (taxon, found[0]['id'], start, end)
                now = db.execute(f'SELECT l.verdict FROM labels l WHERE l.taxon_id=? AND l.recording_id=? AND l.start_s=? AND l.end_s=? AND {LATEST}', piece).fetchone()
                # "No bird sound" and a species heard cannot both hold for one sound.
                clash = (taxon == none or verdict == 'present') and db.execute(f'''SELECT 1 FROM labels l WHERE l.recording_id=? AND l.start_s=? AND l.end_s=?
                    AND l.verdict='present' AND (l.taxon_id=?)<>? AND {LATEST}''', (*piece[1:], none, taxon == none)).fetchone()
                if taxon == none and verdict != 'present':
                    result['invalid'] += 1
                elif now is not None and not (taxon == none and now[0] != 'present'):
                    # An answer already here is never replaced by a table: a different one is counted and left out.
                    result['same' if now[0] == verdict else 'different'] += 1
                elif clash:
                    result['different'] += 1
                elif taxon == none:
                    put_no_bird(db, *piece[1:], 'import', {'file': Path(path).name})
                    result['added'] += 1
                else:
                    put(db, *piece, verdict, 'import', {'file': Path(path).name}, history=True)
                    touched.add(piece)
                    result['added'] += 1
        from validation import refresh
        for piece in touched:
            refresh(root, db, *piece)
        db.execute('DELETE FROM workflow_cache')
    return result
