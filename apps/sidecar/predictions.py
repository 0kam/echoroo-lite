"""Project complete saved Perch logits into display scores and Japanese-bird candidates."""
import numpy as np
from scipy.special import softmax

from storage import CatalogError


def perch_candidates(path, seconds, analysis_id, candidate_filter):
    with np.load(path, allow_pickle=False) as data:
        logits, labels = data["logits"], data["labels"]
    if (logits.ndim != 2 or labels.ndim != 1 or logits.shape[1] != len(labels)
            or not len(labels) or not np.isfinite(logits).all()):
        raise CatalogError("invalid_scores")
    # Perch V2 was trained with a softmax head. Normalize over ALL classes before filtering.
    # Source: https://www.kaggle.com/models/google/bird-vocalization-classifier
    scores = softmax(logits.astype(np.float64), axis=1)
    indices = np.arange(len(labels))
    allowed = set((candidate_filter or {}).get("species", []))
    indices = indices[np.isin(labels, list(allowed))]
    selected = np.argsort(-scores[:, indices], axis=1, kind="stable")[:, :5]
    candidates = []
    for window, row in enumerate(selected):
        for index in indices[row]:
            candidates.append({"analysis_id": analysis_id,
                "start_s": window * 5, "end_s": min((window + 1) * 5, seconds),
                "label": str(labels[index]), "score": float(scores[window, index]),
                "raw_score": float(logits[window, index])})
    candidates.sort(key=lambda row: -row["score"])
    return candidates[:100], {"display_score_type": "softmax_uncalibrated",
                              "display_candidate_filter": "applied" if candidate_filter else "unavailable",
                              "display_species_count": len(indices), "normalization_classes": len(labels)}
