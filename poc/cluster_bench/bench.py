#!/usr/bin/env python3
"""One-shot Fuji clustering benchmark for the Echoroo Lite sound map."""

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_name, "8")

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SIDECAR = ROOT / "apps/sidecar"
MODEL_ROOT = ROOT / "poc/models"
MODEL = MODEL_ROOT / "perch/perch_v2_no_dft.onnx"
DEFAULT_DATA = Path(
    "/private/tmp/claude-501/-Users-okamoto-NIES-Ecoacoustics-echoroo-lite/"
    "a5cb5f10-c87c-4447-9add-4db46eedb5b7/scratchpad/fuji-data/Fuji"
)
DEFAULT_CACHE = ROOT / ".local-data/cluster-bench-20261101"
DEFAULT_RESULT = ROOT / "docs/benchmarks/cluster-bench-2026-11-01.json"
SEED = 20261101
WINDOW_SECONDS = 5
WINDOWS_PER_CLIP = 36
PCA_DIMS = 50
K_GRID = (10, 15, 20, 25, 30, 40, 50, 60)
SILHOUETTE_MAX = 4000
SCALE_N = 210_000
SCALE_NOISE_FACTOR = 0.1
STAGE_TIMEOUT_SECONDS = 45 * 60
PCA_BATCH = 4096


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def peak_rss_mib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)


def package_versions():
    names = ("numpy", "scipy", "sklearn", "openTSNE", "onnxruntime", "soundfile", "pandas", "pyarrow")
    versions = {}
    for name in names:
        try:
            module = __import__(name)
            versions[name] = getattr(module, "__version__", "unknown")
        except Exception as exc:
            versions[name] = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return versions


def require_packages(include_inference=False):
    required = ["numpy", "scipy", "sklearn", "openTSNE"]
    if include_inference:
        required += ["onnxruntime", "soundfile", "pandas", "pyarrow"]
    failures = {}
    for name in required:
        try:
            __import__(name)
        except Exception as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"
    if failures:
        raise RuntimeError("required packages unavailable: " + json.dumps(failures, sort_keys=True))


def iter_batches(matrix, batch_size=PCA_BATCH):
    for start in range(0, len(matrix), batch_size):
        yield np.asarray(matrix[start : start + batch_size], dtype=np.float64)


def exact_pca(matrix, dimensions=PCA_DIMS):
    """Compute exact covariance PCA in two streaming passes."""
    n, width = matrix.shape
    if n < 2 or dimensions > min(n, width):
        raise ValueError(f"invalid PCA shape {matrix.shape} for {dimensions} dimensions")
    total = np.zeros(width, dtype=np.float64)
    for batch in iter_batches(matrix):
        total += batch.sum(axis=0)
    mean = total / n
    covariance = np.zeros((width, width), dtype=np.float64)
    for batch in iter_batches(matrix):
        batch -= mean
        covariance += batch.T @ batch
    covariance /= n - 1
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1][:dimensions]
    components = eigenvectors[:, order]
    projected = np.lib.format.open_memmap(
        exact_pca.output_path, mode="w+", dtype=np.float32, shape=(n, dimensions)
    )
    start = 0
    for batch in iter_batches(matrix):
        end = start + len(batch)
        projected[start:end] = (batch - mean) @ components
        start = end
    projected.flush()
    return {
        "dimensions": dimensions,
        "explained_variance": eigenvalues[order].tolist(),
        "input_shape": [n, width],
    }


exact_pca.output_path = None


def kmeans_labels(matrix, k, seed):
    from scipy.cluster.vq import kmeans2

    centroids, labels = kmeans2(
        matrix,
        k,
        iter=100,
        minit="++",
        missing="raise",
        check_finite=True,
        seed=seed,
    )
    distances = matrix - centroids[labels]
    inertia = float(np.einsum("ij,ij->", distances, distances))
    return labels.astype(np.int32), inertia


