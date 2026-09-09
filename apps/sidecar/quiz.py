"""Local practice questions. Practice answers are never converted into field-data quality."""
import hashlib
import json
import random
import shutil
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from audio import add_reference, read_interval, source
from species import names_for
from storage import CatalogError, checked_source, connect

DATA = Path(__file__).parent / 'data/quiz'


@lru_cache(maxsize=1)
def manifest():
    return json.loads((DATA / 'manifest.json').read_text(encoding='utf-8'))


def initialize_quiz(root):
    """Called under the app mutation lock when opening the quiz library, not ML startup."""
    with connect(root) as db:
        if db.execute("SELECT id FROM quiz_sets WHERE bundled_key='koe-e-lab-v1'").fetchone():
            return
        known = {r['bundled_key']: r['id'] for r in db.execute('SELECT id,bundled_key FROM quiz_clips')}
        taxa = {r['scientific_name']: r['id'] for r in db.execute('SELECT id,scientific_name FROM species')}
    ids = []
    folder = Path(root) / 'quiz/audio'
    folder.mkdir(parents=True, exist_ok=True)
    for item in manifest()['items']:
        if item['key'] in known:
            ids.append(known[item['key']]); continue
        data = DATA / item['audio_file']
        if hashlib.sha256(data.read_bytes()).hexdigest() != item['sha256']['audio_file']:
            raise CatalogError('quiz_bundle_changed')
        # Managed copies survive the temporary extraction directory of a frozen executable.
        target = folder / (item['sha256']['audio_file'] + '.mp3')
        if not target.exists():
            temporary = folder / (uuid4().hex + '.tmp')
            try:
                shutil.copyfile(data, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']['audio_file']:
            raise CatalogError('source_changed')
        reference = add_reference(root, target)
        metadata = {**item, 'source': manifest()['source'], 'start_s': 0, 'end_s': reference['seconds'], 'note': ''}
        with connect(root) as db:
            ids.append(db.execute('INSERT INTO quiz_clips(species_id,reference_id,bundled_key,metadata) VALUES(?,?,?,?)',
                (taxa[item['scientific_name']], reference['id'], item['key'], json.dumps(metadata, ensure_ascii=False))).lastrowid)
    with connect(root) as db:
        db.execute("INSERT INTO quiz_sets(name,clip_ids,bundled_key) VALUES('koe-e-lab',?,'koe-e-lab-v1')", (json.dumps(ids),))


def clip_view(db, row):
    reference = db.execute('SELECT * FROM audio_references WHERE id=?', (row['reference_id'],)).fetchone()
    taxon = names_for(db.execute('SELECT * FROM species WHERE id=?', (row['species_id'],)).fetchone())
    return {**dict(row), 'metadata': json.loads(row['metadata']), 'seconds': reference['seconds'],
            'species': taxon, 'source': {**dict(reference), 'kind': 'reference'}}


def library(root):
    with connect(root) as db:
        return {'clips': [clip_view(db, row) for row in db.execute('SELECT * FROM quiz_clips ORDER BY id')],
                'sets': [{**dict(row), 'clip_ids': json.loads(row['clip_ids'])} for row in db.execute('SELECT * FROM quiz_sets ORDER BY id')],
                'sessions': [{'id': row['id'], 'created_at': row['created_at'], 'count': len(json.loads(row['snapshot'])['questions']),
                    'answered': len(json.loads(row['answers']))} for row in db.execute('SELECT * FROM quiz_sessions ORDER BY id DESC LIMIT 50')]}


def add_clip(root, body):
    if body.end_s - body.start_s < .2 - 1e-9:
        raise CatalogError('invalid_interval')
    with connect(root) as db:
        if not db.execute('SELECT id FROM species WHERE id=?', (body.species_id,)).fetchone():
            raise CatalogError('not_found')
    original = source(root, body.source_kind, body.source_id)
    parent = None
    if body.source_kind == 'reference':
        with connect(root) as db:
            parent = db.execute('SELECT id,metadata FROM quiz_clips WHERE reference_id=?', (body.source_id,)).fetchone()
    previous = json.loads(parent['metadata']) if parent else {}
    attribution = original.get('attribution') or previous.get('xeno_canto')
    credit = {**(attribution or {}), **previous.get('credit', {})}
    credit.update({key: value for key, value in body.credit.model_dump().items() if value})
    samples, interval = read_interval(original, body.start_s, body.end_s)
    folder = Path(root) / 'quiz/audio'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid4().hex + '.wav')
    import soundfile as sf
    sf.write(path, samples, 32000, subtype='FLOAT')
    reference = add_reference(root, path)
    metadata = {'source': 'user_selection', 'label_source': 'user_selection', 'original_source': original,
        **interval, 'sha256': {'audio_file': hashlib.sha256(path.read_bytes()).hexdigest()},
        'sound_type': body.sound_type, 'note': body.note, 'credit': credit,
        'image_file': None}
    if parent:
        metadata['original_quiz_clip_id'] = parent['id']
    if attribution:
        metadata['xeno_canto'] = attribution
    with connect(root) as db:
        cid = db.execute('INSERT INTO quiz_clips(species_id,reference_id,metadata) VALUES(?,?,?)',
            (body.species_id, reference['id'], json.dumps(metadata, ensure_ascii=False))).lastrowid
        return clip_view(db, db.execute('SELECT * FROM quiz_clips WHERE id=?', (cid,)).fetchone())


