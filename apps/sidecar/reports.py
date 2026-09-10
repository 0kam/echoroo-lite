"""Reproducible reports from complete saved outputs, independent of candidate display limits."""
import base64
import hashlib
from functools import lru_cache
import csv
import io
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np

from species import names_for
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


def options(root, dataset_id):
    with connect(root) as db:
        dataset, site, records = dataset_context(db, dataset_id)
        models = [dict(r) for r in db.execute('''SELECT a.engine, json_extract(a.metadata,'$.model_sha256') AS model_sha256,
            json_extract(a.metadata,'$.model') AS name, MAX(a.id) AS latest_analysis_id, COUNT(*) AS recordings FROM analyses a
            JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=? AND a.id IN
            (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) GROUP BY a.engine,model_sha256 ORDER BY a.engine''', (dataset_id,))]
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
            'candidate_windows': 0, 'confirmed_windows': 0, 'similarity_sum': 0.0, 'max_similarity': None}
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
        similarity_sum = cell.pop('similarity_sum')
        cell['mean_similarity'] = similarity_sum / n if n and cell['max_similarity'] is not None else None
        cell['status'] = ('no_recording' if cell['recorded_seconds'] == 0 else 'unanalysed' if cell['analysed_seconds'] == 0
                          else 'not_evaluated' if n == 0 else 'partial' if cell['analysed_seconds'] + .001 < cell['recorded_seconds'] else 'analysed')
        if threshold is None:
            cell['candidate_windows'] = None
        output.append(cell)
    return output


@lru_cache(maxsize=128)
def analysis_metadata(raw):
    return json.loads(raw)


def regional_list(db, analysis):
    meta = analysis_metadata(analysis['metadata'])
    if analysis['engine'] == 'birdnet':
        return meta.get('species')
    if meta.get('geo_filter') is not None:
        return meta['geo_filter'].get('species')
    bird = db.execute("SELECT metadata FROM analyses WHERE job_id=? AND recording_id=? AND engine='birdnet'", (analysis['job_id'], analysis['recording_id'])).fetchone()
    return analysis_metadata(bird[0]).get('species') if bird else None


def classification_windows(analysis, seconds, allowed):
    path = Path(analysis['scores_path'])
    stat = path.stat()
    return cached_windows(str(path), stat.st_size, stat.st_mtime_ns, analysis['engine'], float(seconds), tuple(sorted(allowed)))


def classification_batches(root, db, analyses, records):
    """Read annual outputs in bounded batches instead of reopening thousands of ZIPs per species."""
    for offset in range(0, len(analyses), 256):
        batch = analyses[offset:offset + 256]
        allowed = [regional_list(db, a) for a in batch]
        if len(analyses) <= 256 or any(region is None for region in allowed):
            for a, region in zip(batch, allowed):
                yield a, region, classification_windows(a, records[a['recording_id']]['seconds'], region) if region is not None else None
            continue
        signatures, regions = [], {}
        for a, region in zip(batch, allowed):
            stat = Path(a['scores_path']).stat()
            region_key = tuple(sorted(region))
            region_id = regions.setdefault(region_key, len(regions))
            signatures.append([a['id'], a['scores_path'], stat.st_size, stat.st_mtime_ns, a['engine'],
                               records[a['recording_id']]['seconds'], region_id])
        key = hashlib.sha256(json.dumps([signatures, list(regions)]).encode()).hexdigest()
        folder = Path(root) / 'window-batches'; folder.mkdir(exist_ok=True)
        path = folder / (key + '.npz')
        fields = ('labels', 'codes', 'sizes', 'starts', 'scores', 'best')
        packed = None
        if path.exists():
            try:
                with np.load(path, allow_pickle=False) as saved:
                    packed = [saved[field] for field in fields]
            except (OSError, ValueError, KeyError):
                pass
        if packed is None:
            arrays = [classification_windows(a, records[a['recording_id']]['seconds'], region) for a, region in zip(batch, allowed)]
            labels, codes = np.unique(np.concatenate([a[0] for a in arrays]), return_inverse=True)
            packed = [labels, codes, np.array([(len(a[0]), len(a[1])) for a in arrays]),
                      *[np.concatenate([a[i].ravel() for a in arrays]) for i in (1, 2, 3)]]
            temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
            try:
                with temporary.open('wb') as stream:
                    np.savez(stream, **dict(zip(fields, packed)))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        labels, codes, sizes, starts, scores, best = packed
        li = wi = si = 0
        for a, region, (columns, windows) in zip(batch, allowed, sizes):
            count = columns * windows
            yield a, region, (labels[codes[li:li + columns]], starts[wi:wi + windows],
                              scores[si:si + count].reshape(windows, columns), best[si:si + count].reshape(windows, columns))
            li += columns; wi += windows; si += count


