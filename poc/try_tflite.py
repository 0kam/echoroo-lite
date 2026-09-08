"""Attempt Perch V2 SavedModel -> TFLite (float32) as a packageable CPU path.

The SavedModel is a single XlaCallModule (native-serialized StableHLO). tf2onnx
cannot lower it; this probes whether the TFLite converter's StableHLO/Flex path
can, which would give a PyInstaller-friendly CPU runtime (LiteRT).
"""

from __future__ import annotations

import tensorflow as tf

MP = (
    "/home/appuser/.cache/kagglehub/models/google/"
    "bird-vocalization-classifier/tensorFlow2/perch_v2_cpu/1"
)
OUT = "/tmp/perch_v2.tflite"

conv = tf.lite.TFLiteConverter.from_saved_model(MP)
# Allow both builtin + TF (Flex) ops so XlaCallModule can fall back to Flex.
conv.target_spec.supported_ops = [
    tf.lite.OpsSet.TFLITE_BUILTINS,
    tf.lite.OpsSet.SELECT_TF_OPS,
]
tfl = conv.convert()
with open(OUT, "wb") as f:
    f.write(tfl)
print(f"TFLITE_OK bytes={len(tfl)}")
