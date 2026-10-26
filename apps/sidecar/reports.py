"""Reproducible reports from complete saved outputs, independent of candidate display limits."""
import base64
import hashlib
from functools import lru_cache
import csv
import io
import json
import zipfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np

from species import canonical_scientific_name, names_for
from models import REGISTRY, window_s
from storage import CatalogError, checked_source, connect


def dataset_context(db, dataset_id):
    dataset = db.execute('SELECT * FROM datasets WHERE id=?', (dataset_id,)).fetchone()
    if dataset is None:
        raise CatalogError('not_found')
    site = db.execute('SELECT * FROM sites WHERE id=?', (dataset['site_id'],)).fetchone()
    records = [dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=? ORDER BY recorded_at', (dataset_id,))]
    if not records:
        raise CatalogError('no_audio')
    return dict(dataset), dict(site), records


def scope_records(records,zone,first,last):
    """Only recordings overlapping the local report period, including midnight crossings."""
    first=datetime.fromisoformat(str(first)+'T00:00:00').replace(tzinfo=zone)
    last=(datetime.fromisoformat(str(last)+'T00:00:00')+timedelta(days=1)).replace(tzinfo=zone)
    return [r for r in records if datetime.fromisoformat(r['recorded_at'])<last and
            datetime.fromisoformat(r['recorded_at'])+timedelta(seconds=r['seconds'])>first]


def window_in_scope(record,start,end,zone,first,last):
    return str(first)<=window_key(record,start,zone)[0][0] and window_key(record,end-1e-6,zone)[0][0]<=str(last)


def options(root, dataset_id):
    with connect(root) as db:
        dataset, site, records = dataset_context(db, dataset_id)
        models = [dict(r) for r in db.execute('''SELECT a.engine, json_extract(a.metadata,'$.model_sha256') AS model_sha256,
            json_extract(a.metadata,'$.model') AS name, MAX(a.id) AS latest_analysis_id, COUNT(*) AS recordings FROM analyses a
            JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=? AND a.id IN
            (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) GROUP BY a.engine,model_sha256 ORDER BY a.engine''', (dataset_id,))]
    for model in models:
        model['name'] = REGISTRY[model['engine']]['name']
    zone = ZoneInfo(site['timezone'])
    first = min(datetime.fromisoformat(r['recorded_at']).astimezone(zone).date() for r in records)
    last = max((datetime.fromisoformat(r['recorded_at']) + timedelta(seconds=r['seconds'] - 1e-6)).astimezone(zone).date() for r in records)
    return {'dataset': dataset, 'site': site, 'models': models, 'first_date': first.isoformat(), 'last_date': last.isoformat(),
            'years': sorted({datetime.fromisoformat(r['recorded_at']).astimezone(zone).year for r in records})}


def list_reports(root, dataset_id):
    with connect(root) as db:
        return [{**dict(r), 'species': json.loads(r['species']) if r['species'] else None} for r in db.execute('''
            SELECT id,kind,created_at,json_extract(snapshot,'$.species') AS species,
            json_extract(snapshot,'$.start_date') AS start_date,json_extract(snapshot,'$.end_date') AS end_date
            FROM reports WHERE dataset_id IN (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?)) ORDER BY id DESC''', (dataset_id,))]


def calendar_cells(records, zone, first, last):
    if first > last or (last - first).days >= 366:
        raise CatalogError('report_range')
    cells = {(day.isoformat(), hour): {'date': day.isoformat(), 'hour': hour, 'recorded_seconds': 0.0,
            'analysed_seconds': 0.0, 'effective_seconds': 0.0, 'eligible_windows': 0,
            'candidate_windows': 0}
             for i in range((last - first).days + 1) for day in [first + timedelta(days=i)] for hour in range(24)}
    for r in records:
        for key, seconds in effort(r, zone):
            if key in cells:
                cells[key]['recorded_seconds'] += seconds
    return cells


def effort(record, zone):
    current = datetime.fromisoformat(record['recorded_at'])
    end = current + timedelta(seconds=record['seconds'])
    # Minute boundaries preserve the repeated/skipped local hours of modern IANA timezones.
    while current < end:
        local = current.astimezone(zone)
        seconds = min(60 - local.second - local.microsecond / 1e6, (end - current).total_seconds())
        yield (local.date().isoformat(), local.hour), seconds
        current += timedelta(seconds=seconds)


def window_key(record, start, zone):
    instant = datetime.fromisoformat(record['recorded_at']) + timedelta(seconds=start)
    local = instant.astimezone(zone)
    return (local.date().isoformat(), local.hour), local.isoformat()


def finalize_cells(cells, threshold):
    output = []
    for cell in cells.values():
        n = cell['eligible_windows']
        cell['candidate_rate'] = cell['candidate_windows'] / n if n and threshold is not None else None
        cell['status'] = ('no_recording' if cell['recorded_seconds'] == 0 else 'unanalysed' if cell['analysed_seconds'] == 0
                          else 'not_evaluated' if n == 0 else 'partial' if cell['analysed_seconds'] + .001 < cell['recorded_seconds'] else 'analysed')
        if threshold is None:
            cell['candidate_windows'] = None
        output.append(cell)
    return output


@lru_cache(maxsize=128)
def analysis_metadata(raw):
    return json.loads(raw)


def candidate_list(db, analysis):
    meta = analysis_metadata(analysis['metadata'])
    if analysis['engine'] == 'perch':
        return (meta.get('candidate_filter') or {}).get('species')
    return meta.get('species')


def classification_windows(analysis, seconds, allowed):
    path = Path(analysis['scores_path'])
    stat = path.stat()
    mapping_sha = ''
    if analysis['engine'] == 'birdnet':
        from analysis import species_mapping_sha256
        mapping_sha = species_mapping_sha256()
    return cached_windows(str(path), stat.st_size, stat.st_mtime_ns, analysis['engine'], float(seconds), tuple(sorted(allowed)), mapping_sha)


def _batch_paths(root, batch, allowed, records):
    signatures, regions, model_shas = [], {}, []
    for a, region in zip(batch, allowed):
        stat = Path(a['scores_path']).stat()
        region_key = tuple(sorted(region))
        region_id = regions.setdefault(region_key, len(regions))
        signatures.append([a['id'], a['scores_path'], stat.st_size, stat.st_mtime_ns, a['engine'],
                           records[a['recording_id']]['seconds'], region_id])
        model_shas.append(analysis_metadata(a['metadata']).get('model_sha256') if 'metadata' in a.keys() else None)
    mapping_sha = ''
    if any(a['engine'] == 'birdnet' for a in batch):
        from analysis import species_mapping_sha256
        mapping_sha = species_mapping_sha256()
    key = hashlib.sha256(json.dumps(['native-windows-v3', mapping_sha, signatures, list(regions)]).encode()).hexdigest()
    species_key = hashlib.sha256(json.dumps(['species-windows-v2', key, model_shas]).encode()).hexdigest()
    folder = Path(root) / 'window-batches'; folder.mkdir(exist_ok=True)
    return folder / (key + '.npz'), folder / (species_key + '.species.npz')


def _write_species_index(path, batch, labels, codes, sizes, starts, scores, best):
    total = sum(int(windows) for _, windows in sizes)
    columns = [[np.full(total, np.nan), np.full(total, np.nan)] for _ in labels]
    analysis_ids = np.empty(total, dtype=np.int64)
    recording_ids = np.empty(total, dtype=np.int64)
    li = wi = si = 0
    for analysis, (count, windows) in zip(batch, sizes):
        count, windows = int(count), int(windows)
        end = wi + windows
        analysis_ids[wi:end] = analysis['id']
        recording_ids[wi:end] = analysis['recording_id']
        matrix = scores[si:si + count * windows].reshape(windows, count)
        best_matrix = best[si:si + count * windows].reshape(windows, count)
        for local, code in enumerate(codes[li:li + count]):
            columns[int(code)][0][wi:end] = matrix[:, local]
            columns[int(code)][1][wi:end] = best_matrix[:, local]
        li += count; wi = end; si += count * windows
    temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as stream:
            np.savez(stream, labels=labels, analysis_ids=analysis_ids, recording_ids=recording_ids,
                     starts=starts, **{f'{kind}{i}': column[k] for i, column in enumerate(columns)
                                      for k, kind in enumerate(('s', 'b'))})
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def classification_species_windows(root, db, analyses, records, species, include_best=False):
    """Read one species from the analysis-keyed index without loading every score column."""
    found = [[] for _ in range(5 if include_best else 4)]
    for offset in range(0, len(analyses), 256):
        batch = analyses[offset:offset + 256]
        allowed = [candidate_list(db, a) for a in batch]
        if len(analyses) <= 256 or any(region is None for region in allowed):
            return None
        _, path = _batch_paths(root, batch, allowed, records)
        try:
            with np.load(path, allow_pickle=False) as saved:
                matches = np.flatnonzero(saved['labels'] == species)
                if not len(matches):
                    continue
                arrays = [saved[key] for key in ('analysis_ids', 'recording_ids', 'starts')]
                scores = saved[f's{int(matches[0])}']
                if include_best:
                    arrays.append(saved[f'b{int(matches[0])}'])
        except (OSError, ValueError, KeyError):
            return None
        valid = np.isfinite(scores)
        values = (*arrays[:3], scores, *arrays[3:])
        if len(found) < len(values):
            found.append([])
        for output, values in zip(found, values):
            output.append(values[valid])
    return tuple(np.concatenate(values) if values else np.array([]) for values in found)


def classification_batches(root, db, analyses, records, include_best=True, index_species=False):
    """Read annual outputs in bounded batches instead of reopening thousands of ZIPs per species."""
    for offset in range(0, len(analyses), 256):
        batch = analyses[offset:offset + 256]
        allowed = [candidate_list(db, a) for a in batch]
        if len(analyses) <= 256 or any(region is None for region in allowed):
            for a, region in zip(batch, allowed):
                yield a, region, classification_windows(a, records[a['recording_id']]['seconds'], region) if region is not None else None
            continue
        path, species_path = _batch_paths(root, batch, allowed, records)
        fields = ('labels', 'codes', 'sizes', 'starts', 'scores', 'best')
        requested = fields if include_best else fields[:-1]
        packed = None
        if path.exists():
            try:
                with np.load(path, allow_pickle=False) as saved:
                    packed = [saved[field] for field in requested]
            except (OSError, ValueError, KeyError):
                pass
        if packed is None:
            arrays = [classification_windows(a, records[a['recording_id']]['seconds'], region) for a, region in zip(batch, allowed)]
            labels, codes = np.unique(np.concatenate([a[0] for a in arrays]), return_inverse=True)
            stored = [labels, codes, np.array([(len(a[0]), len(a[1])) for a in arrays]),
                      *[np.concatenate([a[i].ravel() for a in arrays]) for i in (1, 2, 3)]]
            temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
            try:
                with temporary.open('wb') as stream:
                    np.savez(stream, **dict(zip(fields, stored)))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            packed = stored if include_best else stored[:-1]
        labels, codes, sizes, starts, scores = packed[:5]
        if index_species:
            valid_index = False
            if species_path.exists():
                try:
                    with np.load(species_path, allow_pickle=False) as saved:
                        valid_index = all(key in saved for key in ('labels', 'analysis_ids', 'recording_ids', 'starts')) and \
                                      (not len(saved['labels']) or 'b0' in saved)
                except (OSError, ValueError):
                    pass
            if not valid_index:
                if include_best:
                    index_best = packed[5]
                else:
                    with np.load(path, allow_pickle=False) as saved:
                        index_best = saved['best']
                _write_species_index(species_path, batch, labels, codes, sizes, starts, scores, index_best)
        best = packed[5] if include_best else None
        li = wi = si = 0
        for a, region, (columns, windows) in zip(batch, allowed, sizes):
            count = columns * windows
            yield a, region, (labels[codes[li:li + columns]], starts[wi:wi + windows],
                              scores[si:si + count].reshape(windows, columns),
                              best[si:si + count].reshape(windows, columns) if include_best else None)
            li += columns; wi += windows; si += count


@lru_cache(maxsize=8)
def cached_windows(path, size, mtime, engine, seconds, allowed, mapping_sha=''):
    # The derived arrays are shared by all species, reports and validation.
    key = hashlib.sha256(json.dumps([size, mtime, engine, seconds, allowed, mapping_sha, 3]).encode()).hexdigest()[:20]
    cached = Path(path).with_name(Path(path).name + '.' + key + '.windows.npz')
    fields = ('labels', 'starts', 'scores', 'best_starts')
    if cached.exists():
        try:
            with np.load(cached, allow_pickle=False) as saved:
                return tuple(saved[field] for field in fields)
        except (OSError, ValueError, KeyError):
            pass
    result = read_classification_windows({'scores_path': path, 'engine': engine}, seconds, allowed)
    temporary = cached.with_name(cached.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as stream:
            np.savez_compressed(stream, **dict(zip(fields, result)))
        temporary.replace(cached)
    finally:
        temporary.unlink(missing_ok=True)
    return result


def read_classification_windows(analysis, seconds, allowed):
    """Return complete model-native windows, excluding any zero-padded tail."""
    from scipy.special import softmax
    native = window_s(analysis['engine'])
    slots = int(seconds // native)
    with np.load(analysis['scores_path'], allow_pickle=False) as saved:
        if analysis['engine'] == 'perch':
            labels, logits = saved['labels'], saved['logits']
            if logits.ndim != 2 or logits.shape[1] != len(labels) or not np.isfinite(logits).all():
                raise CatalogError('invalid_scores')
            included = np.isin(labels, [s.partition('_')[0] for s in allowed])
            labels = labels[included]
            scores = softmax(logits.astype(np.float64), axis=1)[:slots, included]
            present = np.ones(len(scores), dtype=bool)
        elif REGISTRY[analysis['engine']]['experimental']:
            labels, scores = saved['labels'], saved['scores']
            if (labels.ndim != 1 or scores.ndim != 2 or scores.shape[1] != len(labels)
                    or not np.isfinite(scores).all() or np.any((scores < 0) | (scores > 1))):
                raise CatalogError('invalid_scores')
            included = np.isin(labels, allowed)
            labels, scores = labels[included], scores[:slots, included]
            present = np.ones(len(scores), dtype=bool)
        else:
            raw_labels = saved['labels']
            labels = np.array(sorted(label for label in ({str(s) for s in raw_labels} & set(allowed))
                                     if canonical_scientific_name(analysis['engine'], label)))
            scores = np.full((slots, len(labels)), -np.inf)
            lookup = {label: i for i, label in enumerate(labels)}
            starts, ends, values = saved['start_s'], saved['end_s'], saved['scores']
            if not (len(starts) == len(ends) == len(values) == len(raw_labels)) or not np.isfinite(values).all():
                raise CatalogError('invalid_scores')
            present = np.zeros(slots, dtype=bool)
            for start, end, label, score in zip(starts, ends, raw_labels, values):
                index = round(float(start) / native)
                if index < 0 or index >= slots or abs(float(start) - index * native) > 1e-5 or abs(float(end - start) - native) > 1e-5:
                    continue
                present[index] = True
                if label in lookup:
                    scores[index, lookup[label]] = max(scores[index, lookup[label]], float(score))
            # Stored BirdNET scores are complete at threshold zero; missing entries are not negatives.
            present &= np.isfinite(scores).all(axis=1)
            labels = np.array([canonical_scientific_name(analysis['engine'], label) for label in labels])
    count = min(len(scores), slots)
    if not count or not len(labels):
        return labels, np.array([], dtype=int), np.empty((0, len(labels))), np.empty((0, len(labels)))
    valid = present[:count]
    starts = np.arange(count)[valid] * native
    native_scores = scores[:count][valid]
    return labels, starts, native_scores, np.broadcast_to(starts[:, None], native_scores.shape).copy()


def save_report(db, dataset_id, kind, snapshot):
    configurations, provenance = {}, []
    for aid in snapshot['analysis_ids']:
        row = db.execute('SELECT id,job_id,recording_id,metadata FROM analyses WHERE id=?', (aid,)).fetchone()
        if row['metadata'] not in configurations:
            configurations[row['metadata']] = len(configurations)
        provenance.append({'analysis_id': aid, 'job_id': row['job_id'], 'recording_id': row['recording_id'],
                           'configuration': configurations[row['metadata']]})
    snapshot['analysis_provenance'] = provenance
    snapshot['configurations'] = [json.loads(raw) for raw in configurations]
    return db.execute('INSERT INTO reports(dataset_id,kind,snapshot) VALUES(?,?,?)',
                      (dataset_id, kind, json.dumps(snapshot, ensure_ascii=False, allow_nan=False))).lastrowid


def detection(root, body, *, persist=True):
    with connect(root) as db:
        dataset, site, records = dataset_context(db, body.dataset_id)
        selected = db.execute('SELECT * FROM species WHERE id=?', (body.species_id,)).fetchone() if body.species_id else None
        if body.species_id and selected is None:
            raise CatalogError('not_found')
        wanted = selected['scientific_name'] if selected else None
        species_rows = {r['scientific_name']: names_for(r) for r in db.execute('SELECT * FROM species')}
        analyses = db.execute('''SELECT a.* FROM analyses a JOIN site_recordings r ON r.id=a.recording_id
            WHERE r.scope_id=? AND a.engine=? AND json_extract(a.metadata,'$.model_sha256')=? AND a.id IN
            (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) ORDER BY a.recording_id''',
                              (body.dataset_id, body.engine, body.model_sha256)).fetchall()
        if body.base_report_id is not None:
            base = db.execute("SELECT snapshot FROM reports WHERE id=? AND dataset_id=? AND kind='detection'", (body.base_report_id, body.dataset_id)).fetchone()
            if base is None:
                raise CatalogError('not_found')
            original = json.loads(base[0])
            if original['engine'] != body.engine or original['model_sha256'] != body.model_sha256:
                raise CatalogError('invalid_input')
            dataset, site = original['dataset'], original['site']
            original_ids = set(original['recording_ids'])
            records = [r for r in records if r['id'] in original_ids]
            analyses = [db.execute('SELECT * FROM analyses WHERE id=?', (aid,)).fetchone() for aid in original['analysis_ids']]
        if not analyses:
            raise CatalogError('no_report_analyses')
        zone = ZoneInfo(site['timezone'])
        records=scope_records(records,zone,body.start_date,body.end_date)
        record_ids={r['id'] for r in records}
        analyses=[a for a in analyses if a['recording_id'] in record_ids]
        summary_path = None
        if not persist and wanted is None and body.base_report_id is None:
            signatures = []
            for analysis in analyses:
                stat = Path(analysis['scores_path']).stat()
                signatures.append([analysis['id'], analysis_metadata(analysis['metadata']).get('model_sha256'),
                                   analysis['metadata'], analysis['scores_path'], stat.st_size, stat.st_mtime_ns])
            key = hashlib.sha256(json.dumps(['detection-summary-v1', vars(body), dataset, site, records,
                species_rows, signatures], sort_keys=True, default=str).encode()).hexdigest()
            summary_path = Path(root) / 'window-batches' / (key + '.detection.json.z')
            if summary_path.exists():
                try:
                    return json.loads(zlib.decompress(summary_path.read_bytes()))
                except (OSError, ValueError, zlib.error):
                    pass
        cells = calendar_cells(records, zone, body.start_date, body.end_date)
        indexed = {r['id']: r for r in records}
        stats, evidence, analysis_ids, missing_region = {}, [], [], []
        portions_by_record = {r['id']: [(key, duration) for key, duration in effort(r, zone) if key in cells] for r in records}
        analyses = [a for a in analyses if portions_by_record[a['recording_id']]]
        selected_windows = classification_species_windows(root, db, analyses, indexed, wanted, True) \
            if wanted is not None and not persist else None
        if selected_windows is not None:
            indexed_analysis, indexed_recording, indexed_starts, indexed_scores, indexed_best = selected_windows
            def batches():
                for analysis in analyses:
                    allowed = candidate_list(db, analysis)
                    if allowed is None:
                        yield analysis, allowed, None
                        continue
                    chosen = indexed_analysis == analysis['id']
                    yield analysis, allowed, (np.array([wanted]), indexed_starts[chosen],
                        indexed_scores[chosen, None], indexed_best[chosen, None])
            batches = batches()
        else:
            batches = classification_batches(root, db, analyses, indexed, wanted is not None, True)
        for analysis, allowed, arrays in batches:
            record = indexed[analysis['recording_id']]
            portions = portions_by_record[record['id']]
            checked_source(record)
            if allowed is None:
                missing_region.append(analysis['id'])
                continue
            labels, starts, scores, best_starts = arrays
            analysis_ids.append(analysis['id'])
            for key, duration in portions:
                cells[key]['analysed_seconds'] += duration
            selected_index = next((i for i, label in enumerate(labels) if label == wanted), None)
            width = window_s(body.engine)
            valid = []
            for i, start in enumerate(starts):
                key, local = window_key(record, float(start), zone)
                if key not in cells or not window_in_scope(record,float(start),float(start)+width,zone,body.start_date,body.end_date):
                    continue
                valid.append((i, key, local))
            indices = range(len(labels)) if wanted is None or persist else (() if selected_index is None else (selected_index,))
            if valid:
                valid_indices = [i for i, _, _ in valid]
                dates = {key[0] for _, key, _ in valid}
                candidates = scores[valid_indices] >= body.threshold if body.threshold is not None else None
                counts = np.count_nonzero(candidates, axis=0) if candidates is not None else np.zeros(len(labels), dtype=int)
                candidate_days = [set() for _ in labels]
                if candidates is not None:
                    valid_dates = np.array([key[0] for _, key, _ in valid])
                    for day in dates:
                        for j in np.flatnonzero(np.any(candidates[valid_dates == day], axis=0)):
                            candidate_days[j].add(day)
                for j in indices:
                    item = stats.setdefault(str(labels[j]), {'eligible_windows': 0, 'candidate_windows': 0, 'days': set(), 'candidate_days': set()})
                    item['eligible_windows'] += len(valid)
                    item['candidate_windows'] += int(counts[j])
                    item['days'].update(dates)
                    item['candidate_days'].update(candidate_days[j])
            if selected_index is not None:
                j = selected_index
                for i, key, local in valid:
                    cell = cells[key]
                    cell['eligible_windows'] += 1
                    cell['effective_seconds'] += width
                    if body.threshold is not None and scores[i, j] >= body.threshold:
                        cell['candidate_windows'] += 1
                        native_start = float(best_starts[i, j])
                        evidence.append({'recording_id': record['id'], 'analysis_id': analysis['id'], 'start_s': native_start,
                            'end_s': native_start + width, 'window_start_s': float(starts[i]),
                            'window_end_s': float(starts[i] + width), 'score': float(scores[i, j]), 'local_time': window_key(record, native_start, zone)[1],
                            'window_local_time': local,
                            'date': key[0], 'hour': key[1], 'status': 'ai_candidate'})
            checked_source(record)
        summaries = []
        for label, item in stats.items():
            if item['candidate_windows'] or label == wanted:
                taxon = species_rows.get(label, {'id': None, 'scientific_name': label, 'common_names': {}})
                counted = body.threshold is not None
                # A direct report request may explicitly omit a threshold.
                summaries.append({'species': taxon, 'eligible_windows': item['eligible_windows'],
                    'candidate_windows': item['candidate_windows'] if counted else None,
                    'candidate_rate': item['candidate_windows'] / item['eligible_windows'] if counted else None, 'recorded_days': len(item['days']),
                    'candidate_days': len(item['candidate_days']) if counted else None, 'quality': 'not_evaluated'})
        summaries.sort(key=lambda r: (-(r['candidate_windows'] or 0), r['species']['scientific_name']))
        snapshot = {'kind': 'detection', 'dataset': dataset, 'site': site, 'engine': body.engine, 'model_sha256': body.model_sha256,
                    'analysis_ids': analysis_ids, 'missing_region': missing_region, 'threshold': body.threshold,
                    'start_date': body.start_date.isoformat(), 'end_date': body.end_date.isoformat(),
                    'window_s': window_s(body.engine),
                    'species': names_for(selected) if selected else None, 'summaries': summaries,
                    'cells': finalize_cells(cells, body.threshold) if selected else [], 'evidence': evidence,
                    'quality': 'not_evaluated', 'complete_windows_only': True}
        snapshot['recording_ids'] = [r['id'] for r in records]
        snapshot['base_report_id'] = body.base_report_id
        if not persist:
            if summary_path is not None:
                summary_path.parent.mkdir(exist_ok=True)
                temporary = summary_path.with_suffix('.' + uuid4().hex + '.tmp')
                try:
                    temporary.write_bytes(zlib.compress(json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode()))
                    temporary.replace(summary_path)
                finally:
                    temporary.unlink(missing_ok=True)
            return snapshot
        report_id = save_report(db, body.dataset_id, 'detection', snapshot)
    return get_report(root, report_id)


def get_report(root, report_id, cell_date=None, hour=None):
    with connect(root) as db:
        row = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
        if row is None:
            raise CatalogError('not_found')
        snapshot = json.loads(row['snapshot'])
        return snapshot_view(db, snapshot, row['id'], row['created_at'], cell_date, hour)


def snapshot_view(db, snapshot, report_id=0, created_at='', cell_date=None, hour=None):
    evidence = snapshot['evidence']
    filtered = [e for e in evidence if (cell_date is None or e['date'] == cell_date) and (hour is None or e['hour'] == hour)]
    filtered.sort(key=lambda e: (-e['score'], e['recording_id'], e['start_s']))
    selected = []
    records = {}
    for e in filtered[:100]:
        rid = e['recording_id']
        if rid not in records:
            records[rid] = dict(db.execute('SELECT * FROM recordings WHERE id=?', (rid,)).fetchone())
        selected.append({**records[rid], **e})
    return {**snapshot, 'id': report_id, 'created_at': created_at, 'evidence': selected,
            'evidence_count': len(filtered), 'total_evidence': len(evidence)}

def csv_bytes(rows, fields):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        # Keep numeric negatives numeric; protect user-provided names and notes in spreadsheet apps.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v for k, v in row.items()})
    return output.getvalue().encode('utf-8-sig')


def export_report(root, report_id, folder, svg=None, png=None, audio=(), cancel=None):
    with connect(root) as db:
        row = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
        if row is None:
            raise CatalogError('not_found')
        snapshot = json.loads(row['snapshot'])
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (row['dataset_id'],))}
    destination = Path(folder).expanduser().resolve()
    if not destination.is_dir():
        raise CatalogError('folder_missing')
    common = {'report_id': report_id, 'kind': row['kind'], 'site_id': snapshot['site']['id'], 'site_name': snapshot['site']['name'],
              'dataset_id': row['dataset_id'], 'dataset_name': snapshot['dataset']['name'], 'timezone': snapshot['site']['timezone'],
              'start_date': snapshot['start_date'], 'end_date': snapshot['end_date'], 'window_s': snapshot['window_s'],
              'threshold': snapshot['threshold'], 'model_sha256': snapshot['model_sha256'],
              'engine': snapshot.get('engine', 'perch'), 'quality': 'not_evaluated'}
    def taxon(species):
        return {'species_id': species['id'], 'scientific_name': species['scientific_name'],
                'common_name_ja': species['common_names'].get('ja', ''), 'common_name_en': species['common_names'].get('en', '')} if species else {}
    summaries = [{**common, **taxon(s['species']), **{k: v for k, v in s.items() if k != 'species'}} for s in snapshot['summaries']]
    cells = [{**common, **taxon(snapshot['species']), **c} for c in snapshot['cells']]
    evidence = []
    requested = {(c.recording_id, c.start_s, c.end_s) for c in audio}
    available = {(e['recording_id'], e['start_s'], e['end_s']) for e in snapshot['evidence']}
    if not requested <= available:
        raise CatalogError('not_found')
    audio_files = {clip: f'audio/{clip[0]}_{clip[1]:.6f}_{clip[2]:.6f}.wav' for clip in requested}
    for e in snapshot['evidence']:
        r = records[e['recording_id']]
        evidence.append({**common, **taxon(snapshot['species']), **e, 'path': r['path'], 'recorded_at': r['recorded_at'],
                         'audio_file': audio_files.get((e['recording_id'], e['start_s'], e['end_s']), '')})
    path = destination / f"echoroo-{row['kind']}-{report_id}-{uuid4().hex[:8]}.zip"
    encoded_png = None
    if png:
        try:
            encoded_png = base64.b64decode(png, validate=True)
            if not encoded_png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError()
        except ValueError:
            raise CatalogError('invalid_input') from None
    with path.open('xb') as stream:
        try:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for filename, values, required in [('species.csv', summaries, ['eligible_windows','candidate_windows','candidate_rate','recorded_days','candidate_days']),
                    ('hourly.csv', cells, ['date','hour','recorded_seconds','analysed_seconds','effective_seconds','eligible_windows','candidate_windows','candidate_rate','status']),
                    ('evidence.csv', evidence, ['recording_id','analysis_id','path','recorded_at','local_time','start_s','end_s','score','status'])]:
                    if cancel and cancel.is_set():raise CatalogError('cancelled')
                    fields = list(dict.fromkeys([*common, *taxon(snapshot['species']), *required, *(key for value in values for key in value)]))
                    archive.writestr(filename, csv_bytes(values, fields))
                quality = [{**common, **taxon(s['species']), 'precision': None, 'recall': None, 'validation_id': None} for s in snapshot['summaries']]
                archive.writestr('quality.csv', csv_bytes(quality, [*common, 'species_id', 'scientific_name', 'common_name_ja', 'common_name_en', 'precision', 'recall', 'validation_id']))
                archive.writestr('report.json', json.dumps({'id': report_id, 'created_at': row['created_at'], **snapshot}, ensure_ascii=False, indent=2))
                archive.writestr('recordings.json', json.dumps([records[rid] for rid in snapshot['recording_ids']], ensure_ascii=False, indent=2))
                for (rid, start, end), filename in audio_files.items():
                    if cancel and cancel.is_set():raise CatalogError('cancelled')
                    import soundfile as sf
                    from audio import read_interval
                    samples, interval = read_interval(records[rid], start, end)
                    buffer = io.BytesIO()
                    sf.write(buffer, samples, 32000, format='WAV', subtype='FLOAT')
                    archive.writestr(filename, buffer.getvalue())
                    archive.writestr(filename + '.json', json.dumps({'recording_id': rid, **interval}))
                if svg:
                    archive.writestr('spiral.svg', svg)
                if encoded_png:
                    archive.writestr('spiral.png', encoded_png)
        except Exception:
            stream.close()
            path.unlink(missing_ok=True)
            raise
    return {'path': str(path)}
