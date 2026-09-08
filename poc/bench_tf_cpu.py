"""Benchmark Perch V2 TF SavedModel on CPU + confirm XlaCallModule opacity.

Measures serving_default throughput (seg/s) at a representative batch and
extrapolates to a 30-min recording (360 x 5s segments). Runtime baseline used
to judge whether CPU inference is viable at all, independent of ONNX.

Run inside echoroo-backend container venv.
"""

from __future__ import annotations

import os
import time

# Pin CPU threading before importing TF.
THREADS = int(os.environ.get("PERCH_THREADS", "4"))
os.environ.setdefault("OMP_NUM_THREADS", str(THREADS))

import numpy as np  # noqa: E402
import tensorflow as tf  # noqa: E402

tf.config.threading.set_intra_op_parallelism_threads(THREADS)
tf.config.threading.set_inter_op_parallelism_threads(1)

MP = (
    "/home/appuser/.cache/kagglehub/models/google/"
    "bird-vocalization-classifier/tensorFlow2/perch_v2_cpu/1"
)
SEG = 160000

model = tf.saved_model.load(MP)
fn = model.signatures["serving_default"]

rng = np.random.default_rng(0)


def run(batch: int) -> np.ndarray:
    x = rng.standard_normal((batch, SEG)).astype(np.float32)
    out = fn(inputs=tf.constant(x))
    return out["embedding"].numpy()


# Warmup (triggers XLA compile for this batch size).
BATCH = int(os.environ.get("PERCH_BATCH", "12"))
_ = run(BATCH)
_ = run(BATCH)

# Timed: several batches.
N_BATCHES = 5
t0 = time.perf_counter()
total_segs = 0
for _ in range(N_BATCHES):
    emb = run(BATCH)
    total_segs += emb.shape[0]
dt = time.perf_counter() - t0

seg_per_s = total_segs / dt
print(f"threads={THREADS} batch={BATCH}")
print(f"embedding_shape={emb.shape}")
print(f"segments={total_segs} wall={dt:.3f}s seg_per_s={seg_per_s:.2f}")
print(f"extrapolate_30min_360seg_wall_s={360.0 / seg_per_s:.1f}")
