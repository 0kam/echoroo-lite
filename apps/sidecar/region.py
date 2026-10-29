"""Optional BirdNET 2.4 location/season markers, independent of detection engines."""
import atexit
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path
from threading import Lock

from models import configure_birdnet, settings


_sessions = ExitStack()
_lock = Lock()
atexit.register(_sessions.close)


def model_key(root):
    directory = Path(settings(root).get('directory', root)).resolve()
    folder = directory / 'birdnet/geo-models/v2.4/tf'
    try:
        stat = (folder / 'model-fp32.tflite').stat()
    except OSError:
        return str(directory), None
    try:
        labels_changed = (folder / 'labels').stat().st_mtime_ns
    except OSError:
        labels_changed = None
    return str(directory), (stat.st_size, stat.st_mtime_ns, labels_changed)


def week_number(day):
    # birdnet 1.1.1 has no date helper; BirdNET uses four weeks per month (1-48).
    return (day.month - 1) * 4 + min((day.day - 1) // 7, 3) + 1


@lru_cache(maxsize=1)
def geo_session(key):
    configure_birdnet(key[0])
    import birdnet
    from birdnet.geo.models.v2_4.tf import GeoTFDownloaderV2_4
    if not GeoTFDownloaderV2_4._check_geo_model_available():
        raise FileNotFoundError('BirdNET geo model unavailable')
    geo = birdnet.load('geo', '2.4', 'tf', library='litert')
    # Keep the interpreter alive: geo.predict() creates a new one on every call.
    return _sessions.enter_context(geo.predict_session(min_confidence=0.03))


@lru_cache(maxsize=2048)
def predict_species(key, latitude, longitude, week):
    with _lock:
        prediction = geo_session(key).run(latitude, longitude, week=week)
    return frozenset(str(label).partition('_')[0] for label in
                     prediction.to_structured_array()['species_name'])
