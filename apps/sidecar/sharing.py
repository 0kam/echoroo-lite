"""Passing material and species AIs around: answers of other sites as material, a species AI as one file, and the
answers given by ear as a table (CSV).

A species AI is only weights on the 1536-number sound description of the Perch family (Perch V2 and the Kinglet
models made from it), so its file is small and holds no audio. A file made with another AI of that family is
taken with a warning: it runs, but may do worse than where it was made. An imported AI is a new AI of the site;
the one made here stays and is current again when the imported one is deleted.
"""
import base64
import csv
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
'''
FORMAT, VERSION = 'echoroo-species-ai', 1
SPEC_END = '_embedding_1536_l2_v1'
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
    """Write the species AI as one file into `folder` and return its path. No recording and no sound goes in."""
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
    try:
        report = test_sounds.report(root, context(root, row['dataset_id'], snap['scope']['start_date'], snap['scope']['end_date']), row['species_id'])
    except CatalogError:
        report = {'state': 'none'}
    tests = {'sounds': report['sounds'], 'threshold': report['threshold'], 'rounds': report['rounds']} if report['state'] == 'ready' else None
    made = datetime.now(timezone.utc)
    data = {'format': FORMAT, 'version': VERSION, 'exported_at': made.isoformat(timespec='seconds'),
            'species': {k: v for k, v in names_for(species).items() if k != 'id'},
            'feature': snap['feature'], 'weights': base64.b64encode(weights.tobytes()).decode(), 'intercept': intercept,
            'round': snap.get('round', 1), 'made_at': row['created_at'],
            'training': {k: snap['training'].get(k) for k in ('positive_5s', 'negative_5s', 'other_sites')},
            'tests': tests,
            'site': None if hide_site else {'name': site['name'], 'latitude': site['latitude'], 'longitude': site['longitude']}}
    name = ''.join(c if c.isalnum() else '_' for c in species['scientific_name'])
    destination = parent / f'echoroo-ai-{name}-{made:%Y%m%d-%H%M%S}.json'
    try:
        with open(destination, 'x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1)
    except OSError as error:
        raise CatalogError('report_write_failed') from error
    return {'path': str(destination)}


def _read(root, path):
    """The checked contents of an AI file: (data, weights, species row, warning)."""
    from models import feature_spec, main_engine, main_identity
    try:
        if Path(path).stat().st_size > 5_000_000:
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
    except (KeyError, TypeError, ValueError) as error:
        raise CatalogError('ai_file_invalid') from error
    if len(weights) != 1536 or not np.isfinite(weights).all() or not np.isfinite(intercept) or not spec.endswith(SPEC_END):
        raise CatalogError('ai_file_invalid')
    with connect(root) as db:
        species = db.execute('''SELECT * FROM species WHERE id=(SELECT COALESCE(accepted_id,id) FROM species
            WHERE scientific_name=? ORDER BY accepted_id IS NOT NULL LIMIT 1)''', (name,)).fetchone()
    if species is None:
        raise CatalogError('ai_file_species_unknown')
    here, here_sha = main_identity(root)
    if not feature_spec(here).endswith(SPEC_END):
        raise CatalogError('ai_file_invalid')
    warning = None if engine == here and (here_sha is None or sha == here_sha) else 'ai_file_other_engine'
    return data, weights, intercept, species, counts, warning


def inspect(root, path):
    """What an AI file holds, before it is taken in."""
    from models import main_engine
    from species import names_for
    data, _, _, species, counts, warning = _read(root, path)
    return {'species': names_for(species), 'engine': data['feature']['engine'], 'main_engine': main_engine(root), 'warning': warning,
            'round': data.get('round', 1), 'made_at': data.get('made_at'), 'training': counts, 'tests': data.get('tests'),
            'site': (data.get('site') or {}).get('name')}


def import_model(root, ctx, path, species_id=None):
    """Take an AI file in as a new species AI of the site and score the site's sounds with it. With `species_id`
    the file must be for that species."""
    from custom_models import _digest, _main_analyses, _vector_batches, get_model
    from models import feature_spec, main_engine
    from reports import window_in_scope
    data, weights, intercept, species, counts, warning = _read(root, path)
    if species_id is not None and species['id'] != species_id:
        raise CatalogError('ai_file_other_species')
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
    snapshot = {'schema': 'custom_model_v2', 'species_id': species['id'], 'run_id': None, 'source_fingerprint': ctx['fingerprint'],
        'round': int(data.get('round', 1)), 'rounds': [],
        'scope': {'site_id': ctx['site']['id'], 'start_date': ctx['start_date'], 'end_date': ctx['end_date'], 'recording_ids': sorted(records)},
        'feature': {'engine': engine, 'model_sha256': shas.pop(), 'spec': feature_spec(engine), 'window_s': 5},
        'training': {**counts, 'l2': None, 'calibrated': None, 'examples': []},
        'imported': {'file': Path(path).name, 'engine': data['feature']['engine'], 'warning': warning, 'made_at': data.get('made_at'),
                     'site': (data.get('site') or {}).get('name'), 'tests': data.get('tests')},
        'analysis_ids': [a['id'] for a in analyses], 'inference': {'scored_5s': len(scored)}}
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
        artifact, scores = _digest(temporary / 'model.npz'), _digest(temporary / 'scores.npz')
        with connect(root) as db:
            model_id = db.execute('''INSERT INTO custom_models(dataset_id,species_id,run_id,snapshot,artifact_path,artifact_sha256,
                scores_path,scores_sha256) VALUES(?,?,NULL,'{}','','','','')''', (ctx['dataset']['id'], species['id'])).lastrowid
            final = folder / str(model_id)
            os.replace(temporary, final)
            db.execute('UPDATE custom_models SET snapshot=?,artifact_path=?,artifact_sha256=?,scores_path=?,scores_sha256=? WHERE id=?',
                       (json.dumps(snapshot, allow_nan=False), str(final / 'model.npz'), artifact, str(final / 'scores.npz'), scores, model_id))
            db.execute('DELETE FROM workflow_cache')
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return get_model(root, model_id)


def export_heard(root, dataset_id, folder):
    """Write the site's current answers as a CSV table into `folder`: file name, time, species and what was heard."""
    from labels import current
    parent = Path(folder).expanduser().resolve()
    if not parent.is_dir():
        raise CatalogError('folder_missing')
    with connect(root) as db:
        site = db.execute('SELECT s.* FROM sites s JOIN datasets d ON d.site_id=s.id WHERE d.id=?', (dataset_id,)).fetchone()
        if site is None:
            raise CatalogError('not_found')
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (dataset_id,))}
        rows = current(db, dataset_id)
        from species import names_for
        species = {r['id']: r for r in db.execute('SELECT * FROM species WHERE id IN (SELECT value FROM json_each(?))',
                   (json.dumps(sorted({row['taxon_id'] for row in rows})),))}
        names = {key: names_for(r)['common_names'].get('ja') or names_for(r)['common_names'].get('en') or '' for key, r in species.items()}
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
                start = datetime.fromisoformat(record['recorded_at'].replace('Z', '+00:00'))
                start = (start if start.tzinfo else start.replace(tzinfo=timezone.utc)).astimezone(zone)
                writer.writerow([Path(record['path']).name, start.isoformat(timespec='seconds'), f"{row['start_s']:g}", f"{row['end_s']:g}",
                                 taxon['scientific_name'], names[row['taxon_id']], row['verdict'], row['source']])
    except OSError as error:
        raise CatalogError('report_write_failed') from error
    return {'path': str(destination), 'rows': len(rows)}


def import_heard(root, dataset_id, path):
    """Take answers in from a table written by `export_heard` (or by hand in the same columns). A row is matched to
    a recording of the site by its file name; rows for a recording or species not found here are counted and left out."""
    from labels import LATEST, VERDICTS, put
    try:
        if Path(path).stat().st_size > 50_000_000:
            raise CatalogError('heard_file_invalid')
        with open(path, encoding='utf-8-sig', newline='') as stream:
            table = list(csv.DictReader(stream))
    except (OSError, ValueError, csv.Error) as error:
        raise CatalogError('heard_file_invalid') from error
    if not table or not {'file', 'start_s', 'end_s', 'scientific_name', 'verdict'} <= set(table[0]):
        raise CatalogError('heard_file_invalid')
    result = {'added': 0, 'same': 0, 'no_recording': 0, 'no_species': 0, 'invalid': 0}
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM datasets WHERE id=?', (dataset_id,)).fetchone():
            raise CatalogError('not_found')
        by_name = {}
        for record in db.execute('SELECT id,path,seconds FROM site_recordings WHERE scope_id=?', (dataset_id,)):
            by_name.setdefault(Path(record['path']).name, []).append(record)
        species = {r['scientific_name']: r['id'] for r in db.execute('SELECT scientific_name,COALESCE(accepted_id,id) AS id FROM species')}
        touched = set()
        for line in table:
            try:
                start, end, verdict = float(line['start_s']), float(line['end_s']), line['verdict'].strip()
            except (TypeError, ValueError, AttributeError):
                result['invalid'] += 1
                continue
            found = by_name.get(Path(str(line['file']).replace('\\', '/')).name, [])
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
                if now is not None and now[0] == verdict:
                    result['same'] += 1
                else:
                    put(db, *piece, verdict, 'import', {'file': Path(path).name}, history=True)
                    touched.add(piece)
                    result['added'] += 1
        from validation import refresh
        for piece in touched:
            refresh(root, db, *piece)
        db.execute('DELETE FROM workflow_cache')
    return result
