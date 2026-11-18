"""Turn saved all-class scores into display candidates and family roll-ups (one flow for every AI, see taxa.py)."""
import numpy as np
from scipy.special import softmax

from models import default_threshold
from storage import CatalogError
from taxa import display


def _load(path, key):
    with np.load(path, allow_pickle=False) as data:
        values, labels = data[key], data["labels"]
    if (values.ndim != 2 or labels.ndim != 1 or values.shape[1] != len(labels)
            or not len(labels) or not np.isfinite(values).all()):
        raise CatalogError("invalid_scores")
    return values, labels


def perch_display(path, seconds, analysis_id, candidate_filter):
    """Candidates (top 5 of the allowed species per window), roll-ups, and display metadata of a Perch analysis."""
    logits, labels = _load(path, "logits")
    # Perch V2 was trained with a softmax head. Normalize over ALL classes before filtering.
    # Source: https://www.kaggle.com/models/google/bird-vocalization-classifier
    scores = softmax(logits.astype(np.float64), axis=1)
    allowed = np.isin(labels, list((candidate_filter or {}).get("species", [])))
    rows, rollups = display(scores, labels, allowed=allowed, score_type="softmax", seconds=seconds,
                            rollup_threshold=default_threshold("perch"))
    index_of = {str(label): index for index, label in enumerate(labels)}
    candidates = [{"analysis_id": analysis_id, "start_s": start, "end_s": end, "label": label, "score": score,
                   "raw_score": float(logits[int(start // 5), index_of[label]])} for start, end, label, score in rows]
    candidates.sort(key=lambda row: -row["score"])
    return candidates[:100], rollups, {"display_score_type": "softmax_uncalibrated",
        "display_candidate_filter": "applied" if candidate_filter else "unavailable",
        "display_species_count": int(allowed.sum()), "normalization_classes": len(labels)}


def perch_candidates(path, seconds, analysis_id, candidate_filter):
    candidates, _, meta = perch_display(path, seconds, analysis_id, candidate_filter)
    return candidates, meta


def dense_rollups(path, seconds, engine, window_s=5):
    """Family roll-ups of a dense-score analysis (BirdNET 3, Kinglet, all-class BirdNET 2.4)."""
    scores, labels = _load(path, "scores")
    score_type = "sigmoid" if engine in ("birdnet", "birdnet-v3") else "softmax"
    return display(scores, labels, score_type=score_type, top_k=0, seconds=seconds, window_s=window_s,
                   rollup_threshold=default_threshold(engine))[1]