def numpy_silhouette(matrix, labels, max_points=SILHOUETTE_MAX, seed=SEED):
    """Mean Euclidean silhouette with a deterministic maximum-size sample."""
    n = len(matrix)
    rng = np.random.default_rng(seed)
    indices = np.arange(n) if n <= max_points else np.sort(rng.choice(n, max_points, replace=False))
    sample = np.asarray(matrix[indices], dtype=np.float64)
    sample_labels = np.asarray(labels[indices])
    unique, counts = np.unique(sample_labels, return_counts=True)
    if len(unique) < 2:
        return -1.0, len(sample)
    norms = np.einsum("ij,ij->i", sample, sample)
    total = 0.0
    for start in range(0, len(sample), 256):
        block = sample[start : start + 256]
        squared = np.maximum(
            np.einsum("ij,ij->i", block, block)[:, None] + norms[None, :] - 2 * block @ sample.T,
            0,
        )
        distances = np.sqrt(squared)
        for offset, row_distances in enumerate(distances):
            own = sample_labels[start + offset]
            own_count = int(counts[np.flatnonzero(unique == own)[0]])
            if own_count == 1:
                continue
            own_mask = sample_labels == own
            a = float(row_distances[own_mask].sum() / (own_count - 1))
            b = min(float(row_distances[sample_labels == label].mean()) for label in unique if label != own)
            total += (b - a) / max(a, b) if max(a, b) else 0.0
    return total / len(sample), len(sample)


def candidate_a(matrix, k_grid=K_GRID):
    scores = []
    best = None
    for k in k_grid:
        if k >= len(matrix):
            continue
        labels, inertia = kmeans_labels(matrix, k, SEED + k)
        silhouette, sample_n = numpy_silhouette(matrix, labels)
        item = {"k": k, "silhouette": silhouette, "inertia": inertia, "silhouette_sample_n": sample_n}
        scores.append(item)
        if best is None or (silhouette, -k) > (best[0], -best[1]):
            best = (silhouette, k, labels)
    if best is None:
        raise ValueError("no valid k in grid")
    return best[2], {"selected_k": best[1], "grid": scores}


def candidate_b(matrix):
    from openTSNE import TSNE
    from sklearn.cluster import HDBSCAN

    tsne = TSNE(
        n_components=2,
        perplexity=30,
        metric="euclidean",
        initialization="pca",
        early_exaggeration_iter=250,
        n_iter=500,
        random_state=SEED,
        n_jobs=8,
        verbose=False,
    )
    coordinates = np.asarray(tsne.fit(matrix), dtype=np.float32)
    minimum = max(5, round(0.0025 * len(matrix)))
    labels = HDBSCAN(min_cluster_size=minimum, n_jobs=8).fit_predict(coordinates).astype(np.int32)
    return coordinates, labels, {"min_cluster_size": minimum}


def stage_main(args):
    matrix = np.load(args.input, mmap_mode="r")
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if args.stage == "pca":
        exact_pca.output_path = output / "pca50.npy"
        metadata = exact_pca(matrix)
    elif args.stage == "candidate-a":
        labels, metadata = candidate_a(matrix, tuple(args.k_grid))
        np.save(output / "labels.npy", labels)
    elif args.stage == "tsne":
        from openTSNE import TSNE

        coordinates = np.asarray(
            TSNE(
                n_components=2,
                perplexity=30,
                metric="euclidean",
                initialization="pca",
                early_exaggeration_iter=250,
                n_iter=500,
                random_state=SEED,
                n_jobs=8,
                verbose=False,
            ).fit(matrix),
            dtype=np.float32,
        )
        np.save(output / "coordinates.npy", coordinates)
        metadata = {"shape": list(coordinates.shape)}
    elif args.stage == "hdbscan":
        from sklearn.cluster import HDBSCAN

        minimum = max(5, round(0.0025 * len(matrix)))
        labels = HDBSCAN(min_cluster_size=minimum, n_jobs=8).fit_predict(matrix).astype(np.int32)
        np.save(output / "labels.npy", labels)
        metadata = {"min_cluster_size": minimum}
    else:
        raise ValueError(args.stage)
    save_json(output / "metadata.json", metadata)


def process_rss_mib(pid):
    result = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    try:
        return int(result.stdout.strip()) / 1024
    except ValueError:
        return 0.0


