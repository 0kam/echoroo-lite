"""Verify prebuilt Perch V2 ONNX (justinchuby/Perch-onnx) vs TF SavedModel.

Numerical parity (embedding cosine + max abs diff) and CPU throughput for both
`perch_v2.onnx` and `perch_v2_no_dft.onnx`, against the current serving_default
SavedModel. Run inside echoroo-backend container venv. Does NOT modify echoroo.

Usage:
  PERCH_THREADS=8 uv run --python /opt/venv/bin/python python onnx_parity_bench.py
"""

from __future__ import annotations

import os
import time

THREADS = int(os.environ.get("PERCH_THREADS", "8"))
os.environ.setdefault("OMP_NUM_THREADS", str(THREADS))

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
import tensorflow as tf  # noqa: E402

tf.config.threading.set_intra_op_parallelism_threads(THREADS)
tf.config.threading.set_inter_op_parallelism_threads(1)

MP = (
    "/home/appuser/.cache/kagglehub/models/google/"
    "bird-vocalization-classifier/tensorFlow2/perch_v2_cpu/1"
)
ONNX_DIR = "/home/appuser/.cache/perch_onnx"
ONNX_MODELS = {
    "perch_v2.onnx": f"{ONNX_DIR}/perch_v2.onnx",
    "perch_v2_no_dft.onnx": f"{ONNX_DIR}/perch_v2_no_dft.onnx",
}
SEG = 160000
EMB = 1536


def make_inputs(n: int) -> np.ndarray:
    """Mix of white noise and structured chirps (32 kHz, 5 s)."""
    rng = np.random.default_rng(42)
    xs = []
    t = np.arange(SEG) / 32000.0
    for i in range(n):
        if i % 2 == 0:
            x = rng.standard_normal(SEG).astype(np.float32) * 0.1
        else:
            f0 = 800 + 300 * i
            sweep = np.sin(2 * np.pi * (f0 * t + 400 * t * t)).astype(np.float32)
            x = 0.5 * sweep + 0.05 * rng.standard_normal(SEG).astype(np.float32)
        xs.append(x)
    return np.stack(xs).astype(np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = a.ravel()
    b = b.ravel()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main() -> None:
    N = 6
    X = make_inputs(N)

    # --- TF reference ---
    tf_model = tf.saved_model.load(MP)
    fn = tf_model.signatures["serving_default"]
    tf_emb = fn(inputs=tf.constant(X))["embedding"].numpy()
    print(f"TF embedding shape={tf_emb.shape}")

    so = ort.SessionOptions()
    so.intra_op_num_threads = THREADS
    so.inter_op_num_threads = 1

    for name, path in ONNX_MODELS.items():
        print(f"\n===== {name} =====")
        sess = ort.InferenceSession(
            path, sess_options=so, providers=["CPUExecutionProvider"]
        )
        in_name = sess.get_inputs()[0].name
        out_names = [o.name for o in sess.get_outputs()]
        print(f"providers={sess.get_providers()}")
        print(f"input={in_name} {sess.get_inputs()[0].shape}")
        print(f"outputs={out_names}")

        # pick embedding output
        emb_out = "embedding" if "embedding" in out_names else out_names[0]

        onnx_emb = sess.run([emb_out], {in_name: X})[0]
        print(f"onnx embedding shape={onnx_emb.shape}")

        cos_list = [cosine(tf_emb[i], onnx_emb[i]) for i in range(N)]
        max_abs = float(np.max(np.abs(tf_emb - onnx_emb)))
        print(
            f"PARITY {name}: cosine_mean={np.mean(cos_list):.6f} "
            f"cosine_min={np.min(cos_list):.6f} max_abs_diff={max_abs:.6e}"
        )

        # --- speed: single-segment batch, several reps ---
        one = X[:1]
        for _ in range(3):
            sess.run([emb_out], {in_name: one})  # warmup
        REPS = 30
        t0 = time.perf_counter()
        for _ in range(REPS):
            sess.run([emb_out], {in_name: one})
        dt = time.perf_counter() - t0
        seg_s = REPS / dt
        print(
            f"SPEED {name}: threads={THREADS} seg_per_s={seg_s:.2f} "
            f"30min_360seg_wall_s={360.0 / seg_s:.1f}"
        )


if __name__ == "__main__":
    main()
