"""Sound-map picks and portable audio export."""
import csv
import hashlib
import json
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import soundfile as sf

from storage import CatalogError, checked_source, connect


XC_FIELDS = (
    'filename', 'latitude', 'longitude', 'country', 'location name', 'elevation',
    'recording date', 'time of day', 'license', 'recordist name', 'recording device',
    'microphone', 'type', 'automatic recording', 'genus', 'species', 'subspecies',
    'sex(es)', 'life stage(s)', 'animal seen?', 'sound type(s)', 'recording method',
    'collection date', 'playback used?', 'temperature', 'background species',
    'collection specimen reference', 'remarks', 'quality')


# Context exported before and after the interval that was checked by listening. Half of a 5 s
# interval and no more, so any 5 s crop of a clip still holds at least half of the checked part.
MARGIN_S = 2.5

README = f"""Echoroo Lite の音の地図で、聞いて選んだ音です。

audio/ に WAV、metadata.csv に各音の情報、manifest.json に元の録音とファイルの確認情報があります。
各 WAV には、聞いて確かめた区間の前後 {MARGIN_S:g} 秒も入っています（録音の端では短くなります）。
聞いて確かめた部分は、metadata.csv の remarks と manifest.json に記録しています
（start_s・end_s が確かめた区間、clip_start_s・clip_end_s が WAV の範囲。どちらも元の録音の秒）。
録音者名・ライセンス・標高は空欄です。提供するときに記入してください。
座標はぼかしていません。珍しい種を含む場合は、渡す前に公開範囲を確認してください。
研究機関への受け渡し、または Xeno-canto のフォームへ転記する元資料に使えます。

These sounds were selected by listening on the Echoroo Lite sound map.

audio/ contains WAV files, metadata.csv describes them, and manifest.json records their sources and file checks.
Each WAV also holds {MARGIN_S:g} s before and after the interval that was checked by listening (less at the ends of a recording).
The checked part is recorded in the remarks column of metadata.csv and in manifest.json
(start_s and end_s: the checked interval; clip_start_s and clip_end_s: the range of the WAV; all in seconds of the source recording).
Recordist name, license, and elevation are blank. Complete them before contributing the files.
Coordinates are not obscured. Check the appropriate disclosure before sharing rare species.
The package can be given to a research institution or used as a source for the Xeno-canto form.
"""


