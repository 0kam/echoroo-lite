"""One label store: a taxon's voice was present / absent / unknown in an interval of a recording.

Every screen writes the same shape (`source` names the screen, `source_ref` its context). For one taxon and exact
interval the newest row is the current answer; older rows stay as history. Readers translate to the older
target / other / unknown words where the screens still use them.

"No bird sound" is an answer about the sound, not a species: 'present' on the one taxon of rank 'none' (`no_bird`).
A row whose `source_ref` has `auto` was not given by the person but follows from another answer (the species a swap
moved away from, a species heard where "no bird sound" is recorded, "no bird sound" taken back by a species heard):
it is a current answer for material and history, and the list of checked sounds leaves it out.

A sound is complete when nothing calls in it but the species heard there: every other species counts as "not heard"
in it (`complete`). "No bird sound" is a complete sound with no species; with species heard the same taxon carries
the mark instead, as 'absent' with `complete` in its `source_ref` (`set_complete`), so the two never hold together.
"""
import json

# 'review': an answer put right on the "sounds you checked by ear" screen; newer than what it corrects, so it is current.
SOURCES = ('species_mark', 'validation', 'training', 'search', 'sound_map', 'rollup_fix', 'import', 'review')
VERDICTS = ('present', 'absent', 'unknown')
FROM_ANSWER = {'target': 'present', 'other': 'absent', 'unknown': 'unknown'}
TO_ANSWER = {value: key for key, value in FROM_ANSWER.items()}

SCHEMA = f'''
    CREATE TABLE IF NOT EXISTS labels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recording_id INTEGER NOT NULL REFERENCES recordings,
        start_s REAL NOT NULL, end_s REAL NOT NULL CHECK(end_s > start_s),
        taxon_id TEXT NOT NULL REFERENCES species,
        verdict TEXT NOT NULL CHECK(verdict IN {VERDICTS}),
        source TEXT NOT NULL CHECK(source IN {SOURCES}),
        source_ref TEXT NOT NULL DEFAULT '{{}}',
        incidental INTEGER NOT NULL DEFAULT 0 CHECK(incidental IN (0,1)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')));
    CREATE INDEX IF NOT EXISTS label_key ON labels(taxon_id,recording_id,start_s,end_s,id DESC);
    CREATE INDEX IF NOT EXISTS label_recording ON labels(recording_id);
'''

# The current answer of each taxon and exact interval: its newest row.
LATEST = '''l.id=(SELECT MAX(n.id) FROM labels n WHERE n.taxon_id=l.taxon_id AND n.recording_id=l.recording_id
    AND n.start_s=l.start_s AND n.end_s=l.end_s)'''
# The same, for each screen separately.
LATEST_PER_SOURCE = LATEST[:-1] + ' AND n.source=l.source)'


def put(db, taxon_id, recording_id, start_s, end_s, verdict, source, ref=None, incidental=False, history=False):
    """Add an answer and return its id. Without `history` this source's earlier answer on the same interval is
    replaced, keeping its first time so lists ordered by it do not jump; with it (checks) the earlier answers stay."""
    if verdict not in VERDICTS or source not in SOURCES:
        raise ValueError(verdict if verdict not in VERDICTS else source)
    created = None
    if not history:
        old = db.execute('''SELECT MIN(created_at) FROM labels WHERE taxon_id=? AND recording_id=? AND start_s=? AND end_s=?
            AND source=?''', (taxon_id, recording_id, start_s, end_s, source)).fetchone()[0]
        created = old
        remove(db, taxon_id, recording_id, start_s, end_s, source)
    if verdict == 'present':
        # A voice was heard here: a current "no bird sound" on the same interval no longer holds.
        db.execute(f'''INSERT INTO labels(recording_id,start_s,end_s,taxon_id,verdict,source,source_ref)
            SELECT l.recording_id,l.start_s,l.end_s,l.taxon_id,'absent',?,? FROM labels l
            WHERE l.recording_id=? AND l.start_s=? AND l.end_s=? AND l.taxon_id<>? AND l.verdict='present' AND {LATEST}
              AND l.taxon_id=(SELECT id FROM species WHERE rank='none')''',
            (source, json.dumps({'auto': taxon_id}), recording_id, start_s, end_s, taxon_id))
    return db.execute('''INSERT INTO labels(recording_id,start_s,end_s,taxon_id,verdict,source,source_ref,incidental,created_at)
        VALUES(?,?,?,?,?,?,?,?,COALESCE(?,strftime('%Y-%m-%d %H:%M:%f','now')))''',
        (recording_id, start_s, end_s, taxon_id, verdict, source, json.dumps(ref or {}, ensure_ascii=False),
         int(bool(incidental)), created)).lastrowid


def no_bird(db):
    """The taxon id of "no bird sound"."""
    return db.execute("SELECT id FROM species WHERE rank='none'").fetchone()[0]