def run_stage(name, input_path, output_dir, timeout=STAGE_TIMEOUT_SECONDS, k_grid=K_GRID):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--stage",
        name,
        "--input",
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--k-grid",
        *map(str, k_grid),
    ]
    started = time.monotonic()
    process = subprocess.Popen(command)
    peak = 0.0
    while process.poll() is None:
        peak = max(peak, process_rss_mib(process.pid))
        if time.monotonic() - started > timeout:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return {
                "status": "aborted_timeout",
                "wall_seconds": time.monotonic() - started,
                "peak_rss_mib": peak,
                "timeout_seconds": timeout,
            }
        time.sleep(0.25)
    elapsed = time.monotonic() - started
    peak = max(peak, process_rss_mib(process.pid))
    result = {"wall_seconds": elapsed, "peak_rss_mib": peak, "exit_code": process.returncode}
    if process.returncode:
        result["status"] = "failed"
        return result
    result["status"] = "completed"
    result["metadata"] = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    return result


def pipeline(candidate, matrix_path, work_dir, k_grid=K_GRID):
    work_dir = Path(work_dir)
    stages = {}
    pca_dir = work_dir / "pca"
    stages["pca"] = run_stage("pca", matrix_path, pca_dir)
    if stages["pca"]["status"] != "completed":
        return {"status": "failed", "stages": stages}
    pca_path = pca_dir / "pca50.npy"
    if candidate == "A":
        cluster_dir = work_dir / "kmeans"
        stages["kmeans_and_silhouette"] = run_stage(
            "candidate-a", pca_path, cluster_dir, k_grid=k_grid
        )
        final = stages["kmeans_and_silhouette"]
        labels_path = cluster_dir / "labels.npy"
        coordinates_path = pca_path
    else:
        tsne_dir = work_dir / "tsne"
        stages["openTSNE"] = run_stage("tsne", pca_path, tsne_dir)
        if stages["openTSNE"]["status"] != "completed":
            return {"status": "failed", "stages": stages}
        coordinates_path = tsne_dir / "coordinates.npy"
        cluster_dir = work_dir / "hdbscan"
        stages["HDBSCAN"] = run_stage("hdbscan", coordinates_path, cluster_dir)
        final = stages["HDBSCAN"]
        labels_path = cluster_dir / "labels.npy"
    status = "completed" if final["status"] == "completed" else "failed"
    return {
        "status": status,
        "stages": stages,
        "runtime_seconds": sum(stage["wall_seconds"] for stage in stages.values()),
        "peak_rss_mib": max(stage["peak_rss_mib"] for stage in stages.values()),
        "labels_path": str(labels_path) if status == "completed" else None,
        "coordinates_path": str(coordinates_path) if status == "completed" else None,
    }


def source_identity(data_dir):
    wavs = sorted(Path(data_dir).glob("*.wav"))
    if len(wavs) != 60:
        raise ValueError(f"expected 60 WAV files, found {len(wavs)}")
    files = []
    for wav in wavs:
        annotation = Path(str(wav) + ".csv")
        if not annotation.is_file():
            raise FileNotFoundError(annotation)
        files += [wav, annotation]
    files += [MODEL, SIDECAR / "analysis.py", SIDECAR / "audio.py", SIDECAR / "models.py"]
    return {str(path): sha256(path) for path in files}


def annotation_windows(data_dir):
    rows = []
    for wav in sorted(Path(data_dir).glob("*.wav")):
        annotations = []
        with Path(str(wav) + ".csv").open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                if not row.get("stime") or not row.get("etime"):
                    continue
                start, end = float(row["stime"]), float(row["etime"])
                labels = {
                    value.strip().lower()
                    for value in (row.get("sp1", ""), row.get("sp2", ""))
                    if value and value.strip()
                }
                annotations.append((start, end, labels))
        for index in range(WINDOWS_PER_CLIP):
            start, end = index * WINDOW_SECONDS, (index + 1) * WINDOW_SECONDS
            labels, unknown = set(), False
            for annotation_start, annotation_end, annotation_labels in annotations:
                overlap = max(0.0, min(end, annotation_end) - max(start, annotation_start))
                contained = annotation_start >= start - 1e-9 and annotation_end <= end + 1e-9
                if overlap >= 0.5 - 1e-9 or contained:
                    unknown |= "unknown" in annotation_labels
                    labels.update(annotation_labels - {"unknown"})
            rows.append(
                {
                    "clip": wav.name,
                    "window_index": index,
                    "start_seconds": start,
                    "labels": json.dumps(sorted(labels), ensure_ascii=False),
                    "unknown": unknown,
                }
            )
    if len(rows) != 2160:
        raise AssertionError(len(rows))
    return rows


