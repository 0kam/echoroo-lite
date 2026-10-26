"""Detection engines and the local, persistent analysis selection."""
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from storage import CatalogError


REGISTRY = {
    'perch': dict(name='Perch V2', files=('perch/perch_v2_no_dft.onnx', 'perch-labels/assets/labels.csv'),
                  window_s=5, score_type='softmax_uncalibrated', search_embeddings=True,
                  required=True, experimental=False, species_count=14795, license='Apache-2.0'),
    'birdnet': dict(name='BirdNET 2.4', files=('birdnet/acoustic-models/v2.4/tf/model-fp32.tflite',
                                            'birdnet/geo-models/v2.4/tf/model-fp32.tflite'),
                    window_s=3, score_type='sigmoid_uncalibrated', search_embeddings=False,
                    required=False, experimental=False, species_count=6522, license='CC-BY-NC-SA-4.0'),
    'kinglet-s': dict(name='Kinglet-S (preview)', files=('kinglet/kinglet-s-preview1.onnx',
                                                      'kinglet/kinglet-s-preview1.classes.csv'),
                     window_s=5, score_type='sigmoid_uncalibrated', search_embeddings=False,
                     required=False, experimental=True, species_count=317, license='Apache-2.0'),
    'kinglet-xs': dict(name='Kinglet-XS (preview)', files=('kinglet/kinglet-xs-preview1.onnx',
                                                        'kinglet/kinglet-xs-preview1.classes.csv'),
                      window_s=5, score_type='sigmoid_uncalibrated', search_embeddings=False,
                      required=False, experimental=True, species_count=317, license='Apache-2.0'),
}
Engine = Literal[tuple(REGISTRY)]
ValidationEngine = Literal[(*REGISTRY, 'custom')]
SpeciesEngine = Literal[(*REGISTRY, 'custom', 'auto')]


def available(directory, engine):
    return all((Path(directory) / name).is_file() for name in REGISTRY[engine]['files'])


def window_s(engine):
    return 5 if engine == 'custom' else REGISTRY[engine]['window_s']


def settings(root):
    path = Path(root) / 'models.json'
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or ('enabled' in data and
                (not isinstance(data['enabled'], list) or any(e not in REGISTRY for e in data['enabled'])))):
            raise ValueError('Invalid model settings')
        return data
    except (ValueError, TypeError):
        raise CatalogError('invalid_input') from None


def _write_settings(root, data):
    path = Path(root) / 'models.json'
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(root, directory):
    # Refresh the installed path after an app move; no selection means all available engines.
    data = settings(root)
    directory = str(Path(directory).resolve())
    if data.get('directory') != directory:
        _write_settings(root, {**data, 'directory': directory})


def list_models(root, directory=None):
    data = settings(root)
    directory = directory or data.get('directory', root)
    result = []
    for engine, model in REGISTRY.items():
        exists = available(directory, engine)
        enabled = model['required'] or (engine in data['enabled'] if 'enabled' in data else exists)
        result.append(dict(id=engine, **{key: model[key] for key in
            ('name', 'window_s', 'required', 'experimental', 'species_count', 'license')},
            available=exists, enabled=enabled))
    return result


def enabled_engines(root, directory=None):
    return [m['id'] for m in list_models(root, directory) if m['enabled'] and m['available']]


def set_enabled(root, directory, enabled):
    if any(engine not in REGISTRY or not available(directory, engine) for engine in enabled):
        raise CatalogError('invalid_input')
    selected = [engine for engine, model in REGISTRY.items() if model['required'] or engine in enabled]
    _write_settings(root, {**settings(root), 'directory': str(Path(directory).resolve()), 'enabled': selected})
    return list_models(root, directory)
