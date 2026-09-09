"""Check measured duration and explicit recorder timestamps before inference."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

from real_audio_bench import inventory


class InventoryCheck(unittest.TestCase):
    def test_audio_header_and_timestamp(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / '2MA04037_20240808_060000.wav'
            with wave.open(str(path), 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(bytes(48000))
            row = inventory([path], 'Asia/Tokyo')[0]
            self.assertEqual(row['seconds'], 1.0)
            self.assertEqual(row['datetime'], '2024-08-08T06:00:00+09:00')
            invalid = path.with_name('2MA04037_20240230_060000.wav')
            path.rename(invalid)
            with self.assertRaises(ValueError):
                inventory([invalid], 'Asia/Tokyo')


if __name__ == '__main__':
    unittest.main()