def prepare_fuji(data_dir, cache_dir, identity):
    import pandas as pd
    import soundfile as sf

    sys.path.insert(0, str(SIDECAR))
    import analysis

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "fuji-manifest.json"
    embeddings_path = cache_dir / "fuji-embeddings.npy"
    labels_path = cache_dir / "fuji-labels.parquet"
    expected = {
        "format": 1,
        "inputs": identity,
        "seed": SEED,
        "windows": {"seconds": WINDOW_SECONDS, "per_clip": WINDOWS_PER_CLIP},
        "inference": "analysis.load_onnx+analysis.infer_onnx+analysis.resample",
    }
    if manifest_path.is_file() and embeddings_path.is_file() and labels_path.is_file():
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        if saved.get("identity") == expected:
            embeddings = np.load(embeddings_path, mmap_mode="r")
            labels = pd.read_parquet(labels_path)
            if embeddings.shape == (2160, 1536) and len(labels) == 2160:
                print("Fuji cache verified and reused", flush=True)
                return embeddings_path, labels, {"status": "reused", **saved["generation"]}
    started = time.monotonic()
    detector, model_hash = analysis.load_onnx(MODEL_ROOT, "perch")
    if model_hash != identity[str(MODEL)]:
        raise AssertionError("model hash changed during load")
    model_labels = analysis.perch_labels(MODEL_ROOT)
    embeddings = np.lib.format.open_memmap(
        embeddings_path.with_suffix(".tmp.npy"), mode="w+", dtype=np.float32, shape=(2160, 1536)
    )
    offset = 0
    for number, wav in enumerate(sorted(Path(data_dir).glob("*.wav")), 1):
        native, rate = sf.read(wav, dtype="float32")
        if native.ndim != 1:
            raise ValueError(f"expected mono WAV: {wav}")
        converted = analysis.resample(native, rate)
        _, vectors = analysis.infer_onnx(detector, "perch", converted, len(model_labels))
        if vectors is None or len(vectors) < WINDOWS_PER_CLIP:
            raise ValueError(f"missing Perch windows: {wav}")
        embeddings[offset : offset + WINDOWS_PER_CLIP] = np.asarray(
            vectors[:WINDOWS_PER_CLIP], dtype=np.float32
        )
        offset += WINDOWS_PER_CLIP
        if number % 5 == 0:
            print(f"Perch embeddings: {number}/60 clips", flush=True)
    embeddings.flush()
    temporary_embeddings = embeddings_path.with_suffix(".tmp.npy")
    temporary_embeddings.replace(embeddings_path)
    labels = pd.DataFrame(annotation_windows(data_dir))
    temporary_labels = labels_path.with_suffix(".tmp.parquet")
    labels.to_parquet(temporary_labels, index=False)
    temporary_labels.replace(labels_path)
    generation = {
        "status": "generated",
        "wall_seconds": time.monotonic() - started,
        "peak_rss_mib": peak_rss_mib(),
    }
    save_json(manifest_path, {"identity": expected, "generation": generation})
    return embeddings_path, labels, generation


