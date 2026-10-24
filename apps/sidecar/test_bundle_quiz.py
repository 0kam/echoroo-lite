"""Offline checks for Xeno-Canto selection and manifest generation."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf

SCRIPTS = Path(__file__).parents[2] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import bundle_quiz


class BundleQuizTest(unittest.TestCase):
    def test_license_filter(self):
        accepted = [
            'https://creativecommons.org/licenses/by/2.0/',
            '//creativecommons.org/licenses/by/4.0/deed.en',
            'http://creativecommons.org/licenses/by-sa/3.0/',
            'https://creativecommons.org/licenses/by-sa/4.0/',
            'https://creativecommons.org/publicdomain/zero/1.0/',
        ]
        rejected = [
            'https://creativecommons.org/licenses/by-nc/4.0/',
            'https://creativecommons.org/licenses/by-nd/4.0/',
            'https://creativecommons.org/licenses/by-nc-sa/4.0/',
            'https://creativecommons.org/licenses/by-nc-nd/4.0/',
        ]
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(bundle_quiz.accepted_license(url))
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(bundle_quiz.accepted_license(url))

    def test_search_order_fallback_and_sound_type_preference(self):
        calls = []
        free = 'https://creativecommons.org/licenses/by/4.0/'
        def fetch(country, quality):
            calls.append((country, quality))
            if (country, quality) == ('Japan', 'A'):
                return [dict(id=1, scientific_name='Bird bird', type='song', license=free.replace('/by/', '/by-nc/'))]
            if (country, quality) == ('Japan', 'B'):
                return []
            if (country, quality) == ('', 'A'):
                return [dict(id=2, scientific_name='Bird bird', type='call', license=free),
                        dict(id=3, scientific_name='Bird bird', type='song, male', license=free)]
            return [dict(id=4, scientific_name='Bird bird', type='song', license=free)]
        chosen = bundle_quiz.choose_recording('Bird bird', 'song', fetch)
        self.assertEqual(chosen['id'], 3)
        self.assertEqual(calls, [('Japan', 'A'), ('Japan', 'B'), ('', 'A')])

    def test_manifest_v2(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / 'mp3/bird.mp3'
            audio.parent.mkdir()
            audio.write_bytes(b'test audio')
            selection = {'items': [{'stem': 'bird', 'scientific_name': 'Bird bird',
                'sound_type': 'song', 'audio_file': 'mp3/bird.mp3', 'xc_id': 42,
                'recordist': 'A. Recordist', 'country': 'Japan', 'quality': 'A', 'type': 'song',
                'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
                'source_url': 'https://xeno-canto.org/42', 'start_s': 10.0,
                'end_s': 18.0, 'perch_score': 3.5}]}
            manifest = bundle_quiz.build_manifest(selection, root)
        item = manifest['items'][0]
        self.assertEqual((manifest['version'], manifest['source']), (2, 'xeno-canto'))
        self.assertEqual(item['key'], 'xeno-canto-v1:bird')
        self.assertIsNone(item['image_file'])
        self.assertEqual(item['sha256'], hashlib.sha256(b'test audio').hexdigest())
        self.assertEqual(item['credit'], {'recordist': 'A. Recordist',
            'source_url': 'https://xeno-canto.org/42',
            'license': 'https://creativecommons.org/licenses/by-sa/4.0/'})
        self.assertIn('section was cut', item['note'])
        self.assertEqual((item['source_start_s'], item['source_end_s'], item['perch_score']), (10.0, 18.0, 3.5))

    def test_best_perch_window_centers_eight_second_cut(self):
        class Session:
            def __init__(self):
                self.scores = iter((1.0, 3.0, 2.0))
            def run(self, _outputs, _inputs):
                return [np.array([[next(self.scores)]], dtype=np.float32)]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'source.wav'
            sf.write(path, np.linspace(-.1, .1, 12 * 32000), 32000)
            start, end, score = bundle_quiz.best_window(path, Session(), ['Bird bird'], 'Bird bird')
        self.assertEqual((start, end, score), (3.5, 11.5, 3.0))

    def test_ffmpeg_cut_is_mono_128k_mp3(self):
        commands = []
        def run(command, check):
            commands.append(command)
            Path(command[-1]).write_bytes(b'mp3')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('bundle_quiz.subprocess.run', side_effect=run), \
                    patch('bundle_quiz.sf.info', return_value=SimpleNamespace(frames=1)):
                bundle_quiz.cut_mp3('ffmpeg', root / 'source.audio', root / 'bird.mp3', 3.5, 11.5)
        command = commands[0]
        self.assertEqual(command[command.index('-ac') + 1], '1')
        self.assertEqual(command[command.index('-codec:a') + 1], 'libmp3lame')
        self.assertEqual(command[command.index('-b:a') + 1], '128k')


if __name__ == '__main__':
    unittest.main()