def checked_clips(db, ids):
    if len(set(ids)) != len(ids):
        raise CatalogError('invalid_input')
    rows = [db.execute('SELECT * FROM quiz_clips WHERE id=? AND enabled=1', (cid,)).fetchone() for cid in ids]
    if not rows or any(row is None for row in rows):
        raise CatalogError('quiz_selection_missing')
    return rows


def save_set(root, body):
    with connect(root) as db:
        checked_clips(db, body.clip_ids)
        return {'id': db.execute('INSERT INTO quiz_sets(name,clip_ids) VALUES(?,?)',
            (body.name, json.dumps(body.clip_ids))).lastrowid}


def start_session(root, body):
    with connect(root) as db:
        rows = checked_clips(db, body.clip_ids)
        taxa = {r['species_id']: names_for(db.execute('SELECT * FROM species WHERE id=?', (r['species_id'],)).fetchone()) for r in rows}
        if len(taxa) < 2:
            raise CatalogError('quiz_min_species')
        rng = random.SystemRandom()
        questions = []
        for row in rng.sample(rows, min(body.count, len(rows))):
            choices = [row['species_id'], *rng.sample([sid for sid in taxa if sid != row['species_id']], min(3, len(taxa) - 1))]
            rng.shuffle(choices)
            clip = clip_view(db, row)
            checked_source(clip['source'])
            questions.append({'clip': clip, 'choices': [taxa[sid] for sid in choices]})
        snapshot = {'purpose': 'practice', 'clip_ids': body.clip_ids, 'requested_count': body.count,
                    'method': 'uniform_clips_without_replacement_v1', 'questions': questions}
        sid = db.execute('INSERT INTO quiz_sessions(snapshot) VALUES(?)', (json.dumps(snapshot, ensure_ascii=False),)).lastrowid
    return session_view(root, sid)


def session_row(db, session_id):
    row = db.execute('SELECT * FROM quiz_sessions WHERE id=?', (session_id,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    return row, json.loads(row['snapshot']), json.loads(row['answers'])


def session_view(root, session_id, position=None):
    with connect(root) as db:
        row, snapshot, answers = session_row(db, session_id)
    questions = snapshot['questions']
    pos = len(answers) if position is None else position
    if pos < 0 or pos > len(answers) or pos > len(questions):
        raise CatalogError('invalid_input')
    review, per_species = [], {}
    for i, answer in enumerate(answers):
        q = questions[i]
        correct = answer['species_id'] == q['clip']['species_id']
        status = 'unknown' if answer['species_id'] is None else 'correct' if correct else 'incorrect'
        review.append({'position': i, 'status': status, 'answered_at': answer['at'], 'clip': q['clip'],
            'answer': next((s for s in q['choices'] if s['id'] == answer['species_id']), None)})
        sid = q['clip']['species_id']
        item = per_species.setdefault(sid, {'species': q['clip']['species'], 'correct': 0, 'incorrect': 0, 'unknown': 0, 'total': 0})
        item[status] += 1; item['total'] += 1
    question = None
    if pos < len(questions):
        q = questions[pos]
        # Do not send the target label, file name, photo or explanation before the first answer.
        question = {'position': pos, 'seconds': q['clip']['seconds'], 'choices': q['choices'],
                    'feedback': review[pos] if pos < len(answers) else None}
    return {'id': session_id, 'purpose': 'practice', 'created_at': row['created_at'], 'count': len(questions),
            'answered': len(answers), 'complete': len(answers) == len(questions), 'question': question,
            'correct': sum(r['status'] == 'correct' for r in review), 'unknown': sum(r['status'] == 'unknown' for r in review),
            'review': review, 'per_species': list(per_species.values())}


def answer_question(root, session_id, body):
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        _, snapshot, answers = session_row(db, session_id)
        if body.position < len(answers):
            if answers[body.position]['species_id'] != body.species_id:
                raise CatalogError('quiz_answer_saved')
        else:
            if body.position != len(answers) or body.position >= len(snapshot['questions']):
                raise CatalogError('invalid_input')
            q = snapshot['questions'][body.position]
            if body.species_id is not None and body.species_id not in {s['id'] for s in q['choices']}:
                raise CatalogError('invalid_input')
            answers.append({'species_id': body.species_id, 'at': datetime.now(timezone.utc).isoformat()})
            db.execute('UPDATE quiz_sessions SET answers=? WHERE id=?', (json.dumps(answers), session_id))
    return session_view(root, session_id, body.position)


def question_audio(root, session_id, position):
    with connect(root) as db:
        _, snapshot, answers = session_row(db, session_id)
    if not 0 <= position < len(snapshot['questions']) or position > len(answers):
        raise CatalogError('invalid_input')
    return checked_source(snapshot['questions'][position]['clip']['source'])


def clip_image(root, clip_id):
    with connect(root) as db:
        row = db.execute('SELECT metadata FROM quiz_clips WHERE id=?', (clip_id,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    metadata = json.loads(row[0])
    item = next((item for item in manifest()['items'] if item['key'] == metadata.get('key')), None)
    if item is None:
        raise CatalogError('not_found')
    image = DATA / item['image_file']
    if hashlib.sha256(image.read_bytes()).hexdigest() != item['sha256']['image_file']:
        raise CatalogError('quiz_bundle_changed')
    return image
