"""Bounded audio reads and display spectrograms for recordings and reference clips."""
import base64
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf

from storage import CatalogError, checked_source, connect


def source(root, kind, source_id):
    table = {"recording": "recordings", "reference": "audio_references"}[kind]
    with connect(root) as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (source_id,)).fetchone()
        xc = db.execute("SELECT metadata FROM xc_recordings WHERE reference_id=?", (source_id,)).fetchone() if kind == "reference" else None
    if row is None:
        raise CatalogError("not_found")
    return {**dict(row), "kind": kind, "attribution": json.loads(xc[0]) if xc else None}


def add_reference(root, path):
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in {".wav", ".flac", ".mp3"}:
        raise CatalogError("reference_format")
    try:
        info, stat = sf.info(path), path.stat()
    except (RuntimeError, OSError):
        raise CatalogError("unreadable_audio") from None
    if info.frames <= 0 or info.channels not in (1, 2):
        raise CatalogError("reference_channels")
    with connect(root) as db:
        row = db.execute("SELECT * FROM audio_references WHERE path=?", (str(path),)).fetchone()
        if row:
            checked_source(row)
            return {**dict(row), "kind": "reference"}
        rid = db.execute("INSERT INTO audio_references(path,seconds,sample_rate,channels,size,mtime_ns) VALUES(?,?,?,?,?,?)",
                         (str(path), info.duration, info.samplerate, info.channels, stat.st_size, stat.st_mtime_ns)).lastrowid
    return source(root, "reference", rid)


def resample(audio, rate):
    if rate == 32000:
        return audio
    from scipy.signal import resample_poly
    gcd = math.gcd(rate, 32000)
    return resample_poly(audio, 32000 // gcd, rate // gcd)


def read_interval(row, start, end, maximum=60, *, native_rate=False):
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= row["seconds"]
            and end - start <= maximum + 1e-9):
        raise CatalogError("invalid_interval")
    path = checked_source(row)
    first, last = math.floor(start * row["sample_rate"]), math.ceil(end * row["sample_rate"])
    try:
        samples, rate = sf.read(path, start=first, stop=last, dtype="float32", always_2d=True)
    except (RuntimeError, OSError):
        raise CatalogError("unreadable_audio") from None
    if not len(samples) or not np.isfinite(samples).all():
        raise CatalogError("unreadable_audio")
    checked_source(row)
    mono = samples.mean(axis=1)
    return (mono if native_rate else resample(mono, rate)), {
        "requested_start_s": start, "requested_end_s": end,
        "start_s": first / rate, "end_s": (first + len(samples)) / rate,
        "source_sample_rate": rate, "source_channels": samples.shape[1], "sample_rate": rate if native_rate else 32000}


def spectrogram(row, start, end):
    # Display at the native rate: no resampling or scientific-library cold import.
    samples, interval = read_interval(row, start, end, native_rate=True)
    rate = interval['sample_rate']
    size, hop = max(4, round(rate * .032)), max(1, round(rate * .008))
    window = np.hanning(size + 1)[:-1].astype(np.float32)
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    included = frequencies <= 12000
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(samples, (size // 2, size)), size)[::hop]
    frames = frames[:math.ceil((end - start) * rate / hop)]
    # Bound FFT working memory even for high-rate field recordings.
    power = np.concatenate([np.abs(np.fft.rfft(chunk * window, axis=1)[:, included] / window.sum()) ** 2
                            for chunk in np.array_split(frames, max(1, math.ceil(len(frames) / 256)))], axis=0).T
    # Pool into evenly spaced display columns; do not decode an entire field recording.
    width = min(1000, power.shape[1])
    edges = np.linspace(0, power.shape[1], width + 1).astype(int)
    power = np.maximum.reduceat(power, edges[:-1], axis=1)
    db = 10 * np.log10(np.maximum(power, 1e-12))
    ceiling = max(-80.0, float(db.max()))
    pixels = (np.clip((db - (ceiling - 70)) / 70, 0, 1) * 255).astype(np.uint8)[::-1]
    return {**interval, "width": width, "height": len(frequencies[frequencies <= 12000]),
            "frequency_max_hz": float(frequencies[included][-1]), "db_min": ceiling - 70, "db_max": ceiling,
            "pixels": base64.b64encode(pixels.tobytes()).decode("ascii")}
