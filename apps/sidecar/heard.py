"""Sounds a person checked by ear, from every screen in one place: counts per species, the list, and putting them right.

Reads the current answers of `labels`. A correction is a new answer (source 'review'), so what it replaced stays as
history and can be brought back. "Another species is calling too" on a question card is an answer of that card's
screen with `incidental` set.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from labels import LATEST, put, ref
from reports import window_key
from species import list_species, names_for, _kana
from storage import CatalogError, connect

PAGE = 50
EXAMPLES = 5
SCREENS = ('validation', 'training', 'species_mark')  # question cards that can add another species


def _site(db, dataset_id):
    site = db.execute('SELECT s.* FROM sites s JOIN datasets d ON d.site_id=s.id WHERE d.id=?', (dataset_id,)).fetchone()
    if site is None:
        raise CatalogError('not_found')
    return site


def _taxa(db):
    return {row['id']: names_for(row) for row in db.execute(
        'SELECT * FROM species WHERE id IN (SELECT DISTINCT taxon_id FROM labels)')}


def _current(db, dataset_id=None):
    """Current answers, of one site or of all, with each one's recording."""
    return [dict(r) for r in db.execute(f'''SELECT l.*,r.path,r.seconds,r.recorded_at FROM labels l JOIN recordings r ON r.id=l.recording_id
        WHERE (? IS NULL OR l.recording_id IN (SELECT id FROM site_recordings WHERE scope_id=?)) AND {LATEST}
        ORDER BY l.id''', (dataset_id, dataset_id))]


def _counts(rows):
    found = {}
    for row in rows:
        found.setdefault(row['taxon_id'], {'present': 0, 'absent': 0, 'unknown': 0})[row['verdict']] += 1
    return found


def summary(root, dataset_id, species_id=None):
    """Per species: how many sounds were heard / not heard / left unknown at this site and at all sites, and (for the
    whole list) whether a species AI can be made here. One sound with two species counts once for each."""
    with connect(root) as db:
        _site(db, dataset_id)
        here, everywhere, taxa = _counts(_current(db, dataset_id)), _counts(_current(db)), _taxa(db)
        if species_id is not None:
            row = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
            if row is None:
                raise CatalogError('not_found')
            taxa[species_id] = names_for(row)
    empty = {'present': 0, 'absent': 0, 'unknown': 0}
    if species_id is not None:
        return {'species': taxa[species_id], 'site': here.get(species_id, empty), 'all': everywhere.get(species_id, empty)}
    ai = _ai(root, dataset_id, [taxon for taxon in here if taxa[taxon].get('rank') != 'family'])
    rows = [{'species': taxa[taxon], 'site': here.get(taxon, empty), 'all': counts,
             'ai': {'state': 'family'} if taxa[taxon].get('rank') == 'family' else ai.get(taxon)}
            for taxon, counts in everywhere.items()]
    rows.sort(key=lambda row: (-sum(row['site'].values()), -sum(row['all'].values()), row['species']['scientific_name']))
    from custom_models import MIN_CLASS_EXAMPLES, MIN_EXAMPLES
    return {'rows': rows, 'total': {'site': sum(sum(c.values()) for c in here.values()), 'all': sum(sum(c.values()) for c in everywhere.values())},
            'requirements': {'total': MIN_EXAMPLES, 'each': MIN_CLASS_EXAMPLES}}


def _ai(root, dataset_id, species_ids):
    """Where each species stands with its species AI at this site: made (and which round), can be made, or how many
    more answered sounds it needs. None when the site has nothing to make one from."""
    from custom_models import list_models, material_status
    from results_workflow import context
    try:
        ctx = context(root, dataset_id)
    except CatalogError:
        return {}
    models = {model['species']['id']: model for model in list_models(root, ctx)}
    status = material_status(root, ctx, [s for s in species_ids if s not in models])
    return {taxon: {'state': 'made', 'round': models[taxon]['round'], 'usable': models[taxon]['applicable']} if taxon in models
            else {'state': 'ready' if not status[taxon]['needed'] else 'needed', 'needed': status[taxon]['needed']}
            for taxon in species_ids}


