"""Detection engines and the local, persistent main/comparison AI selection."""
import json
import os
import platform
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

from storage import CatalogError


V3_MODEL = 'BirdNET+_V3.0-preview3.1_Global_11K_FP16_pruned.onnx'
V3_LABELS = 'BirdNET+_V3.0-preview3.1_Global_11K_Labels.csv'
REGISTRY = {
    'perch': dict(name='Perch V2', files=('perch/perch_v2_no_dft.onnx', 'perch-labels/assets/labels.csv'),
                  window_s=5, score_type='softmax_uncalibrated', main_capable=True,
                  experimental=False, species_count=14795, license='Apache-2.0'),
    'birdnet': dict(name='BirdNET 2.4', files=('birdnet/acoustic-models/v2.4/tf/model-fp32.tflite',
                                            'birdnet/geo-models/v2.4/tf/model-fp32.tflite'),
                    window_s=3, score_type='sigmoid_uncalibrated', main_capable=False,
                    experimental=False, species_count=6522, license='CC-BY-NC-SA-4.0'),
    'birdnet-v3': dict(name='BirdNET+ V3.0 (preview)',
                       files=('birdnet-v3/' + V3_MODEL, 'birdnet-v3/' + V3_LABELS, 'birdnet-v3/TERMS_OF_USE.txt'),
                       window_s=5, score_type='probability_uncalibrated', main_capable=False,
                       experimental=True, species_count=11560, license='CC-BY-SA-4.0'),
    'kinglet-s': dict(name='Kinglet-S (preview)', files=('kinglet/kinglet-s-preview1.onnx',
                                                      'kinglet/kinglet-s-preview1.classes.csv'),
                     window_s=5, score_type='sigmoid_uncalibrated', main_capable=True,
                     experimental=True, species_count=317, license='Apache-2.0'),
    'kinglet-xs': dict(name='Kinglet-XS (preview)', files=('kinglet/kinglet-xs-preview1.onnx',
                                                        'kinglet/kinglet-xs-preview1.classes.csv'),
                      window_s=5, score_type='sigmoid_uncalibrated', main_capable=True,
                      experimental=True, species_count=317, license='Apache-2.0'),
}
Engine = Literal[tuple(REGISTRY)]
ValidationEngine = Literal[(*REGISTRY, 'custom')]
SpeciesEngine = Literal[(*REGISTRY, 'custom', 'auto')]


def available(directory, engine):
    return all((Path(directory) / name).is_file() for name in REGISTRY[engine]['files'])


def window_s(engine):
    return 5 if engine == 'custom' else REGISTRY[engine]['window_s']


def configure_birdnet(directory):
    # BirdNET resolves its model cache at import time, before benchmarks or jobs.
    os.environ['BIRDNET_APP_DATA'] = str(Path(directory).resolve() / 'birdnet')


def machine_fingerprint():
    return {'machine': platform.machine(), 'cpu_count': os.cpu_count()}


def settings(root):
    path = Path(root) / 'models.json'
    if not path.exists():
        return dict(main='perch', extra=[], confirmed=False, benchmark={})
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError()
        legacy = 'enabled' in data
        if legacy:
            enabled = data.pop('enabled')
            if not isinstance(enabled, list) or any(e not in REGISTRY for e in enabled):
                raise ValueError()
            data.update(main='perch', extra=[e for e in dict.fromkeys(enabled) if e != 'perch'], confirmed=True)
        data = dict(main='perch', extra=[], confirmed=False, benchmark={}) | data
        main, extra = data['main'], data['extra']
        if (not isinstance(main, str) or main not in REGISTRY or not REGISTRY[main]['main_capable']
                or not isinstance(extra, list) or any(not isinstance(e, str) or e not in REGISTRY or e == main for e in extra)
                or not isinstance(data['confirmed'], bool) or not isinstance(data['benchmark'], dict)):
            raise ValueError()
        if data['benchmark'] and ('minutes_per_hour' in data['benchmark'] or
                data['benchmark'].get('machine') != machine_fingerprint()):
            data['benchmark'] = {}
            legacy = True
        if not isinstance(data['benchmark'].get('seconds_per_hour', {}), dict):
            raise ValueError()
        if legacy:
            _write_settings(root, data)
        return data
    except (ValueError, TypeError):
        raise CatalogError('invalid_input') from None


def _write_settings(root, data):
    path = Path(root) / 'models.json'
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, allow_nan=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(root, directory):
    data = settings(root)
    directory = str(Path(directory).resolve())
    if data.get('directory') != directory:
        _write_settings(root, {**data, 'directory': directory})


def main_engine(root):
    return settings(root)['main']


@lru_cache(maxsize=8)
def _model_digest(path, size, modified):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def model_identity(root, engine):
    data = settings(root)
    path = Path(data.get('directory', root)) / REGISTRY[engine]['files'][0]
    if not path.is_file():
        return engine, None
    stat = path.stat()
    return engine, _model_digest(str(path), stat.st_size, stat.st_mtime_ns)


def main_identity(root):
    return model_identity(root, main_engine(root))


def feature_spec(engine):
    return f'{engine}_embedding_1536_l2_v1'


def require_selected(root, engine):
    if engine not in selected_engines(root):
        raise CatalogError('not_found')


def selected_engines(root):
    data = settings(root)
    return [engine for engine in REGISTRY if engine == data['main'] or engine in data['extra']]


def result_visible(root, result):
    engine = result.get('engine', 'perch')
    if engine == 'custom':
        return result.get('feature_engine', result.get('custom_model', {}).get('engine', 'perch')) == main_engine(root)
    return engine in selected_engines(root)


def list_models(root, directory=None):
    data = settings(root)
    directory = directory or data.get('directory', root)
    results = data['benchmark'].get('seconds_per_hour', {})
    result = []
    for engine, model in REGISTRY.items():
        seconds = results.get(engine)
        seconds = seconds if type(seconds) is int and seconds >= 0 else None
        result.append(dict(id=engine, **{key: model[key] for key in
            ('name', 'window_s', 'main_capable', 'experimental', 'species_count', 'license')},
            available=available(directory, engine), enabled=engine == data['main'] or engine in data['extra'],
            seconds_per_hour=seconds))
    return dict(main=data['main'], extra=data['extra'], confirmed=data['confirmed'], models=result)


def enabled_engines(root, directory=None):
    return [m['id'] for m in list_models(root, directory)['models'] if m['enabled'] and m['available']]


def set_selection(root, directory, main, extra):
    if (main not in REGISTRY or not REGISTRY[main]['main_capable'] or not available(directory, main)
            or any(e not in REGISTRY or e == main or not available(directory, e) for e in extra)):
        raise CatalogError('invalid_input')
    _write_settings(root, {**settings(root), 'directory': str(Path(directory).resolve()),
                           'main': main, 'extra': list(dict.fromkeys(extra)), 'confirmed': True})
    return list_models(root, directory)


def save_benchmark(root, results):
    from datetime import datetime, timezone
    _write_settings(root, {**settings(root), 'benchmark': {'machine': machine_fingerprint(),
        'timestamp': datetime.now(timezone.utc).isoformat(), 'seconds_per_hour': results}})
