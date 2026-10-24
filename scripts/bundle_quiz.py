"""Select licensed Xeno-Canto quiz clips, then build the offline bundle manifest."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / 'apps/sidecar'
DATA = SIDECAR / 'data/quiz'
MANIFEST = DATA / 'manifest.json'
SELECTION = DATA / 'selection.json'
if str(SIDECAR) not in sys.path:
    sys.path.insert(0, str(SIDECAR))

from analysis import load_perch, perch_label_index, perch_labels  # noqa: E402
from audio import resample  # noqa: E402
from storage import CatalogError  # noqa: E402
from xeno_canto import download, search_page  # noqa: E402

SEARCH_ORDER = [('Japan', 'A'), ('Japan', 'B'), ('', 'A'), ('', 'B')]
CUT_NOTE = '元の録音から区間を切り出しています。 / A section was cut from the original recording.'


def accepted_license(value):
    value = str(value or '').strip()
    parsed = urlparse('https:' + value if value.startswith('//') else value)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('creativecommons.org', 'www.creativecommons.org'):
        return False
    parts = [part.casefold() for part in parsed.path.split('/') if part]
    if len(parts) < 3 or not re.fullmatch(r'\d+(?:\.\d+)*', parts[2]):
        return False
    return parts[:2] in (['licenses', 'by'], ['licenses', 'by-sa'], ['publicdomain', 'zero'])


def sound_type_matches(recording, preferred):
    return preferred.casefold() in re.split(r'[^a-z]+', str(recording.get('type', '')).casefold())


def choose_recording(scientific_name, sound_type, fetch_stage):
    for country, quality in SEARCH_ORDER:
        candidates = [recording for recording in fetch_stage(country, quality)
                      if recording.get('scientific_name', '').casefold() == scientific_name.casefold()
                      and accepted_license(recording.get('license'))]
        if candidates:
            return next((recording for recording in candidates if sound_type_matches(recording, sound_type)), candidates[0])
    return None


def api_stage(key_path, scientific_name, country, quality):
    first = search_page(key_path, scientific_name, country, quality, 1)
    recordings = list(first['recordings'])
    for page in range(2, first['total_pages'] + 1):
        recordings.extend(search_page(key_path, scientific_name, country, quality, page)['recordings'])
    return recordings


def best_window(path, session, labels, scientific_name):
    try:
        samples, rate = sf.read(path, dtype='float32', always_2d=True)
        duration = sf.info(path).duration
    except (OSError, RuntimeError):
        raise CatalogError('unreadable_audio') from None
    if not len(samples) or not np.isfinite(samples).all():
        raise CatalogError('unreadable_audio')
    audio = resample(samples.mean(axis=1), rate)
    label_index = perch_label_index(labels, scientific_name)
    best = None
    for index, offset in enumerate(range(0, len(audio), 160000)):
        chunk = audio[offset:offset + 160000]
        window = np.pad(chunk, (0, 160000 - len(chunk)))
        scores = session.run(['label'], {'inputs': window[None].astype(np.float32)})[0]
        if scores.shape != (1, len(labels)) or not np.isfinite(scores).all():
            raise CatalogError('invalid_scores')
        candidate = float(scores[0, label_index]), index
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        raise CatalogError('unreadable_audio')
    center = best[1] * 5 + 2.5
    start = min(max(0.0, center - 4), max(0.0, duration - 8))
    return start, min(duration, start + 8), best[0]


def cut_mp3(ffmpeg, source, output, start, end):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp.mp3')
    try:
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                        '-ss', f'{start:.6f}', '-t', f'{end - start:.6f}', '-vn', '-ac', '1',
                        '-codec:a', 'libmp3lame', '-b:a', '128k', str(temporary)], check=True)
        if sf.info(temporary).frames <= 0:
            raise CatalogError('unreadable_audio')
        temporary.replace(output)
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        raise CatalogError('quiz_cut_failed') from None
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def source_items(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    items = data.get('items', [])
    if len(items) != 26 or len({item['scientific_name'] for item in items}) != 26:
        raise CatalogError('quiz_source_manifest_invalid')
    return items


def select(args):
    items = source_items(args.manifest)
    ffmpeg = shutil.which(args.ffmpeg)
    if not ffmpeg:
        raise CatalogError('ffmpeg_missing')
    session, model_sha256 = load_perch(args.models)
    labels = perch_labels(args.models)
    selection = {'version': 1, 'source': 'xeno-canto', 'model_sha256': model_sha256,
                 'items': [], 'missing': []}
    if args.selection.exists():  # Resume after a network failure; finished species are kept.
        saved = json.loads(args.selection.read_text(encoding='utf-8'))
        if saved.get('model_sha256') == model_sha256:
            selection = saved
    done = {entry['stem'] for entry in selection['items'] + selection['missing']}
    with tempfile.TemporaryDirectory(prefix='echoroo-quiz-') as folder:
        source = Path(folder) / 'recording.audio'
        for item in items:
            stem = item['key'].split(':', 1)[-1]
            if stem in done:
                continue
            scientific_name, sound_type = item['scientific_name'], item['sound_type']
            recording = choose_recording(scientific_name, sound_type,
                lambda country, quality: api_stage(args.key, scientific_name, country, quality))
            if recording is None:
                selection['missing'].append({'stem': stem, 'scientific_name': scientific_name})
                write_json(args.selection, selection)
                print(f'No acceptable recording: {scientific_name}', file=sys.stderr)
                continue
            for attempt in range(3):
                try:
                    with source.open('wb') as output:
                        download(f"https://xeno-canto.org/{recording['id']}/download", output, 50 * 1024 * 1024)
                    break
                except CatalogError:
                    if attempt == 2:
                        raise
                    time.sleep(10 * (attempt + 1))
            start, end, score = best_window(source, session, labels, scientific_name)
            relative = f'mp3/{stem}.mp3'
            cut_mp3(ffmpeg, source, DATA / relative, start, end)
            selection['items'].append({'stem': stem, 'scientific_name': scientific_name,
                'sound_type': sound_type, 'audio_file': relative, 'xc_id': recording['id'],
                'recordist': recording.get('recordist', ''), 'country': recording.get('country', ''),
                'quality': recording.get('quality', ''), 'type': recording.get('type', ''),
                'license_url': recording['license'], 'source_url': recording['url'],
                'start_s': start, 'end_s': end, 'perch_score': score})
            write_json(args.selection, selection)
            source.unlink(missing_ok=True)
            print(f"Selected {scientific_name}: XC{recording['id']} {start:.3f}-{end:.3f}s")
    print(f"Selected {len(selection['items'])}; missing {len(selection['missing'])}: {args.selection}")


def build_manifest(selection, data=DATA):
    items = []
    for record in selection['items']:
        audio = data / record['audio_file']
        try:
            digest = hashlib.sha256(audio.read_bytes()).hexdigest()
        except OSError:
            raise CatalogError('quiz_audio_missing') from None
        items.append({'key': f"xeno-canto-v1:{record['stem']}",
            'scientific_name': record['scientific_name'], 'audio_file': record['audio_file'],
            'image_file': None, 'sha256': digest, 'sound_type': record['sound_type'],
            'label_source': 'perch_max_logit', 'note': CUT_NOTE,
            'credit': {'recordist': record['recordist'], 'source_url': record['source_url'],
                       'license': record['license_url']},
            'xc_id': record['xc_id'], 'country': record['country'], 'quality': record['quality'],
            'type': record['type'], 'source_start_s': record['start_s'],
            'source_end_s': record['end_s'], 'perch_score': record['perch_score']})
    return {'version': 2, 'source': 'xeno-canto', 'purpose': 'practice', 'items': items}


def manifest(args):
    selection = json.loads(args.selection.read_text(encoding='utf-8'))
    output = build_manifest(selection)
    write_json(args.manifest, output)
    print(f"Built manifest v2 with {len(output['items'])} clips; {len(selection.get('missing', []))} missing: {args.manifest}")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='step', required=True)
    choose = commands.add_parser('select', help='Search, score and cut recordings (network required).')
    choose.add_argument('--key', type=Path, required=True, help='File containing the Xeno-Canto API key.')
    choose.add_argument('--models', type=Path, required=True, help='Directory containing Perch model and labels.')
    choose.add_argument('--manifest', type=Path, default=MANIFEST)
    choose.add_argument('--selection', type=Path, default=SELECTION)
    choose.add_argument('--ffmpeg', default='ffmpeg')
    choose.set_defaults(run=select)
    build = commands.add_parser('manifest', help='Build manifest v2 from downloaded selections (offline).')
    build.add_argument('--selection', type=Path, default=SELECTION)
    build.add_argument('--manifest', type=Path, default=MANIFEST)
    build.set_defaults(run=manifest)
    return result


def main():
    args = parser().parse_args()
    args.run(args)


if __name__ == '__main__':
    main()
