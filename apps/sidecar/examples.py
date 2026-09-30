"""Examples of a species' sounds shown beside a question: named recordings, then sounds confirmed at this site.

AI scores are never used here. Every window the reviewer is or will be asked about
in the current check (all steps) is excluded.
"""
import json
from zoneinfo import ZoneInfo

from reports import window_key
from species import ebird_codes
from storage import CatalogError, checked_source, connect

LIMIT = 12
NAMED_S = 15  # Same width as a detection validation window.


def _example(kind, row, source_kind, start, end, label):
    return {'kind': kind, 'source': {'id': row['id'], 'kind': source_kind, 'path': row['path'], 'seconds': row['seconds']},
            'start_s': float(start), 'end_s': float(min(end, row['seconds'])), 'label': label, '_row': row}


def _question_windows(db, validation_id):
    if validation_id is None:
        return []
    if not db.execute('SELECT 1 FROM validation_sessions WHERE id=?', (validation_id,)).fetchone():
        raise CatalogError('not_found')
    return [('recording', r[0], r[1], r[1] + 15) for r in
            db.execute('SELECT recording_id,start_s FROM validation_items WHERE session_id=?', (validation_id,))]


def examples(root, species_id, dataset_id, validation_id=None):
    with connect(root) as db:
        taxon = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
        site = db.execute('SELECT s.* FROM sites s JOIN datasets d ON d.site_id=s.id WHERE d.id=?', (dataset_id,)).fetchone()
        if taxon is None or site is None:
            raise CatalogError('not_found')
        taken = _question_windows(db, validation_id)
        zone = ZoneInfo(site['timezone'])
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (dataset_id,))}
        local = lambda record, start: window_key(record, start, zone)[1]
        named, search_named, confirmed = [], [], []
        for row in db.execute('''SELECT c.id AS clip_id,c.metadata,r.* FROM quiz_clips c JOIN audio_references r ON r.id=c.reference_id
                WHERE c.species_id=? AND c.enabled=1 ORDER BY c.id''', (species_id,)):
            meta = json.loads(row['metadata'])
            credit = meta.get('credit', {})
            example = _example('named', dict(row), 'reference', 0, NAMED_S, {'origin': 'quiz', 'id': row['clip_id'],
                'recordist': credit.get('recordist', ''), 'license': credit.get('license', ''), 'url': credit.get('source_url') or credit.get('url', '')})
            if original := meta.get('original_source'):
                # A clip cut from a site recording or reference is compared where it came from.
                example['_window'] = (original['kind'], original['id'], meta['start_s'], meta['end_s'])
            named.append(example)
        for row in db.execute('''SELECT x.id AS xc_id,x.metadata,r.* FROM xc_recordings x JOIN audio_references r ON r.id=x.reference_id
                WHERE lower(json_extract(x.metadata,'$.scientific_name'))=lower(?) ORDER BY x.id''', (taxon['scientific_name'],)):
            meta = json.loads(row['metadata'])
            named.append(_example('named', dict(row), 'reference', 0, NAMED_S, {'origin': 'xeno-canto', 'id': row['xc_id'],
                'recordist': meta.get('recordist', ''), 'license': meta.get('license', ''), 'url': meta.get('url', ''),
                'country': meta.get('country', ''), 'quality': meta.get('quality', '')}))
        for run in db.execute('SELECT id,snapshot FROM search_runs WHERE species_id=? AND dataset_id IN (SELECT id FROM datasets WHERE site_id=?) ORDER BY id DESC',
                              (species_id, site['id'])):
            for index, ref in enumerate(json.loads(run['snapshot'])['references']):
                row, credit = ref['source'], ref['source'].get('attribution') or {}
                label = {'origin': 'search-reference', 'id': f"R{run['id']}-{index + 1}", 'recordist': credit.get('recordist', ''),
                         'license': credit.get('license', ''), 'url': credit.get('url', '')}
                if row['kind'] == 'recording' and row['id'] in records:
                    label['local_time'] = local(records[row['id']], ref['query']['start_s'])
                search_named.append(_example('named', row, row['kind'], ref['query']['start_s'], ref['query']['end_s'], label))
        for row in db.execute('''SELECT id,created_at,recording_id,start_s,end_s FROM presence_marks
                WHERE species_id=? AND dataset_id IN (SELECT id FROM datasets WHERE site_id=?)''', (species_id, site['id'])):
            if record := records.get(row['recording_id']):
                confirmed.append((row['created_at'], _example('confirmed', record, 'recording', row['start_s'], row['end_s'],
                                  {'origin': 'presence', 'id': row['id'], 'local_time': local(record, row['start_s'])})))
        for row in db.execute('''SELECT a.id,a.created_at,a.recording_id,a.start_s FROM window_answers a
                WHERE a.id IN (SELECT MAX(id) FROM window_answers WHERE species_id=? GROUP BY recording_id,start_s) AND a.verdict='target' ''', (species_id,)):
            if record := records.get(row['recording_id']):
                confirmed.append((row['created_at'], _example('confirmed', record, 'recording', row['start_s'], row['start_s'] + 15,
                                  {'origin': 'validation', 'id': row['id'], 'local_time': local(record, row['start_s'])})))
        in_quiz = bool(db.execute('SELECT 1 FROM quiz_clips WHERE species_id=? AND enabled=1', (species_id,)).fetchone())
    chosen = []
    confirmed.sort(key=lambda pair: (pair[0], pair[1]['label']['id']), reverse=True)
    # The interval deliberately chosen for a search is more useful than a generic
    # first-15-second example from the same source, so it also wins deduplication.
    preferred_sources={(example['source']['kind'],example['source']['id']) for example in search_named}
    named=[example for example in named if not (example['label']['origin']=='xeno-canto' and
        (example['source']['kind'],example['source']['id']) in preferred_sources)]
    quiz_named=[example for example in named if example['label']['origin']=='quiz']
    other_named=[example for example in named if example['label']['origin']!='quiz']
    for example in quiz_named + search_named + other_named + [pair[1] for pair in confirmed]:
        window = example.pop('_window', None) or (example['source']['kind'], example['source']['id'], example['start_s'], example['end_s'])
        # One rule removes questions, duplicates and sounds overlapping an earlier example.
        if any(window[:2] == other[:2] and window[2] < other[3] and other[2] < window[3] for other in taken):
            continue
        try:
            checked_source(example.pop('_row'))
        except CatalogError:
            continue
        taken.append(window)
        chosen.append(example)
        if len(chosen) == LIMIT:
            break
    return {'examples': chosen, 'ebird_code': ebird_codes().get(taxon['scientific_name']), 'in_quiz': in_quiz}


def fetch_xeno_canto(root, species_id):
    """Download the first available Japan/world, A/B condition; one failed file does not stop the others."""
    from xeno_canto import import_recording, search
    with connect(root) as db:
        taxon = db.execute('SELECT scientific_name FROM species WHERE id=?', (species_id,)).fetchone()
        if taxon is None:
            raise CatalogError('not_found')
        done = {r[0] for r in db.execute('SELECT id FROM xc_recordings WHERE reference_id IS NOT NULL')}
    found = []
    for country, quality in [('Japan', 'A'), ('Japan', 'B'), ('', 'A'), ('', 'B')]:
        found = [r for r in search(root, taxon[0], country, quality, 1)['recordings']
                 if r['scientific_name'].casefold() == taxon[0].casefold()]
        if found:
            break
    pending = [r['id'] for r in found if r['id'] not in done][:3]
    errors = []
    for xc_id in pending:
        try:
            import_recording(root, xc_id)
        except CatalogError as error:
            errors.append(str(error))
    if pending and len(errors) == len(pending):
        raise CatalogError(errors[0])