def _public(row, taxa, zone):
    return {'id': row['id'], 'species': taxa[row['taxon_id']], 'verdict': row['verdict'], 'screen': row['source'],
            'extra': bool(row['incidental']), 'recording_id': row['recording_id'], 'start_s': row['start_s'], 'end_s': row['end_s'],
            'created_at': row['created_at'], 'local_time': window_key(row, row['start_s'], zone)[1],
            'source': {'id': row['recording_id'], 'kind': 'recording', 'path': row['path'], 'seconds': row['seconds']}}


def records(root, dataset_id, species_id=None, verdict=None, screen=None, day=None, order='new', offset=0, limit=PAGE):
    """The site's current answers, filtered and ordered, one page at a time, with what the filters can offer."""
    with connect(root) as db:
        site = _site(db, dataset_id)
        zone, taxa = ZoneInfo(site['timezone']), _taxa(db)
        rows = [_public(row, taxa, zone) for row in _current(db, dataset_id)]
    options = {'species': sorted({row['species']['id']: row['species'] for row in rows}.values(), key=lambda s: s['scientific_name']),
               'screens': sorted({row['screen'] for row in rows}), 'days': sorted({row['local_time'][:10] for row in rows})}
    rows = [row for row in rows if (species_id is None or row['species']['id'] == species_id) and (verdict is None or row['verdict'] == verdict)
            and (screen is None or row['screen'] == screen) and (day is None or row['local_time'][:10] == day)]
    if order == 'time':
        rows.sort(key=lambda row: (datetime.fromisoformat(row['local_time']), row['species']['scientific_name']))
    else:
        rows.sort(key=lambda row: (row['created_at'], row['id']), reverse=order != 'old')
    return {'total': len(rows), 'rows': rows[offset:offset + limit], 'options': options, 'site': dict(site)}


def sound(root, dataset_id, recording_id, start_s, end_s):
    """One sound: what is recorded on it now, and every answer ever given on it (newest first)."""
    with connect(root) as db:
        site = _site(db, dataset_id)
        zone, taxa = ZoneInfo(site['timezone']), _taxa(db)
        rows = [dict(r) for r in db.execute('''SELECT l.*,r.path,r.seconds,r.recorded_at FROM labels l JOIN recordings r ON r.id=l.recording_id
            WHERE l.recording_id=? AND l.start_s=? AND l.end_s=? AND l.recording_id IN (SELECT id FROM site_recordings WHERE scope_id=?)
            ORDER BY l.id DESC''', (recording_id, start_s, end_s, dataset_id))]
    if not rows:
        raise CatalogError('not_found')
    newest = {}
    for row in rows:
        newest.setdefault(row['taxon_id'], row['id'])
    history = [{**_public(row, taxa, zone), 'current': newest[row['taxon_id']] == row['id'], 'current_id': newest[row['taxon_id']]} for row in rows]
    return {'site': dict(site), 'file': rows[0]['path'], 'recording_id': recording_id, 'start_s': start_s, 'end_s': end_s,
            'local_time': history[0]['local_time'], 'source': history[0]['source'],
            'now': [row for row in history if row['current']], 'history': history}


