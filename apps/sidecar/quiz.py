"""Local practice questions. Practice answers are never converted into field-data quality."""
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from audio import add_reference, read_interval, source
from species import names_for
from storage import CatalogError, checked_source, connect

RETIRED_KEYS = ('koe-e-lab-v1', 'xeno-canto-v1')


def initialize_quiz(root):
    """Retire old bundled choices under the app mutation lock, preserving history."""
    with connect(root) as db:
        for retired in RETIRED_KEYS:
            db.execute("UPDATE quiz_clips SET enabled=0 WHERE bundled_key LIKE ?", (retired + ':%',))
            db.execute('UPDATE quiz_sets SET bundled_key=NULL WHERE bundled_key=?', (retired,))


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
        'sound_type': body.sound_type, 'note': body.note, 'credit': credit}
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
        clip = q['clip']
        correct = answer['species_id'] == q['clip']['species_id']
        status = 'unknown' if answer['species_id'] is None else 'correct' if correct else 'incorrect'
        review.append({'position': i, 'status': status, 'answered_at': answer['at'], 'clip': clip,
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


def report_results(db, species_ids, *, include_sessions=False):
    """Freeze available practice evidence; it is not an unseen capability test."""
    latest={};sessions=[]
    for row in db.execute("SELECT * FROM quiz_sessions WHERE answers!='[]' ORDER BY id DESC"):
        snap=json.loads(row['snapshot']);answers=json.loads(row['answers']);counts={}
        for question,answer in zip(snap['questions'],answers):
            sid=question['clip']['species_id']
            if sid not in species_ids:continue
            item=counts.setdefault(sid,{'correct':0,'incorrect':0,'unknown':0,'total':0})
            status='unknown' if answer['species_id'] is None else 'correct' if answer['species_id']==sid else 'incorrect'
            item[status]+=1;item['total']+=1;item['answered_at']=answer['at']
        if not counts:continue
        digest=hashlib.sha256(row['snapshot'].encode()).hexdigest()
        for sid,item in counts.items():
            if sid not in latest or item['answered_at']>latest[sid]['answered_at']:
                latest[sid]={'species_id':sid,'session_id':row['id'],'purpose':snap['purpose'],
                    'set_sha256':digest,'method':snap['method'],'complete':len(answers)==len(snap['questions']),
                    'session_answer_count':len(answers),'created_at':row['created_at'],**item}
        if include_sessions:
            sessions.append({'id':row['id'],'snapshot':snap,'answers':answers,'created_at':row['created_at'],'set_sha256':digest})
    return {'per_species':list(latest.values()),'sessions':sessions,
        'interpretation':'practice_only_not_field_annotation_accuracy','independent_test_available':False}


def question_source(root, session_id, position):
    with connect(root) as db:
        _, snapshot, answers = session_row(db, session_id)
    if not 0 <= position < len(snapshot['questions']) or position > len(answers):
        raise CatalogError('invalid_input')
    clip = snapshot['questions'][position]['clip']
    key = clip.get('bundled_key') or clip.get('metadata', {}).get('key', '')
    if any(str(key).startswith(retired + ':') for retired in RETIRED_KEYS):
        raise CatalogError('quiz_audio_unavailable')
    return clip['source']


def question_audio(root, session_id, position):
    return checked_source(question_source(root, session_id, position))
