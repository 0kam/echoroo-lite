"""Extra Fuji features for layout_proto.py: Perch class scores (Japan list) and Kinglet-B embeddings, same 2,160 windows.

Usage (repository root, main .venv, which has onnxruntime):
  .venv/bin/python poc/cluster_bench/fuji_extra.py <folder with the 60 Fuji WAV clips>
Writes .local-data/cluster-bench-20261101/fuji-extra.npz (not committed): perch_japan (softmax over all Perch classes,
then the Japan candidate list, as the recording page shows), kinglet_embedding (1536-d) and kinglet_scores.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps/sidecar'))
import analysis  # noqa: E402

MODELS = ROOT / 'poc/models'
CACHE = ROOT / '.local-data/cluster-bench-20261101'
WINDOWS = 36
data = Path(sys.argv[1])
clips = list(dict.fromkeys(pd.read_parquet(CACHE / 'fuji-labels.parquet')['clip']))
cached = np.load(CACHE / 'fuji-embeddings.npy')
perch, _ = analysis.load_onnx(MODELS, 'perch')
labels = analysis.perch_labels(MODELS)
japan = {name.partition('_')[0] for name in analysis.candidate_filter()['species']}
keep = np.array([i for i, name in enumerate(labels) if name in japan])
kinglet, _ = analysis.load_onnx(MODELS, 'kinglet-b')
count = {o.name: o.shape[1] for o in kinglet.get_outputs()}['logits'] - 1  # the last logit is the background class
perch_japan, kinglet_embedding, kinglet_scores = [], [], []
for number, clip in enumerate(clips):
    native, rate = sf.read(data / clip, dtype='float32')
    audio = analysis.resample(native, rate)
    scores, vectors = analysis.infer_onnx(perch, 'perch', audio, len(labels))
    assert np.abs(np.asarray(vectors[:WINDOWS]) - cached[number * WINDOWS:(number + 1) * WINDOWS]).max() < 1e-3, clip
    logits = np.asarray(scores[:WINDOWS], dtype=np.float64)
    probabilities = np.exp(logits - logits.max(axis=1, keepdims=True)); probabilities /= probabilities.sum(axis=1, keepdims=True)
    perch_japan.append(probabilities[:, keep])
    scores, vectors = analysis.infer_onnx(kinglet, 'kinglet-b', audio, count)
    kinglet_embedding.append(np.asarray(vectors[:WINDOWS])); kinglet_scores.append(np.asarray(scores[:WINDOWS]))
    if (number + 1) % 10 == 0:
        print(f'{number + 1}/{len(clips)} clips', flush=True)
np.savez(CACHE / 'fuji-extra.npz', perch_japan=np.vstack(perch_japan).astype(np.float32), japan_labels=np.array(labels)[keep],
         kinglet_embedding=np.vstack(kinglet_embedding).astype(np.float32), kinglet_scores=np.vstack(kinglet_scores).astype(np.float32))
print('written', CACHE / 'fuji-extra.npz')