def fix(root, body):
    """Put answers right. `ids` are current answers of one species at this site; `action`:
    'present' / 'absent' / 'unknown' (the species' answer becomes that), 'other' (it was `species_id` instead: this
    species becomes absent, that one present), 'add' (`species_id` was calling as well) or 'delete' (never answered).
    """
    from validation import refresh
    if body.action not in ('present', 'absent', 'unknown', 'other', 'add', 'delete') or not body.ids:
        raise CatalogError('invalid_input')
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        _site(db, body.dataset_id)
        rows = [dict(r) for r in db.execute(f'''SELECT l.* FROM labels l WHERE l.id IN (SELECT value FROM json_each(?)) AND {LATEST}
            AND l.recording_id IN (SELECT id FROM site_recordings WHERE scope_id=?)''', (_json(body.ids), body.dataset_id))]
        if len(rows) != len(set(body.ids)):
            # Answered again on another screen since the list was shown.
            raise CatalogError('annotation_changed')
        if len({row['taxon_id'] for row in rows}) != 1:
            raise CatalogError('invalid_input')
        taxon, other = rows[0]['taxon_id'], None
        if body.action in ('other', 'add'):
            found = db.execute('SELECT COALESCE(accepted_id,id) FROM species WHERE id=?', (body.species_id,)).fetchone()
            if found is None or found[0] == taxon:
                raise CatalogError('invalid_input')
            other = found[0]
        checks = 0
        for row in rows:
            piece = (row['recording_id'], row['start_s'], row['end_s'])
            if body.action == 'delete':
                db.execute('DELETE FROM labels WHERE taxon_id=? AND recording_id=? AND start_s=? AND end_s=?', (taxon, *piece))
            elif body.action == 'add':
                put(db, other, *piece, 'present', 'review', {'with': taxon}, incidental=True, history=True)
            else:
                verdict = 'absent' if body.action == 'other' else body.action
                if verdict != row['verdict']:
                    put(db, taxon, *piece, verdict, 'review', {'was': row['verdict'], 'screen': row['source']}, history=True)
                if other:
                    put(db, other, *piece, 'present', 'review', {'instead_of': taxon}, history=True)
            checks += refresh(root, db, taxon, *piece) + (refresh(root, db, other, *piece) if other else 0)
        db.execute('DELETE FROM workflow_cache')
    return {'changed': len(rows), 'checks': checks}


def _json(value):
    import json
    return json.dumps(list(value))


def _interval(db, body):
    record = db.execute('SELECT * FROM site_recordings WHERE scope_id=? AND id=?', (body.dataset_id, body.recording_id)).fetchone()
    if record is None or not 0 <= body.start_s < body.end_s <= record['seconds'] + 1e-6:
        raise CatalogError('invalid_interval')


def others(root, dataset_id, recording_id, start_s, end_s, of):
    """Species other than `of` recorded as heard on exactly this sound. `removable`: added on a question card as
    "another species is calling too", so the card can take it back."""
    with connect(root) as db:
        rows = db.execute(f'''SELECT l.*,s.names,s.scientific_name,s.rank FROM labels l JOIN species s ON s.id=l.taxon_id
            WHERE l.recording_id=? AND l.start_s=? AND l.end_s=? AND l.taxon_id<>? AND l.verdict='present' AND {LATEST}
            AND l.recording_id IN (SELECT id FROM site_recordings WHERE scope_id=?) ORDER BY l.id''',
            (recording_id, start_s, end_s, of, dataset_id)).fetchall()
        return [{'species': names_for({**dict(row), 'id': row['taxon_id']}), 'removable': bool(row['incidental']) and row['source'] in SCREENS}
                for row in rows]


def set_other(root, body):
    """On a question card about `of`: `species_id` is calling in this sound too (`on`), or that is taken back."""
    if body.screen not in SCREENS:
        raise CatalogError('invalid_input')
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        _interval(db, body)
        found = db.execute('SELECT COALESCE(accepted_id,id) FROM species WHERE id=?', (body.species_id,)).fetchone()
        if found is None or found[0] == body.of:
            raise CatalogError('invalid_input')
        piece = (found[0], body.recording_id, body.start_s, body.end_s)
        now = db.execute(f'''SELECT l.* FROM labels l WHERE l.taxon_id=? AND l.recording_id=? AND l.start_s=? AND l.end_s=? AND {LATEST}''', piece).fetchone()
        if body.on:
            if now is None or now['verdict'] != 'present':
                put(db, *piece, 'present', body.screen, {'other_of': body.of}, incidental=True, history=True)
        elif now is not None and now['incidental'] and now['source'] in SCREENS:
            db.execute('DELETE FROM labels WHERE id=?', (now['id'],))
        from validation import refresh
        refresh(root, db, *piece)
        db.execute('DELETE FROM workflow_cache')
    return others(root, body.dataset_id, body.recording_id, body.start_s, body.end_s, body.of)