def prepare_scale(embeddings_path, cache_dir):
    cache_dir = Path(cache_dir)
    path = cache_dir / "scale-210000.npy"
    manifest_path = cache_dir / "scale-210000-manifest.json"
    base_hash = sha256(embeddings_path)
    identity = {
        "format": 1,
        "base_embeddings_sha256": base_hash,
        "n": SCALE_N,
        "noise_sigma": "0.1 * per-feature population std",
        "seed": SEED,
    }
    if path.is_file() and manifest_path.is_file():
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        if saved.get("identity") == identity:
            matrix = np.load(path, mmap_mode="r")
            if matrix.shape == (SCALE_N, 1536):
                return path, {"status": "reused", **saved["generation"]}
    started = time.monotonic()
    base = np.load(embeddings_path, mmap_mode="r")
    sigma = np.asarray(base).std(axis=0, dtype=np.float64) * SCALE_NOISE_FACTOR
    matrix = np.lib.format.open_memmap(
        path.with_suffix(".tmp.npy"), mode="w+", dtype=np.float32, shape=(SCALE_N, 1536)
    )
    rng = np.random.default_rng(SEED)
    for start in range(0, SCALE_N, 2048):
        end = min(start + 2048, SCALE_N)
        source = np.asarray(base[np.arange(start, end) % len(base)], dtype=np.float32)
        noise = rng.normal(0, sigma, size=source.shape).astype(np.float32)
        matrix[start:end] = source + noise
        if start and start % 20_480 == 0:
            print(f"Scale matrix: {start}/{SCALE_N}", flush=True)
    matrix.flush()
    temporary = path.with_suffix(".tmp.npy")
    temporary.replace(path)
    generation = {"wall_seconds": time.monotonic() - started, "peak_rss_mib": peak_rss_mib()}
    save_json(manifest_path, {"identity": identity, "generation": generation})
    return path, {"status": "generated", **generation}


def decoded_labels(frame):
    return [set(json.loads(value)) for value in frame["labels"]]


def ordered_clusters(labels, candidate):
    counts = Counter(map(int, labels))
    regular = sorted((label for label in counts if label != -1), key=lambda label: (-counts[label], label))
    if candidate == "B" and -1 in counts:
        regular.append(-1)
    return regular, counts


def cluster_metrics(candidate, labels, truth_frame):
    from sklearn.metrics import homogeneity_score, normalized_mutual_info_score

    truth = decoded_labels(truth_frame)
    unknown = truth_frame["unknown"].to_numpy(dtype=bool)
    single = np.array([len(item) == 1 for item in truth]) & ~unknown
    background = np.array([not item for item in truth]) & ~unknown
    labeled_truth = [next(iter(truth[index])) for index in np.flatnonzero(single)]
    combined_indices = np.flatnonzero(single | background)
    combined_truth = [
        next(iter(truth[index])) if truth[index] else "__background__" for index in combined_indices
    ]
    order, counts = ordered_clusters(labels, candidate)
    ranks = {cluster: rank for rank, cluster in enumerate(order, 1)}
    species_counts = Counter(name for item in truth for name in item)
    first_hits = {}
    for species in sorted(name for name, count in species_counts.items() if count >= 5):
        hit_clusters = {int(labels[index]) for index, item in enumerate(truth) if species in item}
        first_hits[species] = min(ranks[cluster] for cluster in hit_clusters)
    sizes = list(counts.values())
    composition = []
    classes = []
    for index, item in enumerate(truth):
        if unknown[index]:
            classes.append("__unknown__")
        elif len(item) > 1:
            classes.append("__multi_label__")
        elif item:
            classes.append(next(iter(item)))
        else:
            classes.append("__background__")
    for cluster in order[:10]:
        members = np.flatnonzero(labels == cluster)
        dominant, dominant_count = Counter(classes[index] for index in members).most_common(1)[0]
        composition.append(
            {
                "rank": ranks[cluster],
                "cluster": int(cluster),
                "size": len(members),
                "dominant_annotation_class": dominant,
                "dominant_fraction": dominant_count / len(members),
            }
        )
    non_noise = {value for value in counts if value != -1}
    return {
        "populations": {
            "single_species_not_unknown": int(single.sum()),
            "background_not_unknown": int(background.sum()),
            "multi_label_not_unknown": int(
                sum(len(item) > 1 and not unknown[index] for index, item in enumerate(truth))
            ),
            "multi_label_including_unknown": int(sum(len(item) > 1 for item in truth)),
            "unknown": int(unknown.sum()),
            "excluded_from_labeled_only": int((~single).sum()),
            "excluded_from_with_background": int((~(single | background)).sum()),
        },
        "labeled_only": {
            "n": int(single.sum()),
            "homogeneity": homogeneity_score(labeled_truth, labels[single]),
            "nmi": normalized_mutual_info_score(labeled_truth, labels[single]),
        },
        "with_background": {
            "n": len(combined_indices),
            "homogeneity": homogeneity_score(combined_truth, labels[combined_indices]),
            "nmi": normalized_mutual_info_score(combined_truth, labels[combined_indices]),
        },
        "exploration_efficiency": {
            "species_minimum_positive_windows": 5,
            "positive_population": "all clustered positive windows including multi-label and unknown",
            "positive_window_counts": dict(sorted(species_counts.items())),
            "first_hit_ranks": first_hits,
            "median_first_hit_rank": float(np.median(list(first_hits.values()))),
        },
        "clusters": {
            "count_including_noise_pseudo_cluster": len(counts),
            "count_excluding_noise": len(non_noise),
            "size_min": min(sizes),
            "size_median": float(np.median(sizes)),
            "size_max": max(sizes),
            "noise_count": counts.get(-1, 0),
            "noise_fraction": counts.get(-1, 0) / len(labels),
            "ordering": "size_descending_noise_last" if candidate == "B" else "size_descending",
            "largest_cluster_composition": composition,
        },
    }