def put_no_bird(db, recording_id, start_s, end_s, source, ref=None):
    """Record "no bird sound" on an interval and return the species that were "heard" there: each becomes "not
    heard" as a correction (source 'review'), so its earlier answer stays as history."""
    taxon = no_bird(db)
    heard = [dict(r) for r in db.execute(f'''SELECT l.* FROM labels l WHERE l.recording_id=? AND l.start_s=? AND l.end_s=?
        AND l.taxon_id<>? AND l.verdict='present' AND {LATEST}''', (recording_id, start_s, end_s, taxon))]
    for row in heard:
        put(db, row['taxon_id'], recording_id, start_s, end_s, 'absent', 'review', {'was': 'present', 'screen': row['source'], 'auto': taxon}, history=True)
    put(db, taxon, recording_id, start_s, end_s, 'present', source, ref, history=True)
    return [row['taxon_id'] for row in heard]


def complete(db, scope_id):
    """The site's complete sounds: the current rows of "no bird sound" that say so (see the module text)."""
    return [row for row in current(db, scope_id, no_bird(db)) if row['verdict'] == 'present' or ref(row).get('complete')]


def set_complete(db, recording_id, start_s, end_s, source, on=True):
    """Say that nothing calls in the sound but the species heard in it, or take that back. With no species heard
    it is "no bird sound" itself."""
    taxon, sound = no_bird(db), (recording_id, start_s, end_s)
    now = db.execute(f'SELECT l.* FROM labels l WHERE l.taxon_id=? AND l.recording_id=? AND l.start_s=? AND l.end_s=? AND {LATEST}',
                     (taxon, *sound)).fetchone()
    none = now is not None and now['verdict'] == 'present'
    marked = now is not None and not none and bool(ref(now).get('complete'))
    heard = db.execute(f"""SELECT COUNT(*) FROM labels l WHERE l.recording_id=? AND l.start_s=? AND l.end_s=? AND l.taxon_id<>?
        AND l.verdict='present' AND {LATEST}""", (*sound, taxon)).fetchone()[0]
    if on and not heard:
        if not none:
            put_no_bird(db, *sound, source, {'answer': True})
    elif on:
        if not marked:
            put(db, taxon, *sound, 'absent', source, {'auto': True, 'complete': True}, history=True)
    elif none or marked:
        put(db, taxon, *sound, 'absent', source, {'auto': True}, history=True)


def remove(db, taxon_id, recording_id, start_s, end_s, source):
    """Take back this source's answers on one interval; another screen's answer there, if any, becomes current."""
    return db.execute('DELETE FROM labels WHERE taxon_id=? AND recording_id=? AND start_s=? AND end_s=? AND source=?',
                      (taxon_id, recording_id, start_s, end_s, source)).rowcount


def current(db, scope_id, taxon_id=None, verdict=None, per_source=False):
    """Current answers on the recordings of the site of dataset `scope_id`, oldest first; with `per_source` the
    current answer of each screen."""
    return [dict(r) for r in db.execute(f'''SELECT l.* FROM labels l
        WHERE l.recording_id IN (SELECT id FROM site_recordings WHERE scope_id=?) AND (? IS NULL OR l.taxon_id=?)
          AND {LATEST_PER_SOURCE if per_source else LATEST} AND (? IS NULL OR l.verdict=?) ORDER BY l.id''', (scope_id, taxon_id, taxon_id, verdict, verdict))]


def on_pieces(rows, pieces, first=None):
    """Answers laid on 5-second pieces: {piece key: row}. `pieces` maps recording id -> sorted (start, end, key).

    "Absent" is said of the whole interval, so it holds for every piece inside it. "Present" means somewhere in the
    interval, so it is used only when the interval sits inside one piece. "Unknown" is never used. A later answer on
    the same piece wins, except that answers from the `first` source win over all others.
    """
    found = {}
    for row in sorted(rows, key=lambda r: (r['source'] == first, r['id'])):
        if row['verdict'] == 'unknown':
            continue
        for start, end, key in pieces.get(row['recording_id'], ()):
            if row['verdict'] == 'absent' and row['start_s'] - 1e-6 <= start and end <= row['end_s'] + 1e-6 \
                    or row['verdict'] == 'present' and start - 1e-6 <= row['start_s'] and row['end_s'] <= end + 1e-6:
                found[key] = row
    return found


def upgrade(db):
    """Rebuild a labels table made before a source was added, so its CHECK accepts every source."""
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='labels'").fetchone()
    if sql is None or all(f"'{source}'" in sql[0] for source in SOURCES):
        return
    db.execute('DROP INDEX IF EXISTS label_key')
    db.execute('DROP INDEX IF EXISTS label_recording')
    db.execute('ALTER TABLE labels RENAME TO labels_old')
    db.executescript(SCHEMA)
    db.execute('INSERT INTO labels SELECT * FROM labels_old')
    db.execute('DROP TABLE labels_old')


def corrected(rows):
    """`rows` without the answers a later correction (source 'review') on the same interval replaced."""
    fixed = {}
    for row in rows:
        if row['source'] == 'review':
            key = (row['taxon_id'], row['recording_id'], row['start_s'], row['end_s'])
            fixed[key] = max(fixed.get(key, 0), row['id'])
    return [row for row in rows if row['id'] >= fixed.get((row['taxon_id'], row['recording_id'], row['start_s'], row['end_s']), 0)]


def ref(row):
    return json.loads(row['source_ref'] or '{}')