def pick_species(root, query, dataset_id=None):
    """Species to choose for a sound: matches by name, then families ("a bird of family X") whose name matches or
    that hold one of the first matches. `rare`: not expected at this site in the recorded weeks."""
    from taxa import family_names, family_of, families
    # Birds of the Japan checklist first: a sound here is far more likely one of them than a namesake abroad.
    species = sorted(list_species(root, query), key=lambda row: not row['japan'])[:20]
    text = _kana(query.strip().casefold())
    wanted = []
    if text:
        # A family named like the query first (キツツキ -> キツツキ科), then the families of the first matches.
        named = []
        for family in families():
            names = [_kana(str(name).casefold()) for name in family_names(family).values()]
            if any(text in name for name in names):
                named.append((min((not name.startswith(text), len(name)) for name in names if text in name), family))
        wanted = [family for _, family in sorted(named)]
    wanted += [family for family in dict.fromkeys(family_of(row['scientific_name']) for row in species[:3]) if family and family not in wanted]
    with connect(root) as db:
        rows = {row['scientific_name']: names_for(row) for row in db.execute(
            "SELECT * FROM species WHERE rank='family' AND scientific_name IN (SELECT value FROM json_each(?))", (_json(wanted),))}
    region = {}
    if dataset_id is not None and species:
        from results_workflow import context, region_statuses
        try:
            region = region_statuses(root, context(root, dataset_id), [row['scientific_name'] for row in species])
        except CatalogError:
            pass
    return {'species': [{**row, 'rare': region.get(row['scientific_name']) == 'unusual'} for row in species],
            'families': [rows[family] for family in wanted if family in rows][:5]}


def search_examples(root, dataset_id, species_id):
    """Up to five heard sounds of a species to search similar sounds with, chosen by the app: first sounds with no
    other species recorded in them, spread over days and hours. `mixed`: heard sounds left out for another species."""
    with connect(root) as db:
        site = _site(db, dataset_id)
        zone, taxa = ZoneInfo(site['timezone']), _taxa(db)
        rows = _current(db, dataset_id)
    heard = [row for row in rows if row['verdict'] == 'present']
    mine = [_public(row, taxa, zone) for row in heard if row['taxon_id'] == species_id]
    for row in mine:
        row['mixed'] = sorted({taxa[o['taxon_id']]['id'] for o in heard if o['taxon_id'] != species_id and o['recording_id'] == row['recording_id']
                               and o['start_s'] < row['end_s'] and row['start_s'] < o['end_s']})
    # Overlapping windows of one call (a 3-second and a 5-second answer) are one sound; the newest stands for it.
    mine.sort(key=lambda row: row['id'], reverse=True)
    sounds = []
    for row in mine:
        if not any(o['recording_id'] == row['recording_id'] and o['start_s'] < row['end_s'] and row['start_s'] < o['end_s'] for o in sounds):
            sounds.append(row)
    chosen, days, hours = [], set(), set()
    pool = [row for row in sounds if not row['mixed']]
    while pool and len(chosen) < EXAMPLES:
        # A day not used yet first, then an hour not used yet, then the newest answer.
        best = min(pool, key=lambda row: (row['local_time'][:10] in days, row['local_time'][11:13] in hours, -row['id']))
        pool.remove(best)
        chosen.append(best)
        days.add(best['local_time'][:10])
        hours.add(best['local_time'][11:13])
    chosen.sort(key=lambda row: row['local_time'])
    mixed = [row for row in sounds if row['mixed']]
    return {'examples': chosen, 'heard': len(sounds), 'mixed': len(mixed),
            'mixed_example': {'local_time': mixed[0]['local_time'], 'species': [taxa[t] for t in mixed[0]['mixed']]} if mixed else None}
