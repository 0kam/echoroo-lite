"""Share one listening queue across explicitly grouped species validations."""
import json
from types import SimpleNamespace

from precision_audit import METHOD
from storage import CatalogError, connect


def _snapshots(db, ids):
    rows = db.execute(
        f"SELECT * FROM validation_sessions WHERE id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    by_id = {row['id']: row for row in rows}
    if len(by_id) != len(ids):
        raise CatalogError('not_found')
    return [(by_id[sid], json.loads(by_id[sid]['snapshot'])) for sid in ids]


def _group_ids(db, sid):
    row = db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (sid,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    ids = json.loads(row['snapshot']).get('group_ids')
    return ids or [sid]


def start_group(root, body):
    """Start ordinary sessions, then bind only these sessions as one group."""
    import validation

    species_ids = list(dict.fromkeys(body.species_ids))
    if len(species_ids) < 2 or not getattr(body, 'annotation_session_id', None):
        raise CatalogError('validation_group_requires_shared_annotations')
    sessions = []
    for species_id in species_ids:
        member = SimpleNamespace(**{
            'dataset_id': body.dataset_id, 'species_id': species_id,
            'start_date': body.start_date, 'end_date': body.end_date,
            'annotation_session_id': body.annotation_session_id, 'run_id': None,
        })
        sessions.append(validation.start_session(root, member, _group=False))
    ids = [session['id'] for session in sessions]
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        rows = _snapshots(db, ids)
        if any(snap.get('method') != METHOD for _, snap in rows):
            raise CatalogError('validation_group_conflict')
        scopes = {(row['fingerprint'], snap['site']['id'], snap['start_date'], snap['end_date'],
                   snap['window_s']) for row, snap in rows}
        existing_groups = [snap.get('group_ids') for _, snap in rows]
        exact_resume = all(group == ids for group in existing_groups)
        if len(scopes) != 1 or (not exact_resume and any(
                snap['stage'] != 'calibration' or snap.get('thresholds')
                or snap.get('evaluation_audits') or snap.get('model_id') is not None
                or snap.get('run_id') is not None for _, snap in rows)):
            raise CatalogError('validation_group_conflict')
        for _, snap in rows:
            existing = snap.get('group_ids')
            if existing and existing != ids:
                raise CatalogError('validation_group_conflict')
            if snap.get('annotation_source', {}).get('session_id') != body.annotation_session_id:
                raise CatalogError('validation_group_requires_shared_annotations')
        if exact_resume:
            return view_group(root, ids[0])
        for row, snap in rows:
            snap['group_ids'] = ids
            db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',
                       (json.dumps(snap, allow_nan=False), row['id']))
    return view_group(root, ids[0])


def _active_ids(snap):
    audits = snap.get('evaluation_audits', {})
    if not audits:
        return None
    step = snap.get('audit_step', 'precision')
    ids = set()
    for audit in audits.values():
        ids.update(audit.get('positive_ids' if step == 'precision' else 'negative_ids', []))
    return ids


def _all_audit_ids(snap):
    return {iid for audit in snap.get('evaluation_audits', {}).values()
            for field in ('positive_ids', 'negative_ids') for iid in audit.get(field, [])}


def _rows(db, members):
    result = []
    for order, (row, snap, view) in enumerate(members):
        active = _active_ids(snap)
        phase = 'evaluation' if snap['stage'] == 'complete' else snap['stage']
        if snap.get('audit_step') in ('precision', 'recall'):
            phase = 'evaluation'
        items = __import__('validation').answered_items(db, row['id'])
        selected = _all_audit_ids(snap)
        if selected:
            items = [item for item in items if item['phase'] != 'evaluation' or item['id'] in selected]
        for item in items:
            item.update(session_id=row['id'], species=view['species'], window_s=snap['window_s'],
                        member_order=order, snapshot=snap,
                        visible=item['phase'] == phase,
                        active=item['phase'] == phase and (active is None or item['id'] in active))
            result.append(item)
    return result


def _key(item):
    return item['recording_id'], item['start_s'], item['window_s'], item['phase']


def _rank(item):
    try:
        from precision_audit import rank
        value = rank(item['snapshot'], item)
        return value if isinstance(value, bytes) else repr(value).encode()
    except (ImportError, KeyError, TypeError):
        return f"{item['position']:09d}:{item['recording_id']:09d}:{item['start_s']:020.9f}".encode()


def _reviews(items):
    return [{'session_id': item['session_id'], 'item_id': item['id'],
             'version': item['answer_id'] or 0, 'verdict': item['verdict'],
             'species': item['species']} for item in sorted(items, key=lambda x: x['member_order'])]


def _question(db, peers, positions):
    primary = min(peers, key=lambda item: (item['member_order'], item['position']))
    rec = dict(db.execute('SELECT * FROM recordings WHERE id=?', (primary['recording_id'],)).fetchone())
    ranges = json.loads(primary['ranges']) if primary['ranges'] else primary['snapshot'].get(
        'shared_ranges', {}).get(str(primary['id']), [])
    return {'id': primary['id'], 'position': positions[_key(primary)], 'verdict': primary['verdict'],
            'version': primary['answer_id'] or 0,
            'source': {'id': rec['id'], 'kind': 'recording', 'path': rec['path'], 'seconds': rec['seconds']},
            'start_s': primary['start_s'], 'end_s': primary['start_s'] + primary['window_s'],
            'ranges': ranges, 'reviews': _reviews(peers)}


def _aggregate_counts(db, items, members):
    phases = {'calibration', 'evaluation'}
    phases.update(item['phase'] for item in items)
    counts = {}
    for phase in phases:
        rows = [item for item in items if item['phase'] == phase]
        grouped = {}
        for item in rows:
            grouped.setdefault(_key(item)[:3], []).append(item)
        counts[phase] = {
            'total': len(grouped),
            'answered': sum(all(item['verdict'] is not None for item in peers)
                            for peers in grouped.values()),
        }
    # Precision and recall share the evaluation table but have fixed disjoint ID lists.
    has_audits = any(snap.get('evaluation_audits') for _, snap, _ in members)
    for step, field in [('precision', 'positive_ids'), ('recall', 'negative_ids')]:
        selected = []
        for row, snap, _ in members:
            ids = {iid for audit in snap.get('evaluation_audits', {}).values() for iid in audit.get(field, [])}
            if ids:
                selected.extend(item for item in __import__('validation').answered_items(
                    db, row['id'], 'evaluation') if item['id'] in ids)
        if selected or has_audits:
            grouped = {}
            for item in selected:
                grouped.setdefault((item['recording_id'], item['start_s']), []).append(item)
            counts[step if step == 'recall' else 'evaluation'] = {
                'total': len(grouped),
                'answered': sum(all(item['verdict'] is not None for item in peers)
                                for peers in grouped.values()),
            }
    return counts


def view_group(root, sid, ctx=None, item_id=None):
    import validation

    with connect(root) as db:
        ids = _group_ids(db, sid)
        rows = _snapshots(db, ids)
        members = [(row, snap, validation.view_session(
            root, row['id'], ctx=ctx, include_items=False, _group=False)) for row, snap in rows]
        items = _rows(db, members)
        visible_keys = set(_key(item) for item in items if item['visible'])
        ordered_keys = sorted(visible_keys, key=lambda key: min(
            (_rank(item), item['member_order'], item['position']) for item in items if _key(item) == key))
        positions = {key: position for position, key in enumerate(ordered_keys)}
        pending = [item for item in items if item['active'] and item['verdict'] is None]
        next_key = _key(min(pending, key=lambda item: (
            positions[_key(item)], item['member_order'], item['position']))) if pending else None
        if item_id is not None:
            selected = next((item for item in items if item['visible'] and item['id'] == item_id), None)
            if selected is None:
                raise CatalogError('not_found')
            key = _key(selected)
            selected_peers = [item for item in items if _key(item) == key]
            if any(item['verdict'] is None for item in selected_peers) and key != next_key:
                raise CatalogError('not_found')
        else:
            key = next_key
        peers = [item for item in items if key is not None and _key(item) == key]
        history = []
        history_keys = {_key(item) for item in items if item['visible'] and item['verdict'] is not None}
        for history_key in sorted(history_keys, key=positions.get):
            history_peers = [item for item in items if _key(item) == history_key]
            history.append(_question(db, history_peers, positions))
        primary = dict(members[0][2])
        primary['members'] = [{k: v for k, v in view.items() if k not in ('question', 'history')}
                              for _, _, view in members]
        primary['review_species'] = [view['species'] for _, _, view in members]
        primary['counts'] = _aggregate_counts(db, items, members)
        primary['question'] = _question(db, peers, positions) if peers else None
        primary['history'] = history
        stages = [snap['stage'] for _, snap, _ in members]
        primary['stage'] = 'calibration' if 'calibration' in stages else 'evaluation' if 'evaluation' in stages else 'complete'
        primary['audit_step'] = 'recall' if any(snap.get('audit_step') == 'recall' for _, snap, _ in members) else members[0][1].get('audit_step')
        lookup = {item['id']: item for item in items}
        positive_ids = [iid for _, snap, _ in members for audit in snap.get('evaluation_audits', {}).values()
                        for iid in audit.get('positive_ids', [])]
        negative_ids = [iid for _, snap, _ in members for audit in snap.get('evaluation_audits', {}).values()
                        for iid in audit.get('negative_ids', [])]
        primary['recall_started'] = any(view.get('recall_started', False) for _, _, view in members)
        primary['applicable'] = all(view['applicable'] for _, _, view in members)
        primary['can_start_recall'] = primary['applicable'] and not primary['recall_started'] and bool(negative_ids) and all(
            lookup.get(iid, {}).get('verdict') is not None for iid in positive_ids)
        return primary


def answer_group(root, sid, reviews, ranges=None):
    import validation

    reviews = [r if isinstance(r, dict) else r.model_dump() for r in reviews]
    current = view_group(root, sid, item_id=reviews[0]['item_id'] if reviews else None)
    if not current['applicable']:
        raise CatalogError('workflow_changed')
    expected = current.get('question', {}).get('reviews', [])
    submitted = {(r['session_id'], r['item_id'], r['version']) for r in reviews}
    wanted = {(r['session_id'], r['item_id'], r['version']) for r in expected}
    if submitted != wanted or len(reviews) != len(expected):
        raise CatalogError('annotation_changed')
    if any(r.get('verdict') not in ('target', 'other', 'unknown') for r in reviews):
        raise CatalogError('validation_verdict')
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        for review in reviews:
            validation._write_answer(root, db, review['session_id'], review['item_id'],
                                     review['verdict'], ranges, review['version'],
                                     allow_out_of_order=True, examples=review.get('examples_snapshot'))
    return view_group(root, sid)


def finish_group(root, sid):
    import validation

    current = view_group(root, sid)
    if not current['applicable'] or not all(member['annotation_source_current'] for member in current['members']):
        raise CatalogError('workflow_changed')
    with connect(root) as db:
        ids = _group_ids(db, sid)
        for row, snap in _snapshots(db, ids):
            if snap['stage'] == 'calibration':
                rows = validation.answered_items(db, row['id'], 'calibration')
                if not rows or any(item['verdict'] is None for item in rows):
                    raise CatalogError('validation_calibration_incomplete')
    for member_id in ids:
        validation.finish_calibration(root, member_id, _group=False)
    return view_group(root, sid)


def start_recall_group(root, sid):
    import validation

    if not view_group(root, sid)['can_start_recall']:
        raise CatalogError('validation_precision_incomplete')
    with connect(root) as db:
        ids = _group_ids(db, sid)
    for member_id in ids:
        validation.start_recall(root, member_id, _group=False)
    return view_group(root, sid)
