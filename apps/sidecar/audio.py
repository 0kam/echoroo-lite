"""Bounded audio reads and display spectrograms for recordings and reference clips."""
import base64
import json
import math
from pathlib import Path
from typing import Literal

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


def _mel_power(power, frequencies, rows=256):
    """Map linear FFT power to fixed HTK-mel rows, low to high."""
    to_mel = lambda hz: 2595 * np.log10(1 + hz / 700)
    to_hz = lambda mel: 700 * np.expm1(mel * np.log(10) / 2595)
    edges = to_hz(np.linspace(0, to_mel(frequencies[-1]), rows + 1))
    centers = to_hz((to_mel(edges[:-1]) + to_mel(edges[1:])) / 2)
    result = np.empty((rows, power.shape[1]), dtype=power.dtype)
    bin_width = frequencies[1] - frequencies[0]
    for index, (low, high, center) in enumerate(zip(edges[:-1], edges[1:], centers)):
        first = np.searchsorted(frequencies, low, side="left")
        last = np.searchsorted(frequencies, high, side="right" if index == rows - 1 else "left")
        if high - low >= bin_width and last > first:
            result[index] = power[first:last].max(axis=0)
        else:
            upper = min(np.searchsorted(frequencies, center), len(frequencies) - 1)
            lower = max(0, upper - 1)
            weight = 0 if upper == lower else (center - frequencies[lower]) / (frequencies[upper] - frequencies[lower])
            result[index] = power[lower] * (1 - weight) + power[upper] * weight
    return result


def _frequency_ticks(maximum, scale):
    values = [value for value in (0, 1000, 2000, 4000, 8000, 12000) if value <= maximum]
    if values[-1] != maximum:
        values.append(maximum)
    if scale == "mel":
        denominator = 2595 * math.log10(1 + maximum / 700)
        fraction = lambda hz: 2595 * math.log10(1 + hz / 700) / denominator
    else:
        fraction = lambda hz: hz / maximum
    return [{"frequency_hz": float(value), "row_fraction": fraction(value)} for value in values]


def spectrogram(row, start, end, *, max_columns=1000, scale: Literal["mel", "linear"] = "mel"):
    if scale not in ("mel", "linear"):
        raise ValueError("invalid frequency scale")
    # Display at the native rate: no resampling or scientific-library cold import.
    samples, interval = read_interval(row, start, end, native_rate=True)
    rate = interval['sample_rate']
    size, hop = max(4, round(rate * .016)), max(1, round(rate * .002))
    fft_size = size * 2
    window = np.hanning(size + 1)[:-1].astype(np.float32)
    frequencies = np.fft.rfftfreq(fft_size, 1 / rate)
    included = frequencies <= 12000
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(samples, (size // 2, size)), size)[::hop]
    frames = frames[:math.ceil((end - start) * rate / hop)]
    # Bound FFT working memory even for high-rate field recordings.
    power = np.concatenate([np.abs(np.fft.rfft(chunk * window, n=fft_size, axis=1)[:, included] / window.sum()) ** 2
                            for chunk in np.array_split(frames, max(1, math.ceil(len(frames) / 256)))], axis=0).T
    # Pool into evenly spaced display columns; do not decode an entire field recording.
    width = min(max_columns, power.shape[1])
    edges = np.linspace(0, power.shape[1], width + 1).astype(int)
    power = np.maximum.reduceat(power, edges[:-1], axis=1)
    linear_frequencies = frequencies[included]
    # 256 rows retain useful detail for the reduced-height UI while cutting the
    # common 385-row payload by one third. Narrow low-frequency rows interpolate.
    if scale == "mel":
        power = _mel_power(power, linear_frequencies)
    db = 10 * np.log10(np.maximum(power, 1e-12))
    ceiling = max(-80.0, float(db.max()))
    pixels = (np.clip((db - (ceiling - 70)) / 70, 0, 1) * 255).astype(np.uint8)[::-1]
    maximum = float(linear_frequencies[-1])
    result = {**interval, "width": width, "height": len(power),
            "window": "hann", "window_samples": size, "hop_samples": hop, "fft_samples": fft_size,
            "frequency_scale": scale, "frequency_min_hz": 0.0, "frequency_max_hz": maximum,
            "frequency_ticks": _frequency_ticks(maximum, scale),
            "db_min": ceiling - 70, "db_max": ceiling,
            "pixels": base64.b64encode(pixels.tobytes()).decode("ascii")}
    if scale == "mel":
        result["mel_formula"] = "HTK: 2595 * log10(1 + f / 700)"
    return result