def failed_result(error, versions, identity):
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return {
        "status": "failed_environment",
        "generated_at": now,
        "seed": SEED,
        "error": error,
        "environment": {
            "venv": str(ROOT / "poc/cluster_bench/.venv"),
            "requested_python": "3.11",
            "actual_python": sys.version,
            "installation_status": "failed_network_unavailable",
            "installation_error": (
                "PyPI DNS lookup failed; browser network access to pypi.org was also unavailable. "
                "No Python 3.11-compatible local scikit-learn or openTSNE installation was found."
            ),
            "diagnostic_package_source": (
                "Temporary read-only PYTHONPATH to the repository venv; not retained by the dedicated venv."
            ),
            "method_downgraded": False,
        },
        "versions": versions,
        "input_sha256s": identity,
        "parameters": benchmark_parameters(),
        "candidates": {
            "A": {"status": "not_run", "reason": error},
            "B": {"status": "not_run", "reason": error},
        },
        "scale_test": {
            "n": SCALE_N,
            "A": {"status": "not_run", "reason": error},
            "B": {"status": "not_run", "reason": error},
        },
        "sanity_checks": {"status": "not_run", "reason": error},
    }


def benchmark_parameters():
    return {
        "windows": {"seconds": WINDOW_SECONDS, "per_clip": WINDOWS_PER_CLIP, "count": 2160},
        "pca": {"method": "exact_covariance_eigh", "input_dimensions": 1536, "output_dimensions": 50},
        "A": {
            "coordinates": "PC1_PC2",
            "clustering": "scipy.cluster.vq.kmeans2",
            "initialization": "++",
            "iterations": 100,
            "k_grid": list(K_GRID),
            "selection": "numpy_mean_euclidean_silhouette",
            "silhouette_max_points": SILHOUETTE_MAX,
        },
        "B": {
            "projection": "openTSNE.TSNE",
            "perplexity": 30,
            "metric": "euclidean",
            "initialization": "pca",
            "early_exaggeration_iter": 250,
            "n_iter": 500,
            "n_jobs": 8,
            "clustering": "sklearn.cluster.HDBSCAN",
            "min_cluster_size": "max(5, round(0.0025*n))",
            "noise": "pseudo-cluster ordered last",
        },
        "scale": {
            "n": SCALE_N,
            "construction": "tile Fuji embeddings and add Gaussian noise",
            "noise_sigma": "0.1 * per-feature population std",
            "per_stage_timeout_seconds": STAGE_TIMEOUT_SECONDS,
        },
    }