@lru_cache(maxsize=8)
def cached_windows(path, size, mtime, engine, seconds, allowed):
    # The derived arrays are shared by all species, reports and validation.
    key = hashlib.sha256(json.dumps([size, mtime, engine, seconds, allowed, 1]).encode()).hexdigest()[:20]
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
    """Return full 15-second windows and strongest native-window evidence per species."""
    from scipy.special import softmax
    native = 3 if analysis['engine'] == 'birdnet' else 5
    slots, per_common = int(seconds // native), 15 // native
    with np.load(analysis['scores_path'], allow_pickle=False) as saved:
        if analysis['engine'] == 'perch':
            labels, logits = saved['labels'], saved['logits']
            if logits.ndim != 2 or logits.shape[1] != len(labels) or not np.isfinite(logits).all():
                raise CatalogError('invalid_scores')
            included = np.isin(labels, [s.partition('_')[0] for s in allowed])
            labels = labels[included]
            scores = softmax(logits.astype(np.float64), axis=1)[:slots, included]
            present = np.ones(len(scores), dtype=bool)
        else:
            raw_labels = saved['labels']
            labels = np.array(sorted({str(s) for s in raw_labels} & set(allowed)))
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
            labels = np.array([label.partition('_')[0] for label in labels])
    count = min(len(scores) // per_common, int(seconds // 15))
    if not count or not len(labels):
        return labels, np.array([], dtype=int), np.empty((0, len(labels))), np.empty((0, len(labels)))
    valid = present[:count * per_common].reshape(count, per_common).all(axis=1)
    grouped = scores[:count * per_common].reshape(count, per_common, len(labels))[valid]
    starts = np.arange(count)[valid] * 15
    offsets = grouped.argmax(axis=1)
    maxima = grouped.max(axis=1)
    return labels, starts, maxima, starts[:, None] + offsets * native


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
        cells = calendar_cells(records, zone, body.start_date, body.end_date)
        indexed = {r['id']: r for r in records}
        stats, evidence, analysis_ids, missing_region = {}, [], [], []
        portions_by_record = {r['id']: [(key, duration) for key, duration in effort(r, zone) if key in cells] for r in records}
        analyses = [a for a in analyses if portions_by_record[a['recording_id']]]
        for analysis, allowed, arrays in classification_batches(root, db, analyses, indexed):
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
            for i, start in enumerate(starts):
                key, local = window_key(record, float(start), zone)
                if key not in cells:
                    continue
                selected_labels = enumerate(labels) if wanted is None or persist else ([] if selected_index is None else [(selected_index, wanted)])
                for j, label in selected_labels:
                    item = stats.setdefault(str(label), {'eligible_windows': 0, 'candidate_windows': 0, 'days': set(), 'candidate_days': set()})
                    item['eligible_windows'] += 1
                    item['days'].add(key[0])
                    if scores[i, j] >= body.threshold:
                        item['candidate_windows'] += 1
                        item['candidate_days'].add(key[0])
                if selected_index is None:
                    continue
                j = selected_index
                cell = cells[key]
                cell['eligible_windows'] += 1
                cell['effective_seconds'] += 15
                if scores[i, j] >= body.threshold:
                    cell['candidate_windows'] += 1
                    native_start = float(best_starts[i, j])
                    evidence.append({'recording_id': record['id'], 'analysis_id': analysis['id'], 'start_s': native_start,
                        'end_s': native_start + (3 if body.engine == 'birdnet' else 5), 'window_start_s': float(start),
                        'window_end_s': float(start + 15), 'score': float(scores[i, j]), 'local_time': window_key(record, native_start, zone)[1],
                        'window_local_time': local,
                        'date': key[0], 'hour': key[1], 'status': 'ai_candidate'})
            checked_source(record)
        summaries = []
        for label, item in stats.items():
            if item['candidate_windows'] or label == wanted:
                taxon = species_rows.get(label, {'id': None, 'scientific_name': label, 'common_names': {}})
                summaries.append({'species': taxon, 'eligible_windows': item['eligible_windows'], 'candidate_windows': item['candidate_windows'],
                    'candidate_rate': item['candidate_windows'] / item['eligible_windows'], 'recorded_days': len(item['days']),
                    'candidate_days': len(item['candidate_days']), 'quality': 'not_evaluated'})
        summaries.sort(key=lambda r: (-r['candidate_windows'], r['species']['scientific_name']))
        snapshot = {'kind': 'detection', 'dataset': dataset, 'site': site, 'engine': body.engine, 'model_sha256': body.model_sha256,
                    'analysis_ids': analysis_ids, 'missing_region': missing_region, 'threshold': body.threshold,
                    'start_date': body.start_date.isoformat(), 'end_date': body.end_date.isoformat(), 'window_s': 15,
                    'species': names_for(selected) if selected else None, 'summaries': summaries,
                    'cells': finalize_cells(cells, body.threshold) if selected else [], 'evidence': evidence,
                    'quality': 'not_evaluated', 'complete_windows_only': True}
        snapshot['recording_ids'] = [r['id'] for r in records]
        snapshot['base_report_id'] = body.base_report_id
        if not persist:
            return snapshot
        report_id = save_report(db, body.dataset_id, 'detection', snapshot)
    return get_report(root, report_id)


def search_report(root, body, *, persist=True):
    with connect(root) as db:
        run = db.execute('SELECT * FROM search_runs WHERE id=?', (body.run_id,)).fetchone()
        if run is None:
            raise CatalogError('not_found')
        original = json.loads(run['snapshot'])
        dataset, site, records = dataset_context(db, run['dataset_id'])
        # Search uses the original site settings; later imports do not add searched intervals.
        site = original['site']
        zone = ZoneInfo(site['timezone'])
        cells = calendar_cells(records, zone, body.start_date, body.end_date)
        indexed = {r['id']: r for r in records}
        cutoff = db.execute('SELECT COALESCE(MAX(id),0) FROM search_judgements').fetchone()[0]
        analysis_ids = original.get('analysis_ids', [a['analysis_id'] for a in original.get('analyses', [])])
        recorded_ids = set()
        for aid in analysis_ids:
            row = db.execute('SELECT recording_id FROM analyses WHERE id=?', (aid,)).fetchone()
            recorded_ids.add(row[0])
        for rid in recorded_ids:
            checked_source(indexed[rid])
            for key, duration in effort(indexed[rid], zone):
                if key in cells:
                    cells[key]['analysed_seconds'] += duration
        hits = db.execute('''SELECT h.*,s.start_s,s.end_s,s.analysis_id,a.recording_id,j.verdict,j.id AS judgement_id,j.created_at AS judged_at
            FROM search_hits h JOIN segments s ON s.id=h.segment_id JOIN analyses a ON a.id=s.analysis_id
            LEFT JOIN search_judgements j ON j.id=(SELECT MAX(id) FROM search_judgements
                WHERE species_id=? AND segment_id=h.segment_id AND id<=?) WHERE h.run_id=? ORDER BY h.segment_id''',
                          (run['species_id'], cutoff, body.run_id))
        evidence, eligible_days, candidate_days = [], set(), set()
        counts = {'target': 0, 'other': 0, 'unknown': 0, 'unreviewed': 0}
        labelled_scores = {'target': [], 'other': []}
        distribution = [{'low': -1 + i / 10, 'high': -1 + (i + 1) / 10, 'count': 0, 'target': 0, 'other': 0, 'unknown': 0} for i in range(20)]
        for hit in hits:
            record = indexed[hit['recording_id']]
            key, local = window_key(record, hit['start_s'], zone)
            if key not in cells:
                continue
            cell = cells[key]
            cell['eligible_windows'] += 1
            cell['effective_seconds'] += hit['end_s'] - hit['start_s']
            cell['similarity_sum'] += hit['similarity']
            cell['max_similarity'] = max(cell['max_similarity'] if cell['max_similarity'] is not None else -1, hit['similarity'])
            candidate = body.threshold is not None and hit['similarity'] >= body.threshold
            cell['candidate_windows'] += int(candidate)
            cell['confirmed_windows'] += int(hit['verdict'] == 'target')
            eligible_days.add(key[0])
            if candidate:
                candidate_days.add(key[0])
            verdict = hit['verdict'] or 'unreviewed'
            counts[verdict] += 1
            if verdict in labelled_scores:
                labelled_scores[verdict].append(hit['similarity'])
            band = distribution[min(19, max(0, int((hit['similarity'] + 1) * 10)))]
            band['count'] += 1
            if verdict != 'unreviewed':
                band[verdict] += 1
            # Keep every candidate and every reviewed interval, including positives below threshold.
            if candidate or hit['verdict'] or body.threshold is None:
                evidence.append({**dict(hit), 'score': hit['similarity'], 'local_time': local,
                    'date': key[0], 'hour': key[1], 'status': verdict, 'threshold_exceeded': candidate})
        n = sum(c['eligible_windows'] for c in cells.values())
        positives = sum(c['candidate_windows'] for c in cells.values()) if body.threshold is not None else None
        species = names_for(db.execute('SELECT * FROM species WHERE id=?', (run['species_id'],)).fetchone())
        suggestion = None
        if all(len(values) >= 3 for values in labelled_scores.values()):
            positive, negative = (float(np.median(labelled_scores[key])) for key in ('target', 'other'))
            if positive > negative:
                suggestion = {'threshold': (positive + negative) / 2, 'positive_median': positive, 'negative_median': negative,
                              'positive_count': len(labelled_scores['target']), 'negative_count': len(labelled_scores['other']),
                              'method': 'midpoint_of_labelled_class_medians', 'purpose': 'exploration_only'}
        snapshot = {'kind': 'search', 'dataset': dataset, 'site': site, 'run_id': body.run_id, 'search_snapshot': original,
                    'analysis_ids': analysis_ids, 'model_sha256': original['model_sha256'], 'threshold': body.threshold,
                    'start_date': body.start_date.isoformat(), 'end_date': body.end_date.isoformat(), 'window_s': 5,
                    'judgement_cutoff': cutoff, 'species': species, 'quality': 'not_evaluated', 'judgements': counts,
                    'distribution': distribution, 'threshold_suggestion': suggestion,
                    'cells': finalize_cells(cells, body.threshold), 'evidence': evidence,
                    'summaries': [{'species': species, 'eligible_windows': n, 'candidate_windows': positives,
                        'candidate_rate': positives / n if n and positives is not None else None, 'recorded_days': len(eligible_days),
                        'candidate_days': len(candidate_days) if body.threshold is not None else None,
                        'confirmed_windows': counts['target'], 'quality': 'not_evaluated'}]}
        snapshot['recording_ids'] = [r['id'] for r in records]
        if not persist:
            return snapshot
        report_id = save_report(db, run['dataset_id'], 'search', snapshot)
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
    filtered.sort(key=lambda e: (e['status'] != 'target', -e['score'], e['recording_id'], e['start_s']))
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


def export_report(root, report_id, folder, svg=None, png=None, audio=()):
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
              'threshold': snapshot['threshold'], 'model_sha256': snapshot['model_sha256'], 'search_run_id': snapshot.get('run_id'),
              'engine': snapshot.get('engine', 'perch'), 'quality': 'not_evaluated'}
    if snapshot.get('search_snapshot', {}).get('date_scope'):
        common.update({f'search_{key}': snapshot['search_snapshot'].get(key) for key in ('start_date', 'end_date', 'date_scope')})
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
                for filename, values, required in [('species.csv', summaries, ['eligible_windows','candidate_windows','candidate_rate','recorded_days','candidate_days','confirmed_windows']),
                    ('hourly.csv', cells, ['date','hour','recorded_seconds','analysed_seconds','effective_seconds','eligible_windows','candidate_windows','candidate_rate','max_similarity','mean_similarity','confirmed_windows','status']),
                    ('evidence.csv', evidence, ['recording_id','analysis_id','segment_id','path','recorded_at','local_time','start_s','end_s','score','status','judgement_id','judged_at'])]:
                    fields = list(dict.fromkeys([*common, *taxon(snapshot['species']), *required, *(key for value in values for key in value)]))
                    archive.writestr(filename, csv_bytes(values, fields))
                quality = [{**common, **taxon(s['species']), 'precision': None, 'recall': None, 'validation_id': None} for s in snapshot['summaries']]
                archive.writestr('quality.csv', csv_bytes(quality, [*common, 'species_id', 'scientific_name', 'common_name_ja', 'common_name_en', 'precision', 'recall', 'validation_id']))
                archive.writestr('report.json', json.dumps({'id': report_id, 'created_at': row['created_at'], **snapshot}, ensure_ascii=False, indent=2))
                archive.writestr('recordings.json', json.dumps([records[rid] for rid in snapshot['recording_ids']], ensure_ascii=False, indent=2))
                for (rid, start, end), filename in audio_files.items():
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
