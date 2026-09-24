"""Check native-rate display against SciPy and known physical frequencies."""
import base64
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import stft

from audio import spectrogram


class SpectrogramCheck(unittest.TestCase):
    def test_native_rate_display(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'tone.wav'
            for rate in (8000, 24000, 32000, 44100, 48000, 96000):
                samples = np.sin(2 * np.pi * 1000 * np.arange(rate) / rate).astype(np.float32) / 2
                sf.write(path, samples, rate, subtype='FLOAT')
                stat = path.stat()
                row = dict(path=str(path), seconds=1, sample_rate=rate, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
                result = spectrogram(row, 0, 1)
                pixels = np.frombuffer(base64.b64decode(result['pixels']), np.uint8).reshape(result['height'], result['width'])
                frequencies = np.linspace(result['frequency_max_hz'], 0, result['height'])
                self.assertAlmostEqual(frequencies[pixels.mean(axis=1).argmax()], 1000, delta=32)
                self.assertEqual(result['sample_rate'], rate)
                self.assertLessEqual(result['width'], 1000)
                self.assertAlmostEqual(result['frequency_max_hz'], min(12000, rate / 2), delta=32)
                # Check the selected 16 ms window against an independent SciPy STFT.
                if rate == 32000:
                    f, t, z = stft(samples, fs=rate, nperseg=512, noverlap=448, nfft=1024, boundary='zeros')
                    db = 10 * np.log10(np.maximum(np.abs(z[f <= 12000][:, t < 1]) ** 2, 1e-12))
                    expected = (np.clip((db - (float(db.max()) - 70)) / 70, 0, 1) * 255).astype(np.uint8)[::-1]
                    np.testing.assert_allclose(pixels, expected, atol=1)
                # Sub-window excerpts and silence still produce a finite, nonempty image.
                short = spectrogram(row, .1, .1001)
                self.assertGreater(short['width'], 0)
            sf.write(path, np.zeros(8000), 8000, subtype='FLOAT')
            stat = path.stat(); row.update(sample_rate=8000, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
            self.assertEqual(len(set(base64.b64decode(spectrogram(row, 0, 1)['pixels']))), 1)

    def test_zoom_preserves_native_time_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'calls.wav'
            rate = 24000
            samples = np.zeros(rate * 15, dtype=np.float32)
            samples[rate * 6:rate * 6 + 240] = np.sin(2 * np.pi * 2000 * np.arange(240) / rate)
            sf.write(path, samples, rate, subtype='FLOAT')
            stat = path.stat()
            row = dict(path=str(path), seconds=15, sample_rate=rate, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
            overview = spectrogram(row, 0, 15)
            detail = spectrogram(row, 0, 15, max_columns=8000)
            self.assertEqual(overview['width'], 1000)
            self.assertEqual(detail['width'], 7500)
            for image in (overview, detail):
                pixels = np.frombuffer(base64.b64decode(image['pixels']), np.uint8).reshape(image['height'], image['width'])
                call_time = pixels.max(axis=0).argmax() / image['width'] * 15
                self.assertAlmostEqual(call_time, 6, delta=.03)
                self.assertEqual(image['start_s'], 0)
                self.assertEqual(image['end_s'], 15)


if __name__ == '__main__':
    unittest.main()
