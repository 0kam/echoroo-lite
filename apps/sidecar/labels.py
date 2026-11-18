"""One label store: a taxon's voice was present / absent / unknown in an interval of a recording.

Every screen writes the same shape (`source` names the screen, `source_ref` its context). For one taxon and exact
interval the newest row is the current answer; older rows stay as history. Readers translate to the older
target / other / unknown words where the screens still use them.
"""
import json

SOURCES = ('species_mark', 'validation', 'training', 'search', 'sound_map', 'rollup_fix', 'import')
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
    return db.execute('''INSERT INTO labels(recording_id,start_s,end_s,taxon_id,verdict,source,source_ref,incidental,created_at)
        VALUES(?,?,?,?,?,?,?,?,COALESCE(?,strftime('%Y-%m-%d %H:%M:%f','now')))''',
        (recording_id, start_s, end_s, taxon_id, verdict, source, json.dumps(ref or {}, ensure_ascii=False),
         int(bool(incidental)), created)).lastrowid


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


def ref(row):
    return json.loads(row['source_ref'] or '{}')
