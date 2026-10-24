"""Small manifest-v2 fixture used while release audio is selected outside the sandbox."""
import hashlib
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import quiz

SPECIES = ['Horornis diphone', 'Parus minor', 'Phasianus versicolor', 'Picus awokera']


@contextmanager
def quiz_bundle(species=SPECIES):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        items = []
        for index, scientific_name in enumerate(species):
            relative = f'mp3/test-{index}.mp3'
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(path, np.linspace(-.1, .1, 32000), 32000, format='WAV')
            items.append({'key': f'xeno-canto-v1:test-{index}', 'scientific_name': scientific_name,
                'audio_file': relative, 'image_file': None,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'sound_type': 'song',
                'label_source': 'perch_max_logit', 'note': 'A section was cut.',
                'credit': {'recordist': 'Fixture', 'source_url': f'https://xeno-canto.org/{index + 1}',
                           'license': 'https://creativecommons.org/licenses/by/4.0/'}})
        (root / 'manifest.json').write_text(json.dumps(
            {'version': 2, 'source': 'xeno-canto', 'purpose': 'practice', 'items': items}), encoding='utf-8')
        with patch.object(quiz, 'DATA', root):
            quiz.manifest.cache_clear()
            try:
                yield root
            finally:
                quiz.manifest.cache_clear()
