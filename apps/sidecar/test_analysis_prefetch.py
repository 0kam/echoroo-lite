"""Byte identity, bounded read-ahead and cleanup of Kinglet audio preparation."""
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import analysis
from storage import CatalogError
from test_analysis_costs import WaveSession


SIZE = 32 * 160000


def record(path, rid=1):
    info, stat = sf.info(path), path.stat()
    return dict(id=rid, path=str(path), seconds=info.duration, sample_rate=info.samplerate,
                size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def fingerprint(array):
    return array.dtype.str, array.shape, hashlib.sha256(array.tobytes()).hexdigest()


class PrefetchTest(unittest.TestCase):
    def test_polyphase_chunks_match_whole_file_bytes_including_tails(self):
        rng = np.random.default_rng(20261026)
        with tempfile.TemporaryDirectory() as folder:
            for rate in (8000, 22050, 32000, 44100, 48000, 96000):
                with self.subTest(rate=rate):
                    path = Path(folder)/'record.wav'
                    sf.write(path, rng.normal(0, .1, rate*161+123).astype(np.float32), rate, subtype='PCM_16')
                    row = record(path)
                    native, _ = sf.read(path, dtype='float32')
                    expected = analysis.resample(native, rate)
                    with analysis.prefetch_audio([row]) as read:
                        audio = read(row)
                        actual = np.concatenate([audio[start:start+SIZE] for start in range(0, len(audio), SIZE)])
                    self.assertEqual(fingerprint(actual), fingerprint(expected))
                    # Exact whole-window endings must not introduce an extra padded window.
                    if rate == 48000:
                        sf.write(path, native[:rate*160], rate, subtype='PCM_16')
                        row = record(path)
                        with analysis.prefetch_audio([row]) as read:
                            audio = read(row)
                            self.assertEqual(len(audio), SIZE)
                            self.assertEqual(fingerprint(audio[0:SIZE]),
                                             fingerprint(analysis.resample(native[:rate*160], rate)))

    def test_inference_keeps_batch_boundaries_bytes_and_next_recording(self):
        rng = np.random.default_rng(123)
        with tempfile.TemporaryDirectory() as folder:
            rows = []
            for index, (rate, seconds) in enumerate(((44100, 320.125), (48000, 7.75))):
                path = Path(folder)/f'{index}.flac'
                sf.write(path, rng.normal(0, .1, round(rate*seconds)).astype(np.float32), rate, subtype='PCM_16')
                rows.append(record(path, index+1))
            for engine in ('kinglet-b', 'kinglet-s', 'kinglet-xs'):
                with self.subTest(engine=engine), analysis.prefetch_audio(rows) as read:
                    for row in rows:
                        native, rate = sf.read(row['path'], dtype='float32')
                        original_calls, streamed_calls = [], []
                        expected = analysis.infer_onnx(WaveSession(engine, original_calls), engine,
                                                      analysis.resample(native, rate), 317)
                        actual = analysis.infer_onnx(WaveSession(engine, streamed_calls), engine, read(row), 317)
                        for first, second in zip(expected, actual):
                            self.assertEqual(fingerprint(np.asarray(first)), fingerprint(np.asarray(second)))
                        self.assertEqual([fingerprint(c[2]) for c in original_calls],
                                         [fingerprint(c[2]) for c in streamed_calls])
                        self.assertEqual([len(c[2]) for c in streamed_calls], [32,32,1] if row['id']==1 else [2])

    def test_read_ahead_is_bounded_and_joins_on_early_exit_and_error(self):
        row = dict(id=1, path='unused', seconds=500, sample_rate=48000)
        prepared = [threading.Event(), threading.Event()]
        calls = []
        def convert(samples, rate):
            index = len(calls)
            calls.append(len(samples))
            prepared[index].set()
            return np.zeros((len(samples)*32000+rate-1)//rate, dtype=np.float32)
        def reading(path, start, stop, **kwargs):
            return np.zeros(stop-start, dtype=np.float32), 48000
        with patch('analysis.checked_source', return_value='unused'), patch('analysis.sf.read', side_effect=reading), \
                patch('analysis.resample', side_effect=convert):
            with analysis.prefetch_audio([row]) as read:
                audio = read(row)
                self.assertTrue(prepared[0].wait(2))
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(audio[0:SIZE]), SIZE)
                self.assertTrue(prepared[1].wait(2))
                self.assertEqual(len(calls), 2)
            self.assertFalse(any(t.name.startswith('analysis-audio') for t in threading.enumerate()))
        with patch('analysis.checked_source', return_value='unused'), \
                patch('analysis.sf.read', side_effect=RuntimeError('Decode failed')):
            with self.assertRaisesRegex(CatalogError, 'unreadable_audio'), analysis.prefetch_audio([row]) as read:
                read(row)[0:SIZE]
        self.assertFalse(any(t.name.startswith('analysis-audio') for t in threading.enumerate()))

    def test_changed_source_in_prefetch_keeps_catalog_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'record.wav'
            sf.write(path, np.zeros(160000), 32000)
            row = record(path)
            path.unlink()
            with self.assertRaises(CatalogError), analysis.prefetch_audio([row]) as read:
                read(row)[0:SIZE]

    def test_cancel_between_batches_stops_inference_and_joins_reader(self):
        cancel = threading.Event()
        class CancellingSession(WaveSession):
            def run(self, *args):
                result = super().run(*args)
                cancel.set()
                return result
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'record.wav'
            sf.write(path, np.zeros(32000*321, dtype=np.float32), 32000)
            row = record(path)
            calls = []
            with self.assertRaisesRegex(CatalogError, 'cancelled'), analysis.prefetch_audio([row]) as read:
                analysis.infer_onnx(CancellingSession('kinglet-s', calls), 'kinglet-s', read(row), 317, cancel)
            self.assertEqual(len(calls), 1)
            self.assertFalse(any(t.name.startswith('analysis-audio') for t in threading.enumerate()))