def self_check():
    require_packages()
    rng = np.random.default_rng(SEED)
    centers = np.zeros((3, 1536), dtype=np.float32)
    centers[0, :20] = -12
    centers[1, :20] = 0
    centers[2, :20] = 12
    matrix = np.concatenate([rng.normal(center, 0.4, size=(150, 1536)) for center in centers]).astype(
        np.float32
    )
    expected = np.repeat(np.arange(3), 150)
    temporary = DEFAULT_CACHE / "self-check"
    temporary.mkdir(parents=True, exist_ok=True)
    input_path = temporary / "blobs.npy"
    np.save(input_path, matrix)
    results = {}
    for candidate in ("A", "B"):
        run = pipeline(candidate, input_path, temporary / candidate, k_grid=(3,))
        if run["status"] != "completed":
            raise RuntimeError(f"candidate {candidate} self-check failed: {run}")
        from sklearn.metrics import normalized_mutual_info_score

        labels = np.load(run["labels_path"])
        nmi = float(normalized_mutual_info_score(expected, labels))
        results[candidate] = {"nmi": nmi, "clusters": len(set(map(int, labels)))}
        if nmi < 0.95:
            raise AssertionError(f"candidate {candidate} self-check NMI {nmi:.6f} < 0.95")
    save_json(temporary / "result.json", {"status": "passed", "seed": SEED, "results": results})
    print(json.dumps(results, indent=2))


def sanity_check_result(result):
    errors = []
    for candidate in ("A", "B"):
        item = result["candidates"].get(candidate, {})
        if item.get("status") != "completed":
            errors.append(f"{candidate}: not completed")
            continue
        metrics = item["metrics"]
        cluster_count = metrics["clusters"]["count_including_noise_pseudo_cluster"]
        if cluster_count <= 1:
            errors.append(f"{candidate}: cluster count <= 1")
        for population in ("labeled_only", "with_background"):
            for metric in ("homogeneity", "nmi"):
                value = metrics[population][metric]
                if not 0 <= value <= 1:
                    errors.append(f"{candidate}: {population}.{metric}={value}")
        ranks = metrics["exploration_efficiency"]["first_hit_ranks"]
        if any(rank > cluster_count for rank in ranks.values()):
            errors.append(f"{candidate}: first-hit rank exceeds cluster count")
    return {"status": "passed" if not errors else "failed", "errors": errors}


def run_benchmark(args):
    versions = package_versions()
    identity = source_identity(args.data_dir)
    try:
        require_packages(include_inference=True)
    except RuntimeError as exc:
        result = failed_result(str(exc), versions, identity)
        save_json(args.result, result)
        raise
    cache = Path(args.cache_dir)
    embeddings_path, truth, generation = prepare_fuji(args.data_dir, cache, identity)
    result = {
        "status": "running",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seed": SEED,
        "platform": {"python": sys.version, "platform": platform.platform(), "machine": platform.machine()},
        "versions": versions,
        "input_sha256s": identity,
        "parameters": benchmark_parameters(),
        "embedding_cache": {**generation, "path": str(embeddings_path)},
        "candidates": {},
        "scale_test": {"n": SCALE_N},
    }
    save_json(args.result, result)
    for candidate in ("A", "B"):
        print(f"Fuji candidate {candidate}", flush=True)
        run = pipeline(candidate, embeddings_path, cache / "fuji-runs" / candidate)
        if run["status"] == "completed":
            labels = np.load(run["labels_path"])
            run["metrics"] = cluster_metrics(candidate, labels, truth)
        result["candidates"][candidate] = run
        save_json(args.result, result)
    scale_path, scale_generation = prepare_scale(embeddings_path, cache)
    result["scale_test"]["generation"] = scale_generation
    result["scale_test"]["input_path"] = str(scale_path)
    save_json(args.result, result)
    for candidate in ("A", "B"):
        print(f"Scale candidate {candidate}", flush=True)
        result["scale_test"][candidate] = pipeline(
            candidate, scale_path, cache / "scale-runs" / candidate
        )
        save_json(args.result, result)
    result["sanity_checks"] = sanity_check_result(result)
    result["status"] = "completed" if result["sanity_checks"]["status"] == "passed" else "failed"
    save_json(args.result, result)
    if result["status"] != "completed":
        raise RuntimeError(json.dumps(result["sanity_checks"]))
    print(f"Wrote {args.result}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--stage", choices=("pca", "candidate-a", "tsne", "hdbscan"), help=argparse.SUPPRESS)
    parser.add_argument("--input", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--k-grid", type=int, nargs="+", default=K_GRID, help=argparse.SUPPRESS)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.stage:
        stage_main(args)
    elif args.self_check:
        self_check()
    else:
        run_benchmark(args)


if __name__ == "__main__":
    main()