def _site(db, dataset_id):
    row = db.execute('''SELECT s.* FROM datasets d JOIN sites s ON s.id=d.site_id
        WHERE d.id=?''', (dataset_id,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    return dict(row)


def _rows(db, dataset_id):
    _site(db, dataset_id)
    return db.execute('''SELECT p.*,r.path,r.recorded_at,r.timezone,r.seconds,r.sample_rate,
        r.size,r.mtime_ns FROM picked_sounds p JOIN site_recordings r ON r.id=p.recording_id
        WHERE r.scope_id=? ORDER BY r.recorded_at,p.start_s,p.end_s,p.recording_id''',
        (dataset_id,)).fetchall()


def _species_ids(db, rows):
    names = {row['species'] for row in rows if row['species']}
    for row in rows:
        names.update(json.loads(row['background']))
    if not names:
        return {}
    return {row['scientific_name']: row['id'] for row in db.execute(
        'SELECT id,scientific_name FROM species WHERE scientific_name IN '
        '(SELECT value FROM json_each(?))', (json.dumps(sorted(names)),))}


def _clip(row):
    """The exported range in source seconds: the checked interval plus the margin, kept inside the recording."""
    return {'clip_start_s': max(0.0, row['start_s'] - MARGIN_S),
            'clip_end_s': min(row['seconds'], row['end_s'] + MARGIN_S)}


def _shape(row, species_ids):
    background = json.loads(row['background'])
    species = row['species']
    return {'recording_id': row['recording_id'], 'start_s': row['start_s'], 'end_s': row['end_s'],
            **_clip(row), 'path': row['path'], 'recorded_at': row['recorded_at'], 'timezone': row['timezone'],
            'species': {'id': species_ids.get(species), 'scientific_name': species} if species else None,
            'background': [{'id': species_ids.get(name), 'scientific_name': name} for name in background],
            'sound_type': row['sound_type'], 'quality': row['quality'], 'others': bool(row['others']),
            'exported_at': row['exported_at'], 'updated_at': row['updated_at']}


def list_picked(root, dataset_id):
    with connect(root) as db:
        rows = _rows(db, dataset_id)
        species_ids = _species_ids(db, rows)
    return {'items': [_shape(row, species_ids) for row in rows]}


def put(root, body):
    with connect(root) as db:
        record = db.execute('SELECT * FROM site_recordings WHERE scope_id=? AND id=?',
                            (body.dataset_id, body.recording_id)).fetchone()
        if record is None:
            raise CatalogError('not_found')
        if body.end_s > record['seconds'] + 1e-9:
            raise CatalogError('invalid_interval')
        db.execute('''INSERT INTO picked_sounds
            (recording_id,start_s,end_s,species,background,sound_type,quality,others)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(recording_id,start_s,end_s) DO UPDATE SET
            species=excluded.species,background=excluded.background,sound_type=excluded.sound_type,
            quality=excluded.quality,others=excluded.others,updated_at=CURRENT_TIMESTAMP''',
            (body.recording_id, body.start_s, body.end_s, body.species,
             json.dumps(body.background, ensure_ascii=False), body.sound_type, body.quality, body.others))
        row = db.execute('''SELECT p.*,r.path,r.recorded_at,r.timezone,r.seconds FROM picked_sounds p
            JOIN recordings r ON r.id=p.recording_id
            WHERE p.recording_id=? AND p.start_s=? AND p.end_s=?''',
            (body.recording_id, body.start_s, body.end_s)).fetchone()
        species_ids = _species_ids(db, [row])
    return _shape(row, species_ids)


def delete(root, dataset_id, recording_id, start_s, end_s):
    with connect(root) as db:
        _site(db, dataset_id)
        cursor = db.execute('''DELETE FROM picked_sounds WHERE recording_id=? AND start_s=? AND end_s=?
            AND recording_id IN (SELECT id FROM site_recordings WHERE scope_id=?)''',
            (recording_id, start_s, end_s, dataset_id))
    return {'deleted': cursor.rowcount > 0}


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _local_time(row, zone):
    """When the exported clip starts, in the site's timezone."""
    instant = datetime.fromisoformat(row['recorded_at'])
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return (instant + timedelta(seconds=_clip(row)['clip_start_s'])).astimezone(zone)


def _taxon(name):
    words = name.split() if name else []
    return (words + ['', '', ''])[:3]


def _filename(row, zone, used):
    local = _local_time(row, zone)
    taxon = re.sub(r'[^A-Za-z0-9_-]+', '_', row['species'] or '').strip('_') or 'unknown'
    stem = f"{local:%Y%m%d_%H%M%S}_{taxon}"
    used[stem] = used.get(stem, 0) + 1
    suffix = '' if used[stem] == 1 else f'_{used[stem]}'
    return stem + suffix + '.wav'


def _metadata(row, filename, site, zone):
    local = _local_time(row, zone)
    genus, species, subspecies = _taxon(row['species'])
    clip = _clip(row)
    first = clip['clip_start_s']
    start, end, checked_start, checked_end = (f'{value:g}' for value in (
        first, clip['clip_end_s'], row['start_s'] - first, row['end_s'] - first))
    remarks = (f'Selected by listening on the Echoroo Lite sound map. '
               f'This clip is {start}\u2013{end} s of the source recording; checked by listening: '
               f'{checked_start}\u2013{checked_end} s of this clip; the rest is unchecked context.')
    background = json.loads(row['background'])
    if row['others'] and not background:
        remarks += ' Other species audible (not identified).'
    values = (filename, f"{site['latitude']:.4f}", f"{site['longitude']:.4f}", 'Japan', site['name'], '',
              f'{local:%Y-%m-%d}', f'{local:%H:%M}', '', '', '', '', 'single species', 'yes',
              genus, species, subspecies, '', '', 'no', row['sound_type'], 'field recording', '', 'no', '',
              ','.join(background), '', remarks, row['quality'])
    return dict(zip(XC_FIELDS, values))


def export(root, body):
    parent = Path(body.folder).expanduser().resolve()
    if not parent.is_dir():
        raise CatalogError('folder_missing')
    with connect(root) as db:
        site = _site(db, body.dataset_id)
        rows = [dict(row) for row in _rows(db, body.dataset_id)]
    if not rows:
        raise CatalogError('nothing_picked')
    zone = ZoneInfo(site['timezone'])
    created = datetime.now(timezone.utc)
    destination = parent / f"echoroo-picked-{body.dataset_id}-{created.astimezone(zone):%Y%m%d-%H%M%S}.zip"
    if destination.exists():
        raise CatalogError('report_write_failed')
    published = False
    try:
        with tempfile.TemporaryDirectory(prefix='.echoroo-picked-', dir=parent) as temporary:
            folder = Path(temporary) / 'package'
            audio_folder = folder / 'audio'
            audio_folder.mkdir(parents=True)
            metadata = []
            manifest_items = []
            used = {}
            from audio import read_interval
            for row in rows:
                filename = _filename(row, zone, used)
                checked_source(row)
                subtype = 'PCM_24' if sf.info(row['path']).subtype == 'PCM_24' else 'PCM_16'
                clip = _clip(row)
                samples, interval = read_interval(row, clip['clip_start_s'], clip['clip_end_s'],
                                                  60 + 2 * MARGIN_S, native_rate=True)
                sf.write(audio_folder / filename, samples, interval['sample_rate'], subtype=subtype)
                metadata.append(_metadata(row, filename, site, zone))
                manifest_items.append({'filename': filename, 'source_file': Path(row['path']).name,
                    'start_s': row['start_s'], 'end_s': row['end_s'], **clip, 'recorded_at': row['recorded_at'],
                    'timezone': row['timezone'], 'species': row['species'],
                    'background': json.loads(row['background']), 'sound_type': row['sound_type'],
                    'quality': row['quality'], 'others': bool(row['others'])})
            with (folder / 'metadata.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=XC_FIELDS)
                writer.writeheader(); writer.writerows(metadata)
            (folder / 'README.txt').write_text(README, encoding='utf-8')
            files = {path.relative_to(folder).as_posix(): {'sha256': _digest(path), 'bytes': path.stat().st_size}
                     for path in sorted(folder.rglob('*')) if path.is_file()}
            manifest = {'schema': 'echoroo_picked_sounds_v2', 'created_at': created.isoformat(),
                'site': {key: site[key] for key in ('name', 'timezone', 'latitude', 'longitude')},
                'items': manifest_items, 'files': files}
            (folder / 'manifest.json').write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            staged = Path(temporary) / destination.name
            with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(folder.rglob('*')):
                    if path.is_file():
                        archive.write(path, path.relative_to(folder).as_posix())
            staged.rename(destination)
            published = True
        exported_at = datetime.now(timezone.utc).isoformat()
        with connect(root) as db:
            db.executemany('''UPDATE picked_sounds SET exported_at=?
                WHERE recording_id=? AND start_s=? AND end_s=?''',
                ((exported_at, row['recording_id'], row['start_s'], row['end_s']) for row in rows))
    except CatalogError:
        if published:
            destination.unlink(missing_ok=True)
        raise
    except (OSError, RuntimeError, sqlite3.Error):
        if published:
            destination.unlink(missing_ok=True)
        raise CatalogError('report_write_failed') from None
    return {'path': str(destination), 'count': len(rows)}
